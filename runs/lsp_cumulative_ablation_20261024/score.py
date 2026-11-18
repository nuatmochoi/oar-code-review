"""Five-arm finding F1; historical/reused observations stay explicitly labeled."""
import collections
import hashlib
import importlib.util
import json
import random
from pathlib import Path

R = Path(__file__).resolve().parent
REF = R.parent / 'lsp_agentic_preserved_recovery_20261023'
read = lambda p: json.loads(Path(p).read_text())
save = lambda p, v: Path(p).write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n')
spec = importlib.util.spec_from_file_location('ablation_runner_for_scoring', R / 'experiment.py')
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


def metric(tp, fp, fn):
    return {'TP': tp, 'FP': fp, 'FN': fn,
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'F1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0}


def summary(rows):
    keys = ['valid', 'reported_issues', 'safe_false_alarm_reviews', 'safe_false_alarm_issues',
            'unsafe_unmatched_issues', 'safe_grounded', 'raw_safe_no_issue', 'abstained']
    out = {k: sum(x[k] for x in rows) for k in keys}
    for name in ['grounded', 'local', 'status']:
        out[name + '_F1'] = metric(*(sum(x[name + '_' + k] for x in rows) for k in ['tp', 'fp', 'fn']))
    pairs = {(x['case_id'], x['repeat']) for x in rows}
    out['pair_success'] = sum(all(x['correct'] for x in rows if (x['case_id'], x['repeat']) == p) for p in pairs)
    out.update(trajectories=len(rows), unsafe_reviews=sum(x['label'] == 'unsafe' for x in rows),
               safe_reviews=sum(x['label'] == 'safe' for x in rows),
               fresh_calls_this_supplement=sum(x.get('fresh_calls_this_supplement', 0) for x in rows),
               logical_reused_stages=sum(x.get('logical_reused_stages', 0) for x in rows),
               mean_source_tokens=sum(x.get('source_tokens', 0) for x in rows) / len(rows) if rows else 0)
    out['unsafe_first_to_final'] = dict(collections.Counter(x['first_target_state'] + ' -> ' + x['final_target_state'] for x in rows if x['label'] == 'unsafe'))
    return out


def main():
    audit = read(R / 'audit_all.json')
    required = ['algorithm_hashes', 'freeze_hashes', 'workspace_hashes', 'packet_and_native_integrity',
                'shared_common_prompts', 'complete_trajectories', 'reuse_integrity', 'legacy_gate_regression']
    assert all(audit['checks'].get(k) is True for k in required), {k: audit['checks'].get(k) for k in required}
    annotations = {}
    for arm in e.ARMS:
        part = read(R / f'author_annotations_{arm}.json')
        assert not set(annotations) & set(part)
        annotations.update(part)
    assert len(annotations) == len(audit['rows']) == 216
    rows = []
    for x in audit['rows']:
        g = annotations[x['run_id']]
        for stage in [1, 2]:
            path = e.resolve_stage(x, stage)['raw_path']
            expected = g['raw_sha256'].get(str(stage))
            assert (hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None) == expected, x['run_id']
        rev = x['final']['review'] if x.get('final') else None
        n = len(rev['issues']) if rev else 0
        assert sorted(g['supported_issue_indices'] + g['unsupported_issue_indices']) == list(range(n)), x['run_id']
        assert set(g['supported_issue_indices']) <= set(g['local_supported_issue_indices']) <= set(range(n))
        valid = x['status'] == 'ok' and not x['errors']
        unsafe = x['label'] == 'unsafe'
        tp = int(valid and unsafe and bool(g['supported_issue_indices']))
        local = int(valid and unsafe and bool(g['local_supported_issue_indices']))
        stp, sfp = int(unsafe and n > 0), int(not unsafe and n > 0)
        safe = bool(valid and not unsafe and rev and rev['status'] == 'no_supported_issue' and g['safe_verdict_supported'])
        rows.append({k: x[k] for k in ['run_id', 'case_id', 'world', 'arm', 'repeat', 'label']} | {
            'valid': valid, 'grounded_tp': tp, 'grounded_fp': n - tp, 'grounded_fn': int(unsafe) - tp,
            'local_tp': local, 'local_fp': n - local, 'local_fn': int(unsafe) - local,
            'status_tp': stp, 'status_fp': sfp, 'status_fn': int(unsafe) - stp,
            'reported_issues': n, 'safe_false_alarm_reviews': sfp, 'safe_false_alarm_issues': n if not unsafe else 0,
            'unsafe_unmatched_issues': n - tp if unsafe else 0, 'safe_grounded': safe,
            'raw_safe_no_issue': bool(not unsafe and rev and rev['status'] == 'no_supported_issue'),
            'abstained': bool(rev and rev['status'] == 'insufficient_evidence'),
            'correct': safe or bool(tp and not g['unsupported_issue_indices'] and n == 1),
            'first_target_state': g['first_target_state'], 'final_target_state': g['final_target_state'],
            'source_tokens': x.get('source_tokens', 0),
            'fresh_calls_this_supplement': x.get('physical_calls', 0),
            'logical_reused_stages': sum(e.resolve_stage(x, s)['reused'] for s in [1, 2]),
            'cohort': 'supplement_with_exact_input_reuse' if x['arm'] == 'B2' else 'fresh_supplement'})
    historical = read(REF / 'scores.json')
    for old in historical['rows']:
        rows.append({**old, 'fresh_calls_this_supplement': 0, 'logical_reused_stages': 2, 'cohort': 'sealed_historical'})
    assert len(rows) == 360 and len({x['run_id'] for x in rows}) == 360
    arms = ['B0', 'B1', 'B2', 'F', 'G']
    cases = sorted({x['case_id'] for x in rows})
    totals = {a: summary([x for x in rows if x['arm'] == a]) for a in arms}
    per_case = {c: {a: summary([x for x in rows if x['case_id'] == c and x['arm'] == a]) for a in arms} for c in cases}
    for a in ['F', 'G']:
        for name in ['grounded_F1', 'local_F1', 'status_F1', 'safe_grounded', 'pair_success', 'abstained']:
            assert totals[a][name] == historical['summary'][a][name], ('historical score changed', a, name)
    rng = random.Random(2026102402)
    draws = [[rng.choice(cases) for _ in cases] for _ in range(10000)]
    contrasts = {}
    for left, right in zip(arms, arms[1:]):
        out = {}
        for name in ['grounded_F1', 'local_F1', 'status_F1']:
            samples = []
            for draw in draws:
                values = {a: metric(*(sum(per_case[c][a][name][k] for c in draw) for k in ['TP', 'FP', 'FN']))['F1'] for a in [left, right]}
                samples.append(values[right] - values[left])
            samples.sort()
            out[name] = {'delta_percentage_points': 100 * (totals[right][name]['F1'] - totals[left][name]['F1']),
                         'PR_cluster_percentile_95CI_pp': [100 * samples[249], 100 * samples[9749]], 'bootstrap_samples': 10000}
        out['interpretation'] = ('Only one safe world has changed input; unsafe outcomes shared. Preservation effect on unsafe detection is not identified.'
                                 if (left, right) == ('B2', 'F') else 'Matched settings on previously analyzed development cases; sequential cohort and nonblind grading limitations apply.')
        contrasts[f'{right}_minus_{left}'] = out
    save(R / 'author_annotations.json', annotations)
    result = {'summary': totals, 'per_case': per_case, 'contrasts': contrasts, 'rows': rows,
              'fresh_execution': read(R / 'run_summary.json'),
              'reference_scores_path': str(REF / 'scores.json'), 'reference_scores_sha256': e.sha(REF / 'scores.json'),
              'notes': ['Finding F1 uses precision and recall. Unsupported reports count as unmatched findings, not necessarily nonexistent bugs.',
                        'All72 logical reviews per arm remain in denominator. Reused responses do not create independent samples or fresh calls.',
                        'B0 is shared public contract/fixed input/DIFF without AST/LSP source. B1 is changed-file AST context.',
                        'B2 to F treats only safe N10_a. No unsafe preservation effect can be estimated.',
                        'Same12Python development PRs; nonblind AI causal grading; sequential historical F/G cohort; no holdout claim.']}
    save(R / 'scores.json', result)
    print(json.dumps({'summary': totals, 'contrasts': contrasts}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
