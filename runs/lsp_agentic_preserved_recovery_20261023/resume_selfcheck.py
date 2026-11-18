"""Synthetic transport/native tests only; never calls a model or language server."""
import collections
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time

REAL = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('resume_adapter_under_test', REAL / 'resume.py')
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
e, d = r.e, r.driver
read, save, sha = r.read, r.save, r.sha
packet = read(REAL / 'packets' / 'F' / 'N01_a_fixed.json')
seed = read(REAL / 'packets' / 'seed' / 'N01_a_initial.json')
meta = read(REAL / 'cases' / 'N01_a.json')
response = read(REAL / 'raw' / 'N01_a_F_r2_s1.json')['response']
original_model_stage = e.model_stage


def scenario(resource_halt=False):
    with tempfile.TemporaryDirectory(prefix='resume-synthetic-') as td:
        root = Path(td); r.R = root; e.R = root
        d._last_start = None; d.START_INTERVAL_SECONDS = r.START_INTERVAL_SECONDS = .005
        for name in ['_active', '_max_active', '_completed', '_started', '_native_active', '_max_native', '_native_started', '_native_completed']:
            setattr(e, name, 0)
        e._model_native_overlap = False
        for folder in ['claims', 'trajectories', 'raw', 'retrieval', 'native_metrics', 'actual_cli_starts', 'call_metrics', 'inputs', 'circuit_events']:
            (root / folder).mkdir()
        save(root / 'packets' / 'seed' / 'N01_a_initial.json', seed)
        save(root / 'packets' / 'F' / 'N01_a_fixed.json', packet)
        save(root / 'cases' / 'N01_a.json', meta)
        for name in ['schema_first.json', 'schema_final.json']:
            save(root / name, read(REAL / name))
        log, native_log = [], []
        active = maximum = 0
        guard = threading.Lock()
        fail_enabled = False

        def transport(row, outdir, model, schema):
            nonlocal active, maximum
            with guard:
                active += 1; maximum = max(maximum, active); log.append(row['run_id'])
                fail = fail_enabled and row['run_id'] == 'unstarted_F_s1'
            try:
                time.sleep(.18 if resource_halt else .025)
                record = {**{k: v for k, v in row.items() if k != 'prompt'}, 'response': response,
                          'model': model, 'status': 'error' if fail else 'ok', 'error': None}
                save(outdir / f'{row["run_id"]}.json', record)
                save(outdir / f'{row["run_id"]}.trace.json', {'stdout': json.dumps({'type': 'error', 'message': '429 Too Many Requests'}) if fail else '', 'stderr': ''})
                return record
            finally:
                with guard: active -= 1

        def native(job, first, queued):
            native_log.append(job['run_id'])
            time.sleep(.04)
            return e.store_retrieval(job, packet, [], False)

        d._transport = transport; e.agentic_retrieval = native
        specs = [('done_F', 'F', 'completed_final'), ('second_F', 'F', 'pending_second_review'),
                 ('first_F', 'F', 'pending_retrieval_after_first'), ('first_G', 'G', 'pending_retrieval_after_first'),
                 ('second_G', 'G', 'pending_second_review'), ('unstarted_F', 'F', 'not_started'), ('unstarted_G', 'G', 'not_started')]
        if resource_halt:
            specs = [('unstarted_F', 'F', 'not_started'), ('unstarted_G', 'G', 'not_started'),
                     ('later_F', 'F', 'not_started'), ('later_G', 'G', 'not_started')]
        jobs, rows = [], []
        for i, (rid, arm, state) in enumerate(specs):
            job = {'run_id': rid, 'case_id': 'N01', 'world': 'a', 'arm': arm, 'repeat': 1, 'block': i}
            jobs.append(job); save(root / 'claims' / f'{rid}.json', {'run_id': rid, 'pid': -1})
            row = {**job, 'checkpoint_state': state}; rows.append(row)
            if state == 'not_started': continue
            first = original_model_stage(job, 1, seed)
            if state in ['completed_final', 'pending_second_review']:
                e.store_retrieval(job, packet, [], arm == 'F')
            if state == 'completed_final':
                original_model_stage(job, 2, packet, first['response'])
                save(root / 'trajectories' / f'{rid}.json', {**job, 'status': 'ok', 'physical_calls': 2})
        existing = {str(p.relative_to(root)): sha(p) for p in root.rglob('*.json')}
        old_calls = set(log); log.clear(); fail_enabled = resource_halt
        outcomes, circuits, admission = r.run_scheduler(jobs, rows)
        assert all(sha(root / p) == h for p, h in existing.items()), 'existing bytes were changed'
        assert len(log) == len(set(log)) and not old_calls.intersection(log), 'stage was replayed'
        assert len(outcomes) == len(jobs) and len(list((root / 'trajectories').glob('*.json'))) == len(jobs)
        starts = sorted(read(p)['started_monotonic'] for p in (root / 'actual_cli_starts').glob('*.json'))
        assert all(b - a >= .005 - 1e-6 for a, b in zip(starts, starts[1:]))
        assert maximum <= 2
        if resource_halt:
            assert len(circuits) == 1 and admission == 0
            assert any(x['status'] == 'aborted_resource' for x in outcomes)
            assert not native_log, 'native work was newly admitted after the first resource error'
            assert all(read(p)['started_unix'] <= circuits[0]['unix'] for p in (root / 'actual_cli_starts').glob('*.json'))
            assert len(log) == 2, 'an already accepted peer must drain after halt'
        else:
            expected = {f'{rid}_s2' for rid, arm, state in specs if state in ['pending_second_review', 'pending_retrieval_after_first']}
            expected |= {f'{rid}_s{s}' for rid, arm, state in specs if state == 'not_started' for s in [1, 2]}
            assert set(log) == expected and len(log) == 8
            assert set(native_log) == {'first_G', 'unstarted_G'}, native_log
            assert not circuits and admission == 2 and all(x['status'] == 'ok' for x in outcomes)
        return {'scenario': 'resource_halt' if resource_halt else 'preserve_and_continue', 'status': 'passed',
                'preserved_artifacts': len(existing), 'preexisting_model_stages': len(old_calls), 'new_model_stages': len(log),
                'new_native_jobs': native_log, 'maximum_model_concurrency': maximum,
                'status_counts': dict(collections.Counter(x['status'] for x in outcomes)),
                'existing_stage_replays': 0, 'whole_denominator_retained': True,
                'scaled_transport_start_gap_seconds': .005, 'actual_models_or_LSPs_called': 0}


if __name__ == '__main__':
    result = {'status': 'passed', 'tests': [scenario(), scenario(True)]}
    r.R = e.R = REAL
    print(json.dumps(result, indent=2))
    save(REAL / 'resume_selfcheck_results.json', result)
