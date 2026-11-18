"""Exercise native failure accounting with cached data and a fake policy only."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import types

import prepare as p


def exercise(mode):
    original_root = p.R
    pair = copy.deepcopy(p.read(original_root / 'preparation_plan.json')['pairs'][0])
    calls = []
    previous_policy = sys.modules.get('graph_policy')
    with tempfile.TemporaryDirectory(prefix='native-accounting-', dir=original_root) as directory:
        root = Path(directory)
        p.R = p.r.R = p.r.h.R = p.r.h.e.R = root
        try:
            for name in ['labels.json', 'scopes.json']:
                p.save(root / name, p.read(original_root / name))
            stem = f"{pair['case_id']}_{pair['world']}"
            p.save(root / 'cases' / f'{stem}.json', p.read(original_root / 'cases' / f'{stem}.json'))
            original = p.read(p.ROOT / pair['G0']['original_retrieval_path'])
            result = {'packet': {'source': original['source'], 'plain': original['observations'], 'audit': original['audit']},
                      'actual_trace': original['actual_additional_native_trace'],
                      'initialization': original['initialization'], 'stderr': original['stderr']}
            fake = types.ModuleType('graph_policy')
            def followup(*args, **kwargs):
                calls.append(1)
                value = copy.deepcopy(result)
                if mode in ['error', 'timeout']:
                    audit = value['packet']['audit']
                    audit['additional_queries'][0]['error'] = 'synthetic native failure'
                    audit['queries'][-len(audit['additional_queries'])]['error'] = 'synthetic native failure'
                    audit['new_queries'][0]['error'] = 'synthetic native failure'
                    if mode == 'timeout':
                        index = next(i for i, row in enumerate(value['actual_trace'])
                                     if row.get('method') in ['textDocument/definition', 'textDocument/references'])
                        value['actual_trace'].pop(index)
                return value
            fake.followup = followup
            sys.modules['graph_policy'] = fake
            if mode == 'interrupted':
                p.save(root / 'native_claims' / f"{pair['pair_id']}.json", {'synthetic': True, 'algorithm_seal_sha256': 'synthetic-seal'})
            first = p.prepare_one(pair, 'synthetic-seal')
            observed = {str(path.relative_to(root)): p.sha(path) for path in root.rglob('*.json')}
            second = p.prepare_one(pair, 'synthetic-seal')
            assert first == second
            assert all(p.sha(root / path) == digest for path, digest in observed.items())
            if mode == 'success':
                assert len(calls) == 1 and first['status'] == 'ok'
                assert not list((root / 'trajectories').glob('*.json'))
            else:
                assert first['status'] == 'error'
                outcome = p.read(next((root / 'trajectories').glob('*.json')))
                assert outcome['status'] == 'preparation_error' and outcome['physical_calls'] == 0
                assert not list((root / 'packets' / 'H').glob('*.json'))
                if mode in ['error', 'timeout']:
                    assert len(calls) == 1 and first['trace_complete'] == (mode == 'error')
                    assert first['native_query_count'] == len(result['packet']['audit']['additional_queries'])
                    assert first['observed_native_trace_count'] == first['native_query_count'] - (mode == 'timeout')
                    assert (p.ROOT / first['partial_packet_path']).exists()
                else:
                    assert not calls and first['native_query_count'] is None and not first['trace_complete']
            return {'mode': mode, 'passed': True, 'fake_policy_calls': len(calls),
                    'status': first['status'], 'reported_cached_trace_queries': first['native_query_count'],
                    'observed_cached_trace_queries': first.get('observed_native_trace_count', 0),
                    'trace_complete': first['trace_complete'],
                    'records_preserved_on_repeat': True, 'actual_native_calls': 0, 'model_calls': 0}
        finally:
            p.R = p.r.R = p.r.h.R = p.r.h.e.R = original_root
            if previous_policy is None:
                sys.modules.pop('graph_policy', None)
            else:
                sys.modules['graph_policy'] = previous_policy


if __name__ == '__main__':
    results = [exercise(mode) for mode in ['success', 'error', 'timeout', 'interrupted']]
    p.save_equal(p.R / 'preparation_selfcheck_results.json', {'all_passed': True, 'tests': results,
                                                           'model_calls': 0, 'actual_native_calls': 0})
    print(json.dumps(results))
