"""Target-specific paired scores and hypothesis-to-verifier transition audit."""
import argparse
import json
from pathlib import Path

from .counterfactual import analyze, write_new


def key(row):
    return row['task_id'], row['condition'], row['repeat']


def summarize(finals, proposals, annotations, mapping, budget_audits):
    labels = {key(row): row for row in annotations}
    records = finals + proposals
    if len(labels) != len(annotations) or len({key(row) for row in records}) != len(records):
        raise ValueError('Duplicate annotation or prediction')
    if any(key(row) not in labels for row in records):
        raise ValueError('Missing annotation')
    families = sorted({v['family'] for v in mapping.values()})
    scores, transitions = {}, {}
    final_index = {key(row): row for row in finals}
    drops = {key(row): row['dropped_indices'] for row in budget_audits}
    for family in families:
        ids = {task_id: meta['variant'] for task_id, meta in mapping.items() if meta['family'] == family}
        subset = [p for p in finals if p['task_id'] in ids]
        matching = [dict(task_id=a['task_id'], condition=a['condition'], repeat=a['repeat'],
                         target_present=bool(a['target_indices'])) for a in annotations if a['task_id'] in ids]
        scores[family] = analyze(subset, matching, ids)['conditions']
        transitions[family] = {}
        for arm in ('generic_proposal', 'protocol_proposal'):
            items = [p for p in proposals if p['task_id'] in ids and p['condition'] == arm and p['status'] == 'ok']
            t = dict(unsafe_target_proposed=0, safe_target_proposed=0, unsafe_target_delivered=0,
                     unsafe_target_retained=0, unsafe_target_rejected_after_delivery=0,
                     unsafe_target_added_without_delivered_proposal=0)
            for p in items:
                indices = labels[key(p)]['target_indices']
                if ids[p['task_id']] == 'safe':
                    t['safe_target_proposed'] += bool(indices)
                    continue
                t['unsafe_target_proposed'] += bool(indices)
                final_key = p['task_id'], arm.replace('_proposal', '_two_stage'), p['repeat']
                delivered = any(i not in drops.get(final_key, []) for i in indices)
                t['unsafe_target_delivered'] += delivered
                final = final_index.get(final_key)
                if not final or final['status'] != 'ok':
                    continue
                hit = bool(labels[final_key]['target_indices'])
                t['unsafe_target_retained'] += delivered and hit
                t['unsafe_target_rejected_after_delivery'] += delivered and not hit
                t['unsafe_target_added_without_delivered_proposal'] += not delivered and hit
            transitions[family][arm] = t
    return {'defect_families': len(families), 'families': scores, 'proposal_transition': transitions,
            'scope': 'Exploratory target-specific scores; repeated calls and constructed contexts are not independent real PR samples.'}


def finalize(root, annotations_path):
    root = Path(root)
    def read(name):
        return [json.loads(line) for line in (root / name).read_text().splitlines()]
    finals, proposals, audits = [], [], []
    for repeat in (1, 2, 3):
        for arm in ('general', 'checklist'):
            finals.extend(read(f'{arm}_r{repeat}.jsonl'))
        finals.extend(read(f'verify_r{repeat}.jsonl'))
        for arm in ('generic_proposal', 'protocol_proposal'):
            proposals.extend(read(f'{arm}_r{repeat}.jsonl'))
        audits.extend(dict(a, repeat=repeat) for a in json.loads((root / f'hypothesis_audit_r{repeat}.json').read_text()))
    annotations = json.loads(Path(annotations_path).read_text())
    for p in finals + proposals:
        matches = next(a['target_indices'] for a in annotations if key(a) == key(p))
        items = (p.get('response') or {}).get('issues', (p.get('response') or {}).get('hypotheses', []))
        if any(not 0 <= i < len(items) for i in matches):
            raise ValueError('Target index outside response')
    mapping = json.loads((root / 'private_labels.json').read_text())
    summary = summarize(finals, proposals, annotations, mapping, audits)
    all_records = finals + proposals
    base_requests = read('base_requests.jsonl')
    allowed = {r['task_id']: {'DIFF'} | {b['id'] for b in r['contexts']} for r in base_requests}
    invalid = []
    for p in all_records:
        for i, item in enumerate((p.get('response') or {}).get('issues', (p.get('response') or {}).get('hypotheses', []))):
            bad = set(item['evidence_ids']) - allowed[p['task_id']]
            if bad:
                invalid.append({'task_id': p['task_id'], 'condition': p['condition'], 'repeat': p['repeat'],
                                'index': i, 'invalid_ids': sorted(bad)})
    summary.update(generation_calls=len(all_records), final_calls=len(finals), proposal_calls=len(proposals),
                   model_errors=sum(p['status'] != 'ok' for p in all_records),
                   tool_calls=sum(p.get('tool_isolation', {}).get('tool_call_count', 0) for p in all_records),
                   invalid_evidence_ids=invalid,
                   dropped_hypothesis_items=sum(len(a['dropped_indices']) for a in audits),
                   annotation_source='Session author reviewed all outputs; not independent human adjudication.')
    write_new(root / 'analysis.json', summary)
    write_new(root / 'final_predictions.jsonl', finals, jsonl=True)
    write_new(root / 'proposal_predictions.jsonl', proposals, jsonl=True)
    write_new(root / 'completion.json', {'complete': len(all_records) == 84 and summary['model_errors'] == 0,
                                        'generation_calls': len(all_records), 'new_natural_holdout': False})
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True); parser.add_argument('--annotations', required=True)
    args = parser.parse_args()
    print(json.dumps(finalize(args.root, args.annotations), indent=2))
