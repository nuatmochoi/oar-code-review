"""Explicit, sealed continuation of one paused cohort; never replay an attempted stage."""
import argparse
import collections
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import time

R = Path(__file__).resolve().parent
ROOT = R.parents[1]
spec = importlib.util.spec_from_file_location('sealed_recovery_driver', R / 'experiment.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
e = driver.e
read, save, sha = e.read, e.save, e.sha
START_INTERVAL_SECONDS = 3.0
PROTECTED_DIRS = ('raw', 'inputs', 'call_metrics', 'actual_cli_starts', 'retrieval', 'native_metrics', 'trajectories')


def preserved_files():
    inventory = read(R / 'resume_initial_artifacts.json')['files']
    for name, digest in inventory.items():
        assert sha(R / name) == digest, ('preserved artifact changed', name)
    return inventory


def restored_packet(rid):
    record = read(R / 'retrieval' / f'{rid}.json')
    packet = {'source': record['source'], 'plain': record['observations'], 'audit': record['audit']}
    assert e.packet_digest(packet) == record['packet_sha256'], ('retrieval packet digest', rid)
    return packet


def check(require_resume_seal=False):
    """Fail closed on any stage/artifact not in the exact pre-resume checkpoint."""
    frozen = read(R / 'freeze.json')
    seals = [read(R / 'algorithm_seal.json'), frozen]
    if require_resume_seal:
        seals.append(read(R / 'resume_seal.json'))
    for seal in seals:
        for name, digest in seal['files'].items():
            assert sha(ROOT / name) == digest, ('frozen hash mismatch', name)
    checkpoint = read(R / 'pause_checkpoint.json')
    preflight = read(R / 'resume_preflight.json')
    assert preflight['checkpoint_sha256'] == sha(R / 'pause_checkpoint.json')
    assert not preflight['errors'] and checkpoint['runner_stopped_and_workers_drained']
    assert not checkpoint['ambiguous_started_without_raw']
    assert not (R / 'runner.lock').exists(), 'runner is already active'
    assert not list((R / 'circuit_events').glob('*.json')), 'resource halt may not be resumed'
    assert not (R / 'resume_session.json').exists(), 'this adapter is single-use; a new checkpoint is required after another interruption'
    assert not (R / 'run_summary.json').exists()
    assert frozen['model_workers'] == frozen['native_workers'] == 2
    assert frozen['minimum_global_CLI_start_interval_seconds'] == START_INTERVAL_SECONDS
    inventory = preserved_files()
    actual_inventory = {str(p.relative_to(R)) for folder in PROTECTED_DIRS for p in (R / folder).glob('*.json')}
    assert actual_inventory == set(inventory), ('unexpected or missing pre-resume artifact', sorted(actual_inventory ^ set(inventory)))
    jobs = frozen['jobs']
    rows = checkpoint['rows']
    assert len(jobs) == len(rows) == 144
    assert [x['run_id'] for x in jobs] == [x['run_id'] for x in rows]
    existing_stages = set()
    for job, row in zip(jobs, rows):
        rid = job['run_id']
        assert all(row[k] == v for k, v in job.items())
        claim = read(R / 'claims' / f'{rid}.json')
        assert claim['run_id'] == rid
        state = row['checkpoint_state']
        expected = {'completed_final': {'1', '2'}, 'pending_second_review': {'1'}, 'pending_retrieval_after_first': {'1'}, 'not_started': set()}[state]
        assert set(row['raw_sha256']) == expected
        for stage, digest in row['raw_sha256'].items():
            key = f'{rid}_s{stage}'
            existing_stages.add(key)
            assert sha(R / 'raw' / f'{key}.json') == digest
            raw = read(R / 'raw' / f'{key}.json')
            inp = read(R / 'inputs' / f'{key}.json')
            assert raw['run_id'] == key and raw['status'] == row['raw_status'][stage] == 'ok'
            assert raw['model'] == e.MODEL and raw['prompt_sha256'] == inp['prompt_sha256']
            assert e.digest(inp['prompt']) == inp['prompt_sha256']
            assert (R / 'actual_cli_starts' / f'{key}.json').exists()
        if row['retrieval_sha256']:
            assert sha(R / 'retrieval' / f'{rid}.json') == row['retrieval_sha256']
            restored_packet(rid)
        assert row['trajectory_record_exists'] == (state == 'completed_final')
        if state == 'completed_final':
            assert read(R / 'trajectories' / f'{rid}.json')['status'] == 'ok'
        else:
            for stage in ({'1', '2'} - expected):
                key = f'{rid}_s{stage}'
                for folder, suffix in [('raw', '.json'), ('raw', '.trace.json'), ('inputs', '.json'), ('actual_cli_starts', '.json'), ('call_metrics', '.started.json'), ('call_metrics', '.finished.json')]:
                    assert not (R / folder / f'{key}{suffix}').exists(), ('would replay stage', key)
    assert {p.stem for p in (R / 'claims').glob('*.json')} == {j['run_id'] for j in jobs}
    assert len(existing_stages) == checkpoint['fresh_model_calls'] == 17
    assert {p.stem for p in (R / 'actual_cli_starts').glob('*.json')} == existing_stages
    assert dict(collections.Counter(row['checkpoint_state'] for row in rows)) == checkpoint['states']
    return {'status': 'passed', 'preserved_calls': 17, 'remaining_model_stage_slots': 271,
            'preserved_artifacts': len(inventory), 'checkpoint_sha256': sha(R / 'pause_checkpoint.json')}


def run_scheduler(jobs, rows):
    """Same two-queue recovery controls, with checkpoint-aware initialization only."""
    if list((e.R / 'circuit_events').glob('*.json')):
        raise RuntimeError('Resource-halted cohorts cannot restart admissions.')
    by_id = {row['run_id']: row for row in rows}
    assert len(by_id) == len(jobs) and set(by_id) == {j['run_id'] for j in jobs}
    ready = {a: collections.deque() for a in ['F', 'G']}
    pending = collections.deque()
    states, finished, mf, nf = {}, [], {}, {}
    halted, events, admissions = False, [], []
    last = 'G' if not jobs or jobs[0]['arm'] == 'F' else 'F'
    next_admission = 0.0

    def complete(job, status, error=None):
        rid = job['run_id']; state = states[rid]
        now = time.time()
        rec = {**job, 'status': status,
               'physical_calls': sum((e.R / 'actual_cli_starts' / f'{rid}_s{s}.json').exists() for s in [1, 2]),
               'elapsed_seconds': state.get('pre_pause_active_seconds', 0) + now - state.get('started_unix', now),
               'resume_checkpoint_state': by_id[rid]['checkpoint_state'],
               'elapsed_excludes_user_pause': True}
        if state.get('first'):
            rec.update(first_review=state['first']['response']['review'], request_count=len(state['first']['response']['context_requests']))
        if state.get('final'): rec['final_review'] = state['final'].get('response', {})
        if error: rec['error'] = str(error)
        if (e.R / 'retrieval' / f'{rid}.json').exists():
            a = read(e.R / 'retrieval' / f'{rid}.json')['audit']
            rec.update(accepted_requests=sum(x.get('outcome') == 'queued' for x in a.get('requests', [])),
                       request_outcomes=dict(collections.Counter(x.get('outcome') for x in a.get('requests', []))),
                       declared_gap_outcomes=dict(collections.Counter(x.get('outcome') for x in a.get('declared_gaps', []))))
        save(e.R / 'trajectories' / f'{rid}.json', rec); finished.append(rec)

    def halt(signal, job, stage):
        nonlocal halted
        if signal and not halted:
            halted = True
            event = {'at': e.utc(), 'unix': time.time(), 'trigger_run_id': job['run_id'], 'stage': stage,
                     'explicit_signal': signal, 'model_in_flight': len(mf), 'native_in_flight': len(nf),
                     'action': 'stop new admissions; drain accepted operations; abort remaining full-cohort trajectories'}
            events.append(event); save(e.R / 'circuit_events' / '001.json', event)
            for q in ready.values(): q.clear()
            pending.clear(); print(json.dumps({'recovery_halt': event}), flush=True)

    def fixed_followup(job, first):
        packet = read(e.R / 'packets' / 'F' / f'{job["case_id"]}_{job["world"]}_fixed.json')
        e.store_retrieval(job, packet, [], True, {'native_trace_reference': str((e.R / 'lsp_fixed' / f'{job["case_id"]}_{job["world"]}.json').relative_to(e.R)), 'fixed_native_preparation_reused_from_predecessor': True})
        ready['F'].appendleft((job, 2, packet, first))

    # Claims are never removed or overwritten. Only this sealed checkpoint can
    # authorize a claimed-but-never-started stage to enter the scheduler.
    for job in jobs:
        rid = job['run_id']; row = by_id[rid]; states[rid] = {}
        state = row['checkpoint_state']
        assert read(e.R / 'claims' / f'{rid}.json')['run_id'] == rid
        if state == 'completed_final':
            finished.append(read(e.R / 'trajectories' / f'{rid}.json')); continue
        if state == 'not_started':
            ready[job['arm']].append((job, 1, None, None)); continue
        first = read(e.R / 'raw' / f'{rid}_s1.json')
        assert first['status'] == 'ok'
        metric = read(e.R / 'call_metrics' / f'{rid}_s1.finished.json')
        states[rid] = {'first': first, 'started_unix': time.time(),
                       'pre_pause_active_seconds': metric['ended_unix'] - metric['started_unix']}
        if state == 'pending_second_review':
            packet = restored_packet(rid)
            ready[job['arm']].appendleft((job, 2, packet, first['response']))
        elif state == 'pending_retrieval_after_first':
            if job['arm'] == 'G': pending.append((job, first['response'], time.time()))
            else: fixed_followup(job, first['response'])
        else: raise AssertionError(('unsupported checkpoint state', state))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='resume-model') as models, concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='resume-native') as natives:
        while any(ready.values()) or pending or mf or nf:
            while not halted and pending and len(nf) < 2:
                job, first, queued = pending.popleft()
                nf[natives.submit(e.agentic_retrieval, job, first, queued)] = job
            if not halted and len(mf) < 2 and any(ready.values()) and time.monotonic() >= next_admission:
                preferred = 'F' if last == 'G' else 'G'
                arm = preferred if ready[preferred] else last; last = arm
                job, stage, packet, prior = ready[arm].popleft(); rid = job['run_id']
                if stage == 1:
                    states[rid]['started_unix'] = time.time()
                    packet = read(e.R / 'packets' / 'seed' / f'{job["case_id"]}_{job["world"]}_initial.json')
                admissions.append({'run_id': rid, 'stage': stage, 'arm': arm, 'unix': time.time(), 'in_flight_before': len(mf), 'admission_limit': 2, 'phase': 'resume'})
                mf[models.submit(e.model_stage, job, stage, packet, prior)] = (job, stage)
                next_admission = time.monotonic() + START_INTERVAL_SECONDS
            if not mf and not nf:
                if halted: break
                time.sleep(min(.05, max(.001, next_admission - time.monotonic()))); continue
            done, _ = concurrent.futures.wait(list(mf) + list(nf), timeout=.1, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                if future in nf:
                    job = nf.pop(future); rid = job['run_id']
                    try: packet = future.result()
                    except Exception as exc:
                        halt(e.resource_signal(exception=exc), job, 'native'); complete(job, 'native_retrieval_error', repr(exc)); continue
                    if not halted: ready['G'].appendleft((job, 2, packet, states[rid]['first']['response']))
                    continue
                job, stage = mf.pop(future); rid = job['run_id']
                try: record = future.result()
                except Exception as exc:
                    halt(e.resource_signal(exception=exc), job, f's{stage}'); complete(job, 'infrastructure_error', repr(exc)); continue
                if record['status'] != 'ok':
                    halt(e.resource_signal(record=record, trace_path=e.R / 'raw' / f'{rid}_s{stage}.trace.json'), job, f's{stage}')
                    complete(job, 'first_call_error' if stage == 1 else 'final_call_error'); continue
                if stage == 2:
                    states[rid]['final'] = record; complete(job, 'ok'); continue
                states[rid]['first'] = record
                if halted: continue
                first = record['response']
                if job['arm'] == 'G': pending.append((job, first, time.time()))
                else: fixed_followup(job, first)
    if halted:
        for job in jobs:
            if not (e.R / 'trajectories' / f'{job["run_id"]}.json').exists():
                complete(job, 'aborted_resource', 'First explicit provider/resource limit; no further stage admitted, retained in full denominator.')
    save(e.R / 'scheduler_admissions.json', {'rows': admissions, 'circuits': events, 'ending_model_admission': 0 if halted else 2,
         'model_slots_include_only_model_stages': True, 'native_worker_limit': 2,
         'global_actual_CLI_start_interval_seconds': START_INTERVAL_SECONDS, 'aborted_jobs_retained': True,
         'phase': 'resume_only', 'pre_pause_admissions_not_persisted': True,
         'all_actual_CLI_starts_preserved_separately': True})
    return finished, events, 0 if halted else 2


def interval_maximum(intervals):
    active = maximum = 0
    for _, delta in sorted([(a, 1) for a, b in intervals] + [(b, -1) for a, b in intervals]):
        active += delta; maximum = max(maximum, active)
    return maximum


def run():
    verified = check(require_resume_seal=True)
    lock = R / 'runner.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode()); os.close(fd)
    try:
        old_runtime = [read(p) for p in (R / 'runtime').glob('*.started.json')]
        assert len(old_runtime) == 1
        prior_start = old_runtime[0]['started_unix']
        prior_metrics = [read(p) for folder in ('call_metrics', 'native_metrics') for p in (R / folder).glob('*.finished.json')]
        prior_end = max(x['ended_unix'] for x in prior_metrics)
        started = time.time(); identifier = str(time.time_ns())
        session = {'started_at': e.utc(), 'started_unix': started, 'pid': os.getpid(), 'phase': 'resume',
                   'preserved_model_calls': 17, 'maximum_new_model_calls': 271, 'whole_cohort_model_stage_slots': 288,
                   'checkpoint_sha256': verified['checkpoint_sha256'], 'resume_seal_sha256': sha(R / 'resume_seal.json')}
        save(R / 'resume_session.json', session); save(R / 'runtime' / f'{identifier}.started.json', session)
        # Preserve the global transport gap across the phase boundary without
        # assuming that the machine's monotonic clock survived a reboot.
        last_wall = max(read(p)['started_unix'] for p in (R / 'actual_cli_starts').glob('*.json'))
        driver._last_start = time.monotonic() - max(0, started - last_wall)
        print(json.dumps(session), flush=True)
        outcomes, circuits, admission = run_scheduler(read(R / 'freeze.json')['jobs'], read(R / 'pause_checkpoint.json')['rows'])
        ended = time.time(); preserved_files()
        calls = [read(p) for p in (R / 'raw').glob('*_s[12].json')]
        starts = {p.stem: read(p) for p in (R / 'actual_cli_starts').glob('*.json')}
        metrics = [read(p) for p in (R / 'call_metrics').glob('*.finished.json')]
        native_metrics = [read(p) for p in (R / 'native_metrics').glob('*.finished.json')]
        model_intervals = [(starts[x['run_id']]['started_unix'], x['ended_unix']) for x in metrics]
        native_intervals = [(x['started_unix'], x['ended_unix']) for x in native_metrics]
        elapsed = prior_end - prior_start + ended - started
        result = {'started_at': old_runtime[0]['started_at'], 'started_unix': prior_start, 'ended_at': e.utc(), 'ended_unix': ended,
                  'elapsed_seconds': elapsed, 'active_elapsed_seconds': elapsed, 'wall_elapsed_including_pause_seconds': ended - prior_start,
                  'pre_pause_active_seconds': prior_end - prior_start, 'resume_active_seconds': ended - started,
                  'excluded_pause_gap_seconds': started - prior_end,
                  'pre_pause_active_end_definition': 'last accepted model/native operation finished; excludes later checkpoint bookkeeping',
                  'active_segments': [{'phase': 'before_pause', 'start': prior_start, 'end': prior_end}, {'phase': 'resume', 'start': started, 'end': ended}],
                  'model_workers': 2, 'native_workers': 2, 'model': e.MODEL, 'max_fresh_calls': 288,
                  'calls_started_this_process': e._started, 'calls_finished_this_process': e._completed,
                  'calls_preserved_before_resume': 17, 'calls_started_whole_cohort': len(starts), 'calls_finished_whole_cohort': len(calls),
                  'raw_model_status_counts': dict(collections.Counter(x['status'] for x in calls)),
                  'maximum_simultaneous_model_calls': interval_maximum(model_intervals),
                  'maximum_simultaneous_native_jobs': interval_maximum(native_intervals),
                  'native_followup_jobs_started': len(list((R / 'native_metrics').glob('*.started.json'))),
                  'native_followup_jobs_finished': len(native_metrics),
                  'model_native_overlap_observed': any(a < d and c < b for a, b in model_intervals for c, d in native_intervals),
                  'ending_model_admission': admission, 'resource_circuit_count': len(circuits), 'completed_trajectories': len(outcomes),
                  'status_counts': dict(collections.Counter(x['status'] for x in outcomes)),
                  'calls_per_minute': len(calls) * 60 / elapsed, 'successful_calls_per_minute': sum(x['status'] == 'ok' for x in calls) * 60 / elapsed,
                  'no_retry': True, 'AST_pickle_loads_in_model_loop': 0, 'resume_preserved_artifact_count': 125,
                  'fixed_preparation_native_reused': 144, 'fresh_fixed_preparation_native_calls': 0}
        save(R / 'runtime' / f'{identifier}.finished.json', result); save(R / 'run_summary.json', result)
        print(json.dumps(result), flush=True)
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'run'])
    parser.add_argument('--launch', action='store_true')
    args = parser.parse_args()
    if args.command == 'check': print(json.dumps(check()), flush=True)
    else:
        assert args.launch, 'parent launch authorization required after resume seal'
        run()
