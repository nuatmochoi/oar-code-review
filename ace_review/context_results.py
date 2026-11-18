"""Strict record accounting plus target-specific author-annotated paired scores."""
import argparse
import json
from pathlib import Path

from .context_necessity import assert_frozen, digest
from .counterfactual import analyze, write_new


def key(row):
    return row['task_id'], row['condition'], row['repeat'], row['stage']


def items(row):
    return row['response']['hypotheses' if row['stage'] == 'proposal' else 'issues']


def summarize(records, annotations, mapping, inputs, audits):
    predictions = {key(p): p for p in records}
    labels = {key(a): a for a in annotations}
    expected = {(r['task_id'], r['condition'], repeat, stage) for r in inputs
                for repeat in (1, 2, 3) for stage in ('proposal', 'verify')}
    if len(predictions) != len(records) or len(labels) != len(annotations):
        raise ValueError('Duplicate record or annotation')
    if set(predictions) != expected or set(labels) != expected:
        raise ValueError('Incomplete or extra records/annotations')
    if any(p['status'] != 'ok' for p in records):
        raise ValueError('Failed calls require an explicit failure analysis, not empty safety labels')
    for k, annotation in labels.items():
        indices = annotation['target_indices']
        if len(set(indices)) != len(indices) or any(type(i) is not int or not 0 <= i < len(items(predictions[k])) for i in indices):
            raise ValueError('Invalid target indices')
    allowed = {(r['task_id'], r['condition']): {'DIFF'} | {b['id'] for b in r['contexts']} for r in inputs}
    invalid, bad_keys = [], set()
    for p in records:
        for index, item in enumerate(items(p)):
            bad = set(item['evidence_ids']) - allowed[(p['task_id'], p['condition'])]
            if bad:
                invalid.append({k: p[k] for k in ('task_id', 'condition', 'repeat', 'stage')} |
                               {'index': index, 'invalid_ids': sorted(bad)})
                bad_keys.add((*key(p), index))
    drops = {(a['task_id'], a['condition'], a['repeat']): a['dropped_indices'] for a in audits}
    expected_drops = {k[:3] for k in expected}
    if len(drops) != len(audits) or set(drops) != expected_drops:
        raise ValueError('Missing or duplicate payload audit')
    families, transitions, citation_hits = {}, {}, {}
    finals = [p for p in records if p['stage'] == 'verify']
    for family in sorted({m['family'] for m in mapping.values()}):
        ids = {i: m['variant'] for i, m in mapping.items() if m['family'] == family}
        if sorted(ids.values()) != ['safe', 'unsafe']:
            raise ValueError('Expected exactly one safe/unsafe pair per family')
        subset = [p for p in finals if p['task_id'] in ids]
        matching = [a | {'target_present': bool(a['target_indices'])} for a in annotations
                    if a['task_id'] in ids and a['stage'] == 'verify']
        families[family] = analyze(subset, matching, ids)['conditions']
        transitions[family], citation_hits[family] = {}, {}
        for arm in sorted({p['condition'] for p in subset}):
            counter = dict(proposed=0, delivered=0, retained=0, omitted_after_delivery=0,
                           added_without_delivered_proposal=0, safe_target_proposed=0)
            citation_hits[family][arm] = 0
            for p in records:
                if p['task_id'] not in ids or p['condition'] != arm or p['stage'] != 'proposal':
                    continue
                indices = labels[key(p)]['target_indices']
                if ids[p['task_id']] == 'safe':
                    counter['safe_target_proposed'] += bool(indices)
                    continue
                counter['proposed'] += bool(indices)
                delivered = any(i not in drops[key(p)[:3]] for i in indices)
                counter['delivered'] += delivered
                final_key = (*key(p)[:3], 'verify')
                final_indices = labels[final_key]['target_indices']
                hit = bool(final_indices)
                counter['retained'] += delivered and hit
                counter['omitted_after_delivery'] += delivered and not hit
                counter['added_without_delivered_proposal'] += not delivered and hit
                citation_hits[family][arm] += any((*final_key, i) not in bad_keys for i in final_indices)
            transitions[family][arm] = counter
    return {'families': families, 'transitions': transitions, 'citation_valid_target_hits': citation_hits,
            'generation_calls': len(records), 'final_reviews': len(finals),
            'final_issue_count': sum(len(items(p)) for p in finals),
            'proposal_hypothesis_count': sum(len(items(p)) for p in records if p['stage'] == 'proposal'),
            'invalid_evidence_ids': invalid, 'dropped_hypothesis_items': sum(map(len, drops.values())),
            'tool_calls': sum(p.get('tool_isolation', {}).get('tool_call_count', 0) for p in records),
            'annotation_source': 'Session author reviewed all outputs; not independent adjudication.',
            'scope': 'Two selected new families, one natural production change with constructed safe companion and one synthetic migration. Repeats are not independent PR samples.'}


def finalize(root, annotations_path):
    root = Path(root)
    assert_frozen(root)
    records = [json.loads(line) for line in (root / 'predictions.jsonl').read_text().splitlines()]
    inputs = [json.loads(line) for line in (root / 'requests.jsonl').read_text().splitlines()]
    annotations = json.loads(Path(annotations_path).read_text())
    mapping = json.loads((root / 'private_labels.json').read_text())
    audits = [a for repeat in (1, 2, 3) for a in json.loads((root / f'hypothesis_audit_r{repeat}.json').read_text())]
    result = summarize(records, annotations, mapping, inputs, audits)
    write_new(root / 'analysis.json', result)
    write_new(root / 'final_integrity.json', {'frozen_files_unchanged': True,
        'raw_sha256': {p.name: digest(p) for p in sorted((root / 'raw').glob('*.jsonl'))},
        'annotations_sha256': digest(annotations_path), 'predictions_sha256': digest(root / 'predictions.jsonl')})
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--root', required=True); p.add_argument('--annotations', required=True)
    a = p.parse_args()
    print(json.dumps(finalize(a.root, a.annotations), indent=2))
