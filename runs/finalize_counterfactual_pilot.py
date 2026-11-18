"""Aggregate already completed runs using session-author-reviewed target labels.

This is not an independent human or LLM-judge annotation. All 60 responses were
read; only the two explicitly listed findings match the preregistered target.
"""
import json
import subprocess
from pathlib import Path

from ace_review.counterfactual import ARMS, analyze, sha, write_new


def finalize(name, positives):
    root = Path(__file__).resolve().parent / name
    predictions = []
    for repeat in (1, 2, 3):
        predictions.extend(json.loads(line) for line in (root / f'predictions_r{repeat}.jsonl').read_text().splitlines())
    mapping = json.loads((root / 'private_labels.json').read_text())['mapping']
    expected = {(task_id, arm, repeat) for task_id in mapping for arm in ARMS for repeat in (1, 2, 3)}
    actual = {(p['task_id'], p['condition'], p['repeat']) for p in predictions}
    assert len(predictions) == 30 and actual == expected
    requests = {(r['task_id'], r['condition']): r for r in
                [json.loads(line) for line in (root / 'requests.jsonl').read_text().splitlines()]}
    labels, issues, invalid_ids = [], [], []
    for p in predictions:
        assert p['status'] == 'ok'
        found = p['response']['issues']
        key = (mapping[p['task_id']], p['condition'], p['repeat'])
        matches = positives.get(key, [])
        assert all(0 <= index < len(found) for index in matches)
        labels.append({'task_id': p['task_id'], 'condition': p['condition'], 'repeat': p['repeat'],
                       'target_present': bool(matches), 'target_issue_indices': matches,
                       'annotation_source': 'session_author_review_not_independent_human_or_judge',
                       'rationale': ('Explicit JSON error pickle/MRO/reconstruction failure and companion reduce fix.'
                                     if matches else 'No pickle/exception-serialization regression finding; other findings are outside the predefined target.'),
                       'matched_claims': [found[i]['claim'] for i in matches]})
        allowed = {'DIFF'} | {b['id'] for b in requests[p['task_id'], p['condition']]['contexts']}
        for index, issue in enumerate(found):
            row = {'task_id': p['task_id'], 'variant': mapping[p['task_id']], 'condition': p['condition'],
                   'repeat': p['repeat'], 'issue_index': index, 'target_match': index in matches, **issue}
            issues.append(row)
            bad = set(issue['evidence_ids']) - allowed
            if bad:
                invalid_ids.append({'task_id': p['task_id'], 'condition': p['condition'], 'repeat': p['repeat'],
                                    'issue_index': index, 'invalid_ids': sorted(bad)})
    summary = analyze(predictions, labels, mapping)
    summary.update(model_calls=30, model_errors=sum(p['status'] != 'ok' for p in predictions),
                   tool_calls=sum(p['tool_isolation']['tool_call_count'] for p in predictions),
                   all_findings=len(issues), target_findings=sum(i['target_match'] for i in issues),
                   empty_reviews=sum(not p['response']['issues'] for p in predictions),
                   invalid_evidence_ids=invalid_ids, independent_judge_used=False)
    write_new(root / 'predictions.jsonl', predictions, jsonl=True)
    write_new(root / 'target_labels.jsonl', labels, jsonl=True)
    write_new(root / 'findings.json', issues)
    write_new(root / 'analysis.json', summary)
    write_new(root / 'completion.json', {'complete': True, 'independent_families': 1,
              'model': 'gpt-5.6-sol', 'codex_version': subprocess.check_output(['codex', '--version'], text=True).strip(),
              'runs': 3, 'requests_per_repeat': 10, 'execution_errors': summary['model_errors'],
              'input_sha256': {name: sha((root / name).read_text()) for name in
                              ('requests.jsonl', 'prompt.txt', 'schema.json', 'private_labels.json')},
              'source_sha256': {name: sha((root.parent.parent / name).read_text()) for name in
                               ('ace_review/counterfactual.py', 'ace_review/counterfactual_followup.py',
                                'ace_review/reviewer.py', 'ace_review/fixtures/requests_roundtrip.py')},
              'annotation_limit': 'Session author checked target matching; no independent human adjudication.'})
    return summary


if __name__ == '__main__':
    primary = finalize('counterfactual_requests_pilot1_v2', {})
    runtime = finalize('counterfactual_requests_runtime1', {
        ('unsafe', 'definition', 1): [0],
        ('unsafe', 'oracle', 1): [0],
    })
    print(json.dumps({'primary': primary, 'runtime_followup': runtime}, indent=2))
