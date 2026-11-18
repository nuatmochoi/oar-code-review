"""Read-only checks of completed pilot records; write only aggregate audit.json."""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from ace_review.reviewer import _trace_audit


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_hashes(mapping, base):
    for name, expected in mapping.items():
        assert sha(base / name) == expected, name
    return len(mapping)


hash_checks = {}
hash_checks['transport_freeze'] = check_hashes(read(HERE / 'transport-v2-freeze.json'), ROOT)
hash_checks['original_environment_sources'] = check_hashes(read(HERE / 'environment.json')['source_sha256'], ROOT)
hash_checks['mcr_final_artifacts'] = check_hashes(read(HERE / 'mcr/final_audit.json')['files'], ROOT)
frozen = read(HERE / 'swing/freeze.json')
hash_checks['swing_frozen_inputs'] = check_hashes(
    {k: v for k, v in frozen.items() if k.endswith(('.json', '.md'))}, HERE / 'swing')
swing_summary = read(HERE / 'swing/summary.json')
hash_checks['swing_final_artifacts'] = check_hashes(swing_summary['artifact_sha256'], HERE / 'swing')
packets = {r['run_id']: r for r in read(HERE / 'swing/packets.json')['records']}
usage = Counter()
counts = {}
audited = []
for directory, expected in [('swing/model', 16), ('mcr/reviews', 27), ('mcr/judges', 9)]:
    records = []
    for path in sorted((HERE / directory).glob('*.json')):
        row = read(path)
        if not isinstance(row, dict) or 'status' not in row or 'transport' not in row:
            continue
        assert row['status'] == 'ok' and row['returncode'] == 0, path
        assert row['transport'] == 'isolated_review_v2', path
        assert row['model'] == 'gpt-5.6-sol', path
        trace = read(path.with_suffix('.trace.json'))
        audit = _trace_audit(trace['stdout'])
        assert audit['tool_call_count'] == 0 and not audit['tool_events'], path
        assert row['tool_audit'] == audit, path
        request = packets[row['run_id']] if directory == 'swing/model' else read(path.with_suffix('.prompt.json'))
        digest = hashlib.sha256(request['prompt'].encode()).hexdigest()
        assert digest == row['prompt_sha256'] == request['prompt_sha256'], path
        if directory == 'swing/model':
            assert row['source_tokens'] <= 800
        elif directory == 'mcr/reviews':
            assert row['budget']['source_tokens'] <= 5000
        recorded_usage = []
        for line in trace['stdout'].splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get('type') == 'turn.completed' and 'usage' in event:
                recorded_usage.append(event['usage'])
        assert recorded_usage == row['usage'] and len(recorded_usage) == 1, path
        for item in recorded_usage:
            usage.update(item)
        records.append(row)
        audited.append({'path': str(path.relative_to(ROOT)), 'sha256': sha(path), 'prompt_sha256': digest})
    assert len(records) == expected, (directory, len(records))
    counts[directory] = len(records)

assert sum(counts.values()) == 52
expected_usage = Counter(swing_summary['strict_model_usage'])
expected_usage.update(read(HERE / 'mcr/final_audit.json')['total_valid_usage'])
assert usage == expected_usage, (usage, expected_usage)
integrity = read(HERE / 'mcr/prompt_integrity.json')
assert integrity['all_exact_prompt_reconstructions'] and integrity['review_calls'] == 27
assert swing_summary['source_mutations'] == 0
assert read(HERE / 'swing/runtime_preflight.json')['all_runtime_snapshots_match_corrected_indices']
out = {
    'valid_calls': counts, 'total_valid_calls': 52, 'observed_tool_calls': 0,
    'all_recorded_prompts_match_hashes': True, 'all_recorded_usage_matches_raw_traces': True,
    'artifact_hash_checks': hash_checks, 'total_valid_usage': dict(usage),
    'usage_caveat': 'Cached input and reasoning output are subsets, not additional totals; excluded attempts and diagnostic calls are not included.',
    'excluded_study_attempts': {'swing': 6, 'mcr': 7, 'total': 13},
    'successful_model_diagnostic_calls_separate': 3,
    'unit_tests': {'passed': 13, 'command': 'tools/venv-parallel-review20260928/bin/python -m pytest -q tests/test_isolated_review.py tests/test_swing_pilot.py tests/test_mcr_pilot.py', 'basis': 'Prior completed verification in this task; not rerun by this read-only audit.'},
    'records': audited,
}
(HERE / 'audit.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k: v for k, v in out.items() if k != 'records'}, ensure_ascii=False, indent=2))
