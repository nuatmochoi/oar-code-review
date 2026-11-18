"""Synthetic transport tests: reuse, durable pause/resume, ambiguity and limits."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent


def load():
    spec = importlib.util.spec_from_file_location('ablation_test_harness', HERE / 'experiment.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def exercise(mode):
    m = load(); e = m.e
    first = m.read(m.PRIOR / 'raw/N01_a_F_r1_s1.json')['response']
    final = m.read(m.PRIOR / 'raw/N01_a_F_r1_s2.json')['response']
    with tempfile.TemporaryDirectory(prefix='.ablation-synthetic-', dir=HERE) as directory:
        m.R = e.R = Path(directory); m.START_INTERVAL_SECONDS = .015; m._session_id = 'phase1'
        jobs = [{'run_id': f'N01_a_{arm}_r{repeat}', 'case_id': 'N01', 'world': 'a', 'arm': arm, 'repeat': repeat, 'block': repeat - 1}
                for repeat in [1, 2] for arm in m.ARMS]
        jobs[-1] = {**jobs[-1], 'run_id': 'N01_b_B2_r2', 'world': 'b'}
        m.jobs = lambda: jobs
        for folder in ['raw', 'inputs', 'actual_cli_starts', 'stage_claims', 'retrieval', 'trajectories', 'checkpoints', 'admissions', 'circuit_events']:
            (m.R / folder).mkdir()
        for name in ['schema_first.json', 'schema_final.json']:
            e.copy_equal(m.PRIOR / name, m.R / name)
        e.copy_equal(m.PRIOR / 'cases/N01_a.json', m.R / 'cases/N01_a.json')
        seed = m.read(m.PRIOR / 'packets/seed/N01_a_initial.json')
        fixed = m.read(m.PRIOR / 'packets/F/N01_a_fixed.json')
        for arm in m.ARMS:
            initial = seed if arm != 'B0' else {'source': '', 'plain': '', 'audit': {'queries': []}}
            for stage in [1, 2]:
                m.save(m.packet_path({'arm': arm, 'case_id': 'N01', 'world': 'a'}, stage), initial if stage == 1 or arm != 'B2' else fixed)
        e.copy_equal(m.PRIOR / 'cases/N01_b.json', m.R / 'cases/N01_b.json')
        bseed = m.read(m.PRIOR / 'packets/seed/N01_b_initial.json')
        bfinal = m.read(m.PRIOR / 'packets/F/N01_b_fixed.json')
        bfinal = {**bfinal, 'source': bfinal['source'] + '\n[SYNTHETIC ADDED CONTEXT]\n'}
        for stage, packet in [(1, bseed), (2, bfinal)]: m.save(m.packet_path(jobs[-1], stage), packet)
        manifest = m.make_reuse_manifest()
        assert manifest['stage_counts'] == {'fresh': 9, 'reused': 3}
        assert manifest['entries']['N01_a_B2_r1_s2']['mode'] == 'reused'
        assert manifest['entries']['N01_b_B2_r2_s1']['mode'] == 'reused'
        assert manifest['entries']['N01_b_B2_r2_s2']['mode'] == 'fresh'
        assert manifest['entries']['N01_b_B2_r2_s2']['prior_comparison']['prompt_identical'] is False
        m.save(m.R / 'algorithm_seal.json', {'files': {}})
        m.save(m.R / 'freeze.json', {'files': {}})
        calls = []; guard = threading.Lock(); active = maximum = 0
        pause_triggered = False

        def fake(row, root, model, schema):
            nonlocal active, maximum, pause_triggered
            with guard:
                calls.append(row['run_id']); active += 1; maximum = max(maximum, active)
            try:
                time.sleep(.18)
                failed = mode == 'resource' and row['run_id'] == 'N01_a_B0_r1_s1'
                raw = {k: v for k, v in row.items() if k != 'prompt'}
                raw.update(status='error' if failed else 'ok', response=None if failed else first if row['run_id'].endswith('s1') else final,
                           model=model, error=None, duration_seconds=.18, tool_audit={'tool_call_count': 0})
                command = m.settings(m.R / ('schema_first.json' if row['run_id'].endswith('s1') else 'schema_final.json'))['command']
                m.save(root / f'{row["run_id"]}.trace.json', {'command': command, 'stdout': json.dumps({'type': 'error', 'message': '429 Too Many Requests'}) if failed else '', 'stderr': ''})
                m.save(root / f'{row["run_id"]}.json', raw)
                if mode == 'pause' and not pause_triggered:
                    pause_triggered = True; m._pause_requested = True
                return raw
            finally:
                with guard: active -= 1

        m._transport = fake
        if mode == 'ambiguous':
            m.save(m.R / 'actual_cli_starts/N01_a_B0_r1_s1.json', {'run_id': 'N01_a_B0_r1_s1', 'started_unix': time.time(), 'started_monotonic': time.monotonic(), 'session_id': 'interrupted'})
        claimed_snapshot = {}
        if mode == 'claimed_only':
            key = 'N01_a_B0_r1_s1'
            m.save(m.R / 'stage_claims' / f'{key}.json', {'run_id': key, 'session_id': 'abandoned'})
            old_admission = m.R / 'admissions' / key / 'abandoned.json'
            m.save(old_admission, {'run_id': jobs[0]['run_id'], 'stage': 1, 'session_id': 'abandoned', 'unix': time.time()})
            m.checkpoint(jobs[0], 'claimed_only_interruption')
            claimed_snapshot = {str(p.relative_to(m.R)): m.sha(p) for p in [old_admission, m.R / 'stage_claims' / f'{key}.json']}
            m._session_id = 'resumed_claim'
        outcomes, circuits, admissions = m.run_scheduler(jobs)
        if mode == 'claimed_only':
            assert all(m.sha(m.R / name) == digest for name, digest in claimed_snapshot.items())
            assert len(list((m.R / 'admissions' / 'N01_a_B0_r1_s1').glob('*.json'))) == 2
            assert calls.count('N01_a_B0_r1_s1') == 1

        snapshot = {str(p.relative_to(m.R)): m.sha(p) for folder in ['raw', 'inputs', 'actual_cli_starts', 'retrieval', 'trajectories', 'call_metrics', 'checkpoints'] for p in (m.R / folder).rglob('*.json')}
        first_phase_calls = list(calls)
        if mode == 'pause':
            assert len(first_phase_calls) == 2 and len(outcomes) == 1
            assert all('B2' not in key for key in first_phase_calls)
            m.validate_existing(jobs)
            m._pause_requested = False; m._session_id = 'phase2'
            outcomes, circuits, admissions = m.run_scheduler(jobs)
            assert all(m.sha(m.R / name) == digest for name, digest in snapshot.items())
        assert len(outcomes) == len(jobs) and len(calls) == len(set(calls)) and maximum <= 2
        assert not any(key in calls for key in ['N01_a_B2_r1_s1', 'N01_a_B2_r1_s2', 'N01_b_B2_r2_s1'])
        assert len(list((m.R / 'retrieval').glob('*.json'))) >= 1, 'reused completed trajectory needs logical retrieval record'
        starts = sorted(m.read(p)['started_monotonic'] for p in (m.R / 'actual_cli_starts').glob('*.json') if m.read(p).get('session_id') != 'interrupted')
        assert all(b - a >= .015 for a, b in zip(starts, starts[1:]))
        if mode == 'resource':
            assert len(calls) == 2 and len(circuits) == 1
            assert sum(x['status'] == 'aborted_resource' for x in outcomes) == 4
            assert all(x['unix'] <= circuits[0]['unix'] for x in admissions)
        elif mode == 'ambiguous':
            assert len(calls) == 7 and 'N01_a_B0_r1_s1' not in calls
            assert sum(x['status'] == 'interrupted_stage_not_retried' for x in outcomes) == 1
        else:
            assert len(calls) == 9 and all(x['status'] == 'ok' for x in outcomes)
            # An integrity mismatch must stop resumption, not silently regenerate.
            victim = next((m.R / 'inputs').glob('*.json')); victim.write_text(victim.read_text() + ' ')
            try: m.validate_existing(jobs)
            except AssertionError: pass
            else: raise AssertionError('checkpoint corruption was not rejected')
        return {'scenario': mode, 'status': 'passed', 'logical_trajectories': 6, 'logical_stage_slots': 12,
                'inherited_stages': 3, 'fresh_synthetic_calls': len(calls), 'max_model_concurrency': maximum,
                'minimum_scaled_start_gap': min((b - a for a, b in zip(starts, starts[1:])), default=None),
                'prior_stages_replayed': 0, 'all_denominators_retained': True, 'actual_model_or_native_calls': 0,
                'pause_first_phase_calls': len(first_phase_calls) if mode == 'pause' else None}


if __name__ == '__main__':
    result = {'status': 'passed', 'tests': [exercise('normal'), exercise('pause'), exercise('resource'), exercise('ambiguous'), exercise('claimed_only')],
              'production_defaults': {'model_workers': 2, 'global_actual_CLI_start_interval_seconds': 3.0, 'native_queries_in_execution': 0}}
    with (HERE / 'harness_selfcheck_results.json').open('x') as stream: json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))
