"""Synthetic stage-two scheduling/IO tests; zero model or native invocations."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time

import runner as r
from ace_review.packet_review import write_new


def fixture(root, pairs=2):
    """Real immutable first-response references plus synthetic final packets."""
    rows = []
    entries = {}
    seed = r.read(r.PRIOR / 'packets/seed/N01_a_initial.json')
    meta = r.read(r.PRIOR / 'cases/N01_a.json')
    r.save(root / 'cases/N01_a.json', meta)
    r.save(root / 'packets/seed/N01_a_initial.json', seed)
    for name in ['schema_first.json', 'schema_final.json']:
        r.save(root / name, r.read(r.PRIOR / name))
    for rep in range(1, pairs + 1):
        old = f'N01_a_G_r{rep}'
        retrieval = r.read(r.PRIOR / 'retrieval' / f'{old}.json')
        packet = {'source': retrieval['source'], 'plain': retrieval['observations'], 'audit': retrieval['audit']}
        for arm in ['G0', 'H']:
            job = {'run_id': f'N01_a_{arm}_r{rep}', 'case_id': 'N01', 'world': 'a', 'arm': arm,
                   'repeat': rep, 'block': rep - 1, 'pair_id': f'N01_a_r{rep}',
                   'shared_first_stage_id': old + '_s1'}
            rows.append(job)
            final_path = root / 'packets' / arm / f'N01_a_r{rep}_final.json'
            r.save(final_path, packet)
            r.save(root / 'preparation' / arm / f'N01_a_r{rep}.json',
                   {'status': 'ok', 'native_trace_path': str((r.PRIOR / 'retrieval' / f'{old}.json').relative_to(r.ROOT)),
                    'native_trace_sha256': r.sha(r.PRIOR / 'retrieval' / f'{old}.json')})
            for stage in [1, 2]:
                key = r.stage_key(job, stage)
                original = old + '_s1' if stage == 1 else key
                origin = r.PRIOR if stage == 1 else root
                entry = {'job': job, 'stage': stage, 'mode': 'reused' if stage == 1 else 'fresh',
                         'logical_stage_id': key, 'original_stage_id': original,
                         'raw_path': str(origin / 'raw' / f'{original}.json'),
                         'input_path': str(origin / 'inputs' / f'{original}.json'),
                         'trace_path': str(origin / 'raw' / f'{original}.trace.json'),
                         'schema_path': str(origin / ('schema_first.json' if stage == 1 else 'schema_final.json'))}
                entries[key] = entry
    r.save(root / 'stage_reuse_manifest.json', {'entries': entries})
    return rows


def exercise(mode):
    with tempfile.TemporaryDirectory(prefix='symbol-harness-', dir=r.R) as directory:
        root = Path(directory)
        old_root = r.R
        r.R = r.h.R = r.h.e.R = root
        r.h._session_id = 'synthetic-' + mode
        r.h._last_start = None
        r.h._pause_requested = False
        r.h.START_INTERVAL_SECONDS = .015
        r.h.e._active = r.h.e._max_active = r.h.e._completed = r.h.e._started = 0
        jobs = fixture(root)
        calls = []
        lock = threading.Lock()
        maximum = active = 0
        try:
            assert not (root / 'raw').exists()
            # This is the real exclusive writer used by the transport. It
            # reproduces the old missing-parent failure without a paid call.
            try:
                write_new(root / 'raw' / 'not-a-model.trace.json', {})
            except FileNotFoundError:
                missing_parent_reproduced = True
            else:
                raise AssertionError('Real writer unexpectedly created parent')
            r.ensure_output_directory()

            def fake_transport(row, output, model, schema):
                nonlocal maximum, active
                assert row['run_id'].endswith('_s2'), 'A reused first stage was invoked.'
                with lock:
                    ordinal = len(calls)
                    calls.append(row['run_id'])
                    active += 1
                    maximum = max(maximum, active)
                if mode == 'pause' and ordinal == 0:
                    r.h._pause_requested = True
                # The inherited scheduler polls every 100ms. Keep fake calls
                # longer than one poll so the second worker actually overlaps.
                time.sleep(.18 if ordinal == 0 else .25)
                failed = mode == 'resource' and ordinal == 0
                first_key = row['run_id'].removesuffix('_s2') + '_s1'
                original_first = r.read(r.resolve_stage(next(j for j in jobs if j['run_id'] == row['run_id'].removesuffix('_s2')), 1)['raw_path'])
                prior = {k: v for k, v in original_first['response'].items() if k != 'context_requests'}
                assert '\n[PREVIOUS REVIEW: hypotheses, not authoritative evidence]\n' + json.dumps(prior, ensure_ascii=False) in row['prompt']
                response = copy.deepcopy(r.read(r.PRIOR / 'raw/N01_a_G_r1_s2.json')['response'])
                record = {k: v for k, v in row.items() if k != 'prompt'}
                record.update(status='error' if failed else 'ok', model=model, response=response,
                              tool_audit={'tool_call_count': 0}, error='429 TooManyRequests' if failed else None)
                # Do not make the parent directory here: this test must catch
                # a regression in the pre-call output directory probe.
                write_new(output / f"{row['run_id']}.trace.json", {'stderr': 'HTTP 429 TooManyRequests' if failed else '', 'stdout': ''})
                write_new(output / f"{row['run_id']}.json", record)
                with lock:
                    active -= 1
                return record

            r.h._transport = fake_transport
            outcomes, circuits, _ = r.h.run_scheduler(jobs)
            preserved = {str(p.relative_to(root)): r.sha(p) for folder in ['raw', 'inputs', 'call_metrics', 'actual_cli_starts', 'trajectories'] for p in (root / folder).glob('*.json')}
            paused_count = len(calls)
            if mode == 'pause':
                assert len(outcomes) < len(jobs) and paused_count == 1
                r.h._pause_requested = False
                r.h._session_id = 'synthetic-resumed'
                outcomes, circuits, _ = r.h.run_scheduler(jobs)
                assert all(r.sha(root / p) == digest for p, digest in preserved.items())
            assert len(calls) == len(set(calls))
            assert len(outcomes) == len(jobs)
            assert not any((root / 'raw').glob('*_s1.json'))
            assert maximum <= 2
            if mode == 'resource':
                assert len(calls) == 2 and len(circuits) == 1
                assert sum(o['status'] == 'aborted_resource' for o in outcomes) == 2
            else:
                assert len(calls) == 4 and all(o['status'] == 'ok' for o in outcomes)
                before = list(calls)
                r.h.run_scheduler(jobs)
                assert calls == before, 'Completed stage was replayed.'
            starts = sorted([r.read(p) for p in (root / 'actual_cli_starts').glob('*.json')], key=lambda x: x['started_monotonic'])
            gaps = [b['started_monotonic'] - a['started_monotonic'] for a, b in zip(starts, starts[1:])]
            assert all(gap >= .015 - 1e-6 for gap in gaps)
            return {'mode': mode, 'passed': True, 'fake_final_calls': len(calls), 'reused_first_physical_calls': 0,
                    'max_fake_model_overlap': maximum, 'minimum_scaled_start_gap': min(gaps),
                    'raw_parent_missing_reproduced_then_repaired': missing_parent_reproduced,
                    'pause_accepted_calls': paused_count if mode == 'pause' else None,
                    'resource_events': len(circuits), 'model_calls': 0, 'native_calls': 0}
        finally:
            r.R = r.h.R = r.h.e.R = old_root


if __name__ == '__main__':
    results = [exercise(mode) for mode in ['normal', 'pause', 'resource']]
    r.save(r.R / 'harness_selfcheck_results.json', {'all_passed': True, 'tests': results,
           'test_uses_real_exclusive_output_writer': True, 'model_calls': 0, 'native_calls': 0})
    print(json.dumps(results))
