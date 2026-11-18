"""Analyze complete reviewer cells; keep exploratory diagnostics separate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .evaluation import aggregate_labels
from .io import load_tasks, read_jsonl, write_json
from .scip_screening import validate_labels
from .statistics import paired_bootstrap


def provided_ids(row):
    ids = {b['id'] for b in row.get('contexts', [])}
    for b in row.get('contexts', []):
        if b.get('kind') == 'scip_bundle_manifest':
            ids.update(i for i in b.get('metadata', {}).get('selected_bundle_ids', []) if i+':' in b['content'])
    return ids


def unjudged(predictions, labels):
    def key(r):
        return r['task_id'], r['condition'], r.get('repeat', 0)
    if len({key(r) for r in predictions}) != len(predictions):
        raise ValueError('Duplicate predictions')
    judged = {key(r) for r in labels if r.get('status') == 'ok' and r.get('judge_status') in ('ok','skipped')}
    return [r for r in predictions if key(r) not in judged]


def transitions(labels, condition, baseline='diff'):
    base = {r['task_id']: set(r.get('semantic_anywhere_gold_ids', [])) for r in labels if r['condition'] == baseline}
    current = {r['task_id']: set(r.get('semantic_anywhere_gold_ids', [])) for r in labels if r['condition'] == condition}
    tids = sorted(set(base) & set(current))
    return {'paired_tasks': len(tids),
            'gained': [dict(task_id=t, gold_id=g) for t in tids for g in sorted(current[t]-base[t])],
            'lost': [dict(task_id=t, gold_id=g) for t in tids for g in sorted(base[t]-current[t])]}


def analyze(screening, diagnostics):
    screening, diagnostics = Path(screening), Path(diagnostics)
    requests = list(read_jsonl(screening/'requests.jsonl'))
    diagnostic_requests = list(read_jsonl(diagnostics/'requests.jsonl'))
    predictions = list(read_jsonl(screening/'predictions.jsonl')) + list(read_jsonl(diagnostics/'predictions.jsonl'))
    labels = list(read_jsonl(screening/'labels.jsonl'))
    diagnostic_labels = list(read_jsonl(diagnostics/'labels.jsonl'))
    validate_labels(requests, labels)
    validate_labels(diagnostic_requests, diagnostic_labels)
    if len(predictions) != len(requests) + len(diagnostic_requests):
        raise ValueError('Unexpected prediction count')
    if any(p.get('status') != 'ok' or p.get('tool_isolation', {}).get('tool_call_count') != 0 for p in predictions):
        raise ValueError('Reviewer error or missing tool-isolation audit')
    tasks = load_tasks(screening/'manifest.jsonl')
    all_labels = labels + diagnostic_labels
    all_requests = requests + diagnostic_requests
    metrics = aggregate_labels(tasks, labels)
    metrics.pop('selector_outcomes', None)
    write_json(screening/'analysis.json', metrics)
    for mode in ('semantic_anywhere', 'strict'):
        write_json(screening/f'bootstrap_{mode}.json', paired_bootstrap(
            tasks, labels, requests=requests, samples=5000, seed=20260903, match_mode=mode))
    comparisons = {
        'sources_only_vs_bundle': paired_bootstrap(tasks, all_labels, baseline='scip_bundles',
            samples=5000, seed=20260903, match_mode='semantic_anywhere')['analyses']['intention_to_treat']['scip_bundles_sources_only'],
        'provisional_vs_diff': paired_bootstrap(tasks, all_labels, baseline='diff',
            samples=5000, seed=20260903, match_mode='semantic_anywhere')['analyses']['intention_to_treat']['provisional_evidence'],
    }
    config = json.loads((diagnostics/'config.json').read_text())
    by_label = {(r['task_id'], r['condition']): r for r in all_labels}
    by_prediction = {(r['task_id'], r['condition']): r for r in predictions}
    by_request = {(r['task_id'], r['condition']): r for r in all_requests}
    case_rows = []
    conditions = sorted({r['condition'] for r in all_labels})
    for task in tasks:
        for gold in task.gold_issues:
            row = {'task_id': task.id, 'gold_id': gold.id, 'file': gold.file,
                   'gold_claim': gold.claim, 'context_level': gold.context_level, 'conditions': {}}
            for condition in conditions:
                key = (task.id, condition)
                label = by_label.get(key)
                if label is None:
                    continue
                matches = [m for m in label['semantic_matches'] if m['gold_id'] == gold.id]
                sources = provided_ids(by_request[key])
                issues = by_prediction[key]['response']['issues']
                row['conditions'][condition] = {
                    'detected': gold.id in label['semantic_anywhere_gold_ids'],
                    'strict_detected': gold.id in label['matched_gold_ids'],
                    'matched_issues': [issues[m['prediction_index']] for m in matches],
                    'cited_provided_context_ids': sorted({e for m in matches for e in issues[m['prediction_index']]['evidence_ids'] if e in sources}),
                }
            case_rows.append(row)
    target_pairs = {(tid,g) for tid,gs in config['provisional_target_gold_ids_for_evaluator_only'].items() for g in gs}
    provisional_cases = [r for r in case_rows if (r['task_id'],r['gold_id']) in target_pairs]
    report = {
        'diagnostic_comparisons': comparisons,
        'semantic_transitions': {c: transitions(all_labels,c) for c in conditions if c != 'diff'},
        'provisional_target_cases': provisional_cases,
        'provisional_target_detection': {c: sum(r['conditions'].get(c,{}).get('detected',False) for r in provisional_cases) for c in conditions},
        'provisional_target_count': len(provisional_cases),
        'reviewer_outputs': len(predictions),
        'reviewer_tool_calls': sum(p['tool_isolation']['tool_call_count'] for p in predictions),
        'judge_calls': sum(r['judge_status'] != 'skipped' for r in all_labels),
        'judge_errors': sum(r['judge_status'] == 'error' for r in all_labels),
        'limitations': ['Development cohort; one repeat', 'No independent Oracle',
                        'Unmatched predictions are not verified hallucinations',
                        'No exact-token causal control or paired-no-delta arm',
                        'Paired PR bootstrap CIs exploratory; multiplicity not adjusted'],
    }
    write_json(diagnostics/'analysis.json', report)
    write_json(diagnostics/'gold_cases.json', case_rows)
    evidence_audit = []
    for p in predictions:
        request = by_request[(p['task_id'], p['condition'])]
        ids = provided_ids(request)
        for i, issue in enumerate(p['response']['issues']):
            citations = issue['evidence_ids']
            evidence_audit.append(dict(task_id=p['task_id'],condition=p['condition'],prediction_index=i,
                provided_context_citations=[e for e in citations if e in ids],
                other_citations=[e for e in citations if e not in ids],
                note='Other citations may refer to the diff; not automatically invalid.'))
    write_json(diagnostics/'citation_audit.json', evidence_audit)
    paths = [screening/'requests.jsonl',screening/'predictions.jsonl',screening/'labels.jsonl',
             diagnostics/'requests.jsonl',diagnostics/'predictions.jsonl',diagnostics/'labels.jsonl',Path(__file__)]
    write_json(diagnostics/'analysis_provenance.json', {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    print(json.dumps({'reviewer_outputs':len(predictions),'judge_calls':report['judge_calls'],
                      'primary_semantic':metrics['semantic_anywhere_conditions'],
                      'diagnostics':comparisons,'target_detection':report['provisional_target_detection']}))


def analyze_aa(screening, aa):
    screening, aa = Path(screening), Path(aa)
    requests = [r for r in read_jsonl(screening/'requests.jsonl') if r['condition']=='diff']
    replay = list(read_jsonl(aa/'labels.jsonl'))
    validate_labels(requests,replay)
    predictions = list(read_jsonl(aa/'predictions.jsonl'))
    if (len(predictions)!=len(requests) or any(p.get('status')!='ok' or
            p.get('tool_isolation',{}).get('tool_call_count')!=0 for p in predictions)):
        raise ValueError('Incomplete or non-isolated A/A review')
    baseline = [r for r in read_jsonl(screening/'labels.jsonl') if r['condition']=='diff']
    role_labels = baseline + [r | {'condition':'diff_replay'} for r in replay]
    result = paired_bootstrap(load_tasks(screening/'manifest.jsonl'),role_labels,
                              samples=5000,seed=20260903,match_mode='semantic_anywhere')
    report = {'stage':'post_hoc_identical_input_control',
              'comparison':result['analyses']['intention_to_treat']['diff_replay'],
              'transitions':transitions(role_labels,'diff_replay'),
              'requests':len(requests),
              'judge_calls':sum(r['judge_status']!='skipped' for r in replay),
              'limitations':'One replay; not a stable variance estimate or replacement for repeating all arms',
              'inputs_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                  [screening/'requests.jsonl',screening/'labels.jsonl',aa/'predictions.jsonl',aa/'labels.jsonl']}}
    write_json(aa/'analysis.json',report)
    print(json.dumps(report))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screening', required=True)
    parser.add_argument('--diagnostics', required=True)
    parser.add_argument('--pending-output')
    parser.add_argument('--predictions', nargs='+')
    parser.add_argument('--labels', nargs='+', default=[])
    parser.add_argument('--aa')
    args = parser.parse_args()
    if args.aa:
        analyze_aa(args.screening,args.aa)
    elif args.pending_output:
        pending = unjudged([r for p in args.predictions for r in read_jsonl(p)],
                          [r for p in args.labels for r in read_jsonl(p)])
        with Path(args.pending_output).open('x') as f:
            for r in pending:
                f.write(json.dumps(r, ensure_ascii=False)+'\n')
        print(json.dumps({'pending':len(pending),'output':args.pending_output}))
    else:
        analyze(args.screening, args.diagnostics)


if __name__ == '__main__':
    main()
