"""Freeze and execute fixed probes; no review-model calls."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ace_review.context_necessity import assert_frozen, digest, freeze  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402
from ace_review.runtime_probe import run_probe  # noqa: E402

RUN = ROOT / 'runs/runtime_probe1'
INDEX = ROOT / 'runs/auto_context_dependency_check1'
PYTHON = ROOT / 'tools/venv-context-runtime/bin/python'


def main():
    assert_frozen(INDEX)
    assert_frozen(ROOT / 'runs/answer_source1')
    RUN.mkdir(exist_ok=False)
    for name in ('ace_review/runtime_probe.py', 'tests/test_runtime_probe.py',
                 'runs/runtime_probe_experiment.py', 'runs/RUNTIME_PROBE_PLAN_KO.md'):
        write_new(RUN / 'inputs/code' / name, (ROOT / name).read_text(), raw=True)
    manifest = {'runtime': str(PYTHON), 'interpreter_executable_sha256': digest(PYTHON),
                'index_freeze_sha256': digest(INDEX / 'freeze.json'),
                'upstream_answer_source_freeze_sha256': digest(ROOT / 'runs/answer_source1/freeze.json'),
                'model_calls': 0, 'cohort': 'Previously observed development snapshots',
                'development_tests': 'Same-snapshot interception tests already run before formal freeze; not a blind holdout'}
    write_new(RUN / 'inputs/provenance.json', manifest)
    freeze(RUN / 'inputs')
    records = []
    for name, kind, snapshot, timeout in [
        ('isolation', 'isolation_selftest', None, 15),
        ('timeout', 'timeout_selftest', None, 0.5),
        ('predicate', 'binaryio_predicate', None, 15),
    ] + [(cell, 'anyio_option_path', INDEX / 'indices/anyio' / cell, 15)
         for cell in ('safe_base', 'safe_head', 'unsafe_base', 'unsafe_head')]:
        result = run_probe(kind, PYTHON, snapshot=snapshot, timeout=timeout)
        write_new(RUN / 'raw' / (name + '.json'), result)
        records.append({'name': name, **result})
        print(json.dumps({'name': name, 'status': result['status'], 'observation': result.get('observation')}), flush=True)
        expected = 'timeout' if name == 'timeout' else 'ok'
        if result['status'] != expected:
            write_new(RUN / 'failure.json', {'name': name, 'status': result['status']})
            raise RuntimeError('Probe failed; original result preserved')
        if name == 'isolation' and not all(result['observation'].values()):
            raise RuntimeError('Isolation self-test did not enforce policy')
    write_new(RUN / 'observations.json', records)
    by_name = {r['name']: r for r in records}
    tasks = [json.loads(line) for line in (ROOT / 'runs/question_review1/base_requests.jsonl').read_text().splitlines()]
    evidence = []
    for i, task in enumerate(tasks):
        name = 'predicate' if i < 2 else 'safe_head' if i == 2 else 'unsafe_head'
        # No cell labels/absolute snapshot paths/gold verdicts enter content.
        observation = by_name[name]['observation']
        evidence.append({'task_id': task['task_id'], 'id': 'RUNTIME-01', 'kind': 'bounded_runtime_observation',
                         'content': json.dumps(observation, ensure_ascii=False),
                         'limitation': ('Predicate only; no production Click path executed' if i < 2 else
                                        'Fixed sentinel input with boundary interception, not actual clock installation')})
    write_new(RUN / 'evidence.jsonl', evidence, jsonl=True)
    write_new(RUN / 'completion.json', {'complete': True, 'top_level_probe_runs': 7,
                                       'task_observations': 5, 'model_calls': 0,
                                       'timeout_selftest_expected': True, 'whole_client_execution': False})
    assert_frozen(RUN / 'inputs')
    assert_frozen(INDEX)
    print('Runtime observations and label-free evidence blocks saved.')


if __name__ == '__main__':
    main()
