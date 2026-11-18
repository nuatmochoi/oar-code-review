"""Reuse tested scoring with strict raw accounting and development-cohort scope."""
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ace_review.context_necessity import assert_frozen, digest
from ace_review.context_results import key, summarize
from ace_review.counterfactual import write_new

RUN = ROOT / 'runs/auto_context2'


def main():
    for root in (RUN, ROOT / 'runs/auto_context_setup2', ROOT / 'runs/auto_context_dependency_check1'):
        assert_frozen(root)
    records = [json.loads(l) for l in (RUN / 'predictions.jsonl').read_text().splitlines()]
    inputs = [json.loads(l) for l in (RUN / 'requests.jsonl').read_text().splitlines()]
    annotations = json.loads((RUN / 'annotations.json').read_text())
    labels = json.loads((RUN / 'private_labels.json').read_text())
    audits = [a for repeat in (1, 2, 3) for a in json.loads((RUN / f'hypothesis_audit_r{repeat}.json').read_text())]
    result = summarize(records, annotations, labels, inputs, audits)
    result['scope'] = ('Two previously observed development families, one natural production change with a constructed safe companion '
                       'and one synthetic migration. Repeats are not independent PR samples. No fresh holdout.')
    raw_paths = sorted((RUN / 'raw').glob('*.jsonl'))
    raw = [json.loads(p.read_text()) | {'stage': p.name.split('_')[0]} for p in raw_paths]
    validations = [json.loads(p.read_text()) for p in (RUN / 'validation').glob('*.json')]
    if len(raw) != 96 or len(validations) != 96 or not all(v['valid'] for v in validations):
        raise ValueError('Incomplete or invalid outputs')
    if {key(r): r for r in raw} != {key(r): r for r in records}:
        raise ValueError('Combined records differ from raw output')
    safe_alarms = {f: {arm: 0 for arm in ('diff', 'bm25', 'scip_head', 'scip_delta')} for f in result['families']}
    wrong, target_count = [], 0
    for a in annotations:
        if a['stage'] != 'verify':
            continue
        target_count += len(a['target_indices'])
        if a['wrong_mechanism_indices']:
            wrong.append(a)
        meta = labels[a['task_id']]
        if meta['variant'] == 'safe':
            safe_alarms[meta['family']][a['condition']] += bool(a['target_indices'] or a['wrong_mechanism_indices'])
    changed_sources, rehashed = [], 0
    for case in {v['source_run'] for v in labels.values()}:
        expected = json.loads((Path(case) / 'execution.json').read_text())['file_sha256']
        for cell, hashes in expected.items():
            for path, sha in hashes.items():
                source = Path(case) / 'snapshots' / cell / path
                rehashed += 1
                if digest(source) != sha:
                    changed_sources.append(str(source))
    if changed_sources:
        raise ValueError('Original source changed')
    auxiliary = dict(safe_target_path_false_alarm_runs=safe_alarms, wrong_mechanism_final_rows=wrong,
                     target_mechanism_final_items=target_count,
                     wrong_mechanism_final_items=sum(len(a['wrong_mechanism_indices']) for a in wrong),
                     other_final_items=result['final_issue_count'] - target_count - sum(len(a['wrong_mechanism_indices']) for a in wrong),
                     source_files_rehashed=rehashed, changed_source_files=changed_sources,
                     validated_model_outputs=len(validations),
                     model_call_seconds_sum=sum(r['duration_seconds'] for r in records),
                     model_call_seconds_median=statistics.median(r['duration_seconds'] for r in records),
                     scope='Target-path alarms only; not all-code precision. Hypotheses count as candidates, not final findings.')
    previous = ROOT / 'runs/context_necessity1'
    auxiliary['historical_reviewer_files_identical'] = {name: digest(previous / name) == digest(RUN / name)
        for name in ('proposal.txt', 'verify.txt', 'frozen_runner.py', 'frozen_protocol.py', 'frozen_reviewer.py')}
    write_new(RUN / 'analysis.json', result)
    write_new(RUN / 'diagnostics.json', auxiliary)
    write_new(RUN / 'final_integrity.json', dict(frozen_files_unchanged=True,
        raw_sha256={p.name: digest(p) for p in raw_paths}, annotations_sha256=digest(RUN / 'annotations.json'),
        predictions_sha256=digest(RUN / 'predictions.jsonl'), source_files_rehashed=rehashed,
        changed_sources=changed_sources, annotation_script_sha256=digest(ROOT / 'runs/auto_context_annotations.py'),
        evaluator_sha256=digest(ROOT / 'ace_review/context_results.py')))
    print(json.dumps(dict(scores=result, auxiliary=auxiliary), indent=2))


if __name__ == '__main__':
    main()
