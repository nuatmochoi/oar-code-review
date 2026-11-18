"""Three deterministic evidence arms; immutable stage reuse and paced fresh calls.

No AST or native-language-server work occurs in this runner. Completed stages
are referenced, never copied as fresh output or replayed after interruption.
"""
import argparse
import collections
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import random
import signal
import sys
import threading
import time

R = Path(__file__).resolve().parent
ROOT = R.parents[1]
PRIOR = ROOT / 'runs/lsp_agentic_preserved_recovery_20261023'
COMMON = ROOT / 'runs/lsp_agentic_preserved_20261023/experiment.py'
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('sealed_ablation_common', COMMON)
e = importlib.util.module_from_spec(spec); spec.loader.exec_module(e)
e.R = R; e.MODEL_WORKERS = 2
read, save, sha, save_equal = e.read, e.save, e.sha, e.save_equal
build_prompt, packet_digest, MODEL = e.build_prompt, e.packet_digest, e.MODEL
ARMS = ('B0', 'B1', 'B2')
MODEL_WORKERS = 2
START_INTERVAL_SECONDS = 3.0
_transport = e.call_packet
_start_lock = threading.Lock()
_last_start = None
_session_id = None
_pause_requested = False
_manifest_cache = {}
_prior_seal_cache = None


def stage_manifest():
    path = R / "stage_reuse_manifest.json"
    stat = path.stat(); key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _manifest_cache:
        _manifest_cache.clear(); _manifest_cache[key] = read(path)
    return _manifest_cache[key]


def packet_path(job, stage):
    suffix = 'initial' if stage == 1 else 'final'
    return R / 'packets' / job['arm'] / f'{job["case_id"]}_{job["world"]}_{suffix}.json'


def stage_key(job, stage):
    return f'{job["run_id"]}_s{stage}'


def jobs():
    rng = random.Random(2026102401)
    blocks = [(cid, w, repeat) for cid in sorted(read(R / 'labels.json')) for w in ['a', 'b'] for repeat in [1, 2, 3]]
    rng.shuffle(blocks)
    result = []
    for index, (cid, world, repeat) in enumerate(blocks):
        arms = list(ARMS); rng.shuffle(arms)
        result.extend({'run_id': f'{cid}_{world}_{arm}_r{repeat}', 'case_id': cid, 'world': world,
                       'arm': arm, 'repeat': repeat, 'block': index} for arm in arms)
    return result


def normalized_command(command):
    result = list(command)
    for option, value in [('--output-schema', '__EVAL_TMP__/schema.json'),
                          ('--output-last-message', '__EVAL_TMP__/response.json'), ('-C', '__EVAL_TMP__')]:
        result[result.index(option) + 1] = value
    return result


def settings(schema_path):
    from ace_review.isolated_review import command_for
    verified_prior(PRIOR / 'algorithm_seal.json')
    origin = read(PRIOR / 'algorithm_seal.json')['files']
    dependencies = ['ace_review/context_adaptive.py', 'ace_review/isolated_review.py', 'ace_review/packet_review.py', 'ace_review/reviewer.py', str(COMMON.relative_to(ROOT))]
    dependency_hashes = {name: origin[name] for name in dependencies}
    assert all(sha(ROOT / name) == digest for name, digest in dependency_hashes.items()), 'original common/transport dependency changed'
    value = {'original_algorithm_seal_sha256': sha(PRIOR / 'algorithm_seal.json'), 'original_dependency_sha256': dependency_hashes, 'model': MODEL, 'reasoning_effort': 'low', 'schema_sha256': sha(schema_path),
             'command': normalized_command(command_for(Path('__EVAL_TMP__'), MODEL)),
             'transport_module_sha256': sha(ROOT / 'ace_review/isolated_review.py'),
             'common_prompt_builder_module_sha256': sha(COMMON)}
    return {**value, 'fingerprint_sha256': e.digest(json.dumps(value, ensure_ascii=False, sort_keys=True))}


def verified_prior(path):
    global _prior_seal_cache
    if _prior_seal_cache is None: _prior_seal_cache = read(PRIOR / 'result_seal.json')['files']
    seal = _prior_seal_cache
    name = str(Path(path).relative_to(PRIOR))
    expected = seal[name]
    expected = expected['sha256'] if isinstance(expected, dict) else expected
    assert sha(path) == expected, ('prior sealed file changed', name)
    return expected


def make_reuse_manifest():
    assert not list((R / 'actual_cli_starts').glob('*.json'))
    entries = {}; all_jobs = jobs()
    for job in all_jobs:
        meta = read(R / 'cases' / f'{job["case_id"]}_{job["world"]}.json')
        original = f'{job["case_id"]}_{job["world"]}_F_r{job["repeat"]}'
        first = read(PRIOR / 'raw' / f'{original}_s1.json') if job['arm'] == 'B2' else None
        for stage in [1, 2]:
            key = stage_key(job, stage); packet = read(packet_path(job, stage))
            schema = R / ('schema_first.json' if stage == 1 else 'schema_final.json')
            config = settings(schema)
            prior_response = {k: v for k, v in first['response'].items() if k != 'context_requests'} if first is not None else None
            prompt = build_prompt(meta, packet, stage == 1, prior_response) if stage == 1 or first else None
            entry = {'logical_stage_id': key, 'job': job, 'stage': stage, 'mode': 'fresh',
                     'logical_packet_path': str(packet_path(job, stage).relative_to(ROOT)),
                     'logical_packet_file_sha256': sha(packet_path(job, stage)),
                     'logical_packet_sha256': packet_digest(packet), 'settings': config,
                     'expected_prompt_sha256': e.digest(prompt) if prompt is not None else None,
                     'raw_path': str((R / 'raw' / f'{key}.json').relative_to(ROOT)),
                     'input_path': str((R / 'inputs' / f'{key}.json').relative_to(ROOT)),
                     'trace_path': str((R / 'raw' / f'{key}.trace.json').relative_to(ROOT)),
                     'schema_path': str(schema.relative_to(ROOT)), 'original_stage_id': key}
            if job['arm'] == 'B2':
                oldkey = f'{original}_s{stage}'
                paths = {'raw_path': PRIOR / 'raw' / f'{oldkey}.json', 'input_path': PRIOR / 'inputs' / f'{oldkey}.json',
                         'trace_path': PRIOR / 'raw' / f'{oldkey}.trace.json', 'schema_path': PRIOR / schema.name}
                hashes = {name: verified_prior(path) for name, path in paths.items()}
                raw, inp, trace = (read(paths[name]) for name in ['raw_path', 'input_path', 'trace_path'])
                assert raw['status'] == 'ok' and raw['run_id'] == oldkey and raw['model'] == MODEL
                assert raw['tool_audit']['tool_call_count'] == 0
                assert raw['prompt_sha256'] == inp['prompt_sha256'] == e.digest(inp['prompt'])
                assert raw['packet_sha256'] == inp['packet_sha256']
                assert hashes['schema_path'] == config['schema_sha256']
                assert normalized_command(trace['command']) == config['command'], ('settings differ', oldkey)
                same = prompt == inp['prompt']
                if stage == 1: assert same, ('B2 initial prompt must be exact F', key)
                entry['prior_comparison'] = {'original_stage_id': oldkey, 'prompt_identical': same,
                    'original_prompt_sha256': inp['prompt_sha256'], 'schema_identical': True,
                    'model_and_command_settings_identical': True}
                if same:
                    entry.update(mode='reused', original_stage_id=oldkey,
                                 **{name: str(path.relative_to(ROOT)) for name, path in paths.items()},
                                 original_file_sha256=hashes,
                                 original_packet_sha256=inp['packet_sha256'],
                                 source_result_seal_path=str((PRIOR / 'result_seal.json').relative_to(ROOT)),
                                 source_result_seal_sha256=sha(PRIOR / 'result_seal.json'))
            entries[key] = entry
    counts = dict(collections.Counter(x['mode'] for x in entries.values()))
    result = {'created_at': e.utc(), 'logical_trajectories': len(all_jobs), 'logical_stage_slots': len(all_jobs) * 2,
              'stage_counts': counts, 'reuse_rule': 'B2 only; exact reconstructed prompt/schema/model/transport settings. Original immutable outputs remain in their original directory.',
              'entries': entries}
    save(R / 'stage_reuse_manifest.json', result)
    print(json.dumps({'reuse_manifest': counts, 'logical_stage_slots': len(entries)}), flush=True)
    return result


def resolve_stage(job, stage):
    key = stage_key(job, stage)
    entry = stage_manifest()['entries'][key]
    return {name: ROOT / entry[name] for name in ['raw_path', 'input_path', 'trace_path', 'schema_path']} | {
        'reused': entry['mode'] == 'reused', 'original_stage_id': entry['original_stage_id'],
        'logical_stage_id': key, 'entry': entry}


stage_paths = resolve_stage


def stage_record(job, stage):
    path = resolve_stage(job, stage)['raw_path']
    return read(path) if path.exists() else None


def check():
    records = []
    for cid in sorted(read(R / 'labels.json')):
        for w in ['a', 'b']:
            stem = f'{cid}_{w}'; meta = read(R / 'cases' / f'{stem}.json')
            assert meta['common'] == read(PRIOR / 'cases' / f'{stem}.json')['common']
            for arm in ARMS:
                job = {'arm': arm, 'case_id': cid, 'world': w}
                initial, final = [read(packet_path(job, stage)) for stage in [1, 2]]
                for packet in [initial, final]:
                    assert isinstance(packet['source'], str) and isinstance(packet['plain'], str) and isinstance(packet['audit'], dict)
                    assert e.token_count(packet['source']) <= 5200 and e.token_count(packet['plain']) <= 800
                    queries = packet['audit'].get('queries', [])
                    assert len(queries) <= 18 and all(q.get('depth', 0) <= 3 for q in queries)
                    if arm in ['B0', 'B1']: assert not queries, ('native query leakage', arm, stem)
                if arm in ['B0', 'B1']:
                    assert initial['source'] == final['source'] and initial['plain'] == final['plain']
                if arm == 'B0': assert initial['source'] == initial['plain'] == ''
                if arm == 'B2':
                    old = read(PRIOR / 'packets' / 'seed' / f'{stem}_initial.json')
                    assert initial['source'] == old['source'] and initial['plain'] == old['plain']
                records.append({'case_id': cid, 'world': w, 'arm': arm,
                                'initial_packet_sha256': packet_digest(initial), 'final_packet_sha256': packet_digest(final)})
    for name in ['schema_first.json', 'schema_final.json']:
        assert sha(R / name) == verified_prior(PRIOR / name), ('schema mismatch', name)
    assert len(records) == 72
    manifest = read(R / 'stage_reuse_manifest.json')
    assert len(manifest['entries']) == 432
    for job in jobs():
        for stage in [1, 2]:
            resolved = resolve_stage(job, stage); item = resolved['entry']
            assert item['job'] == job and item['stage'] == stage
            assert item['logical_packet_file_sha256'] == sha(packet_path(job, stage))
            assert item['settings'] == settings(R / ('schema_first.json' if stage == 1 else 'schema_final.json'))
            if resolved['reused']:
                for name, digest in item['original_file_sha256'].items(): assert sha(resolved[name]) == digest
                meta = read(R / 'cases' / f'{job["case_id"]}_{job["world"]}.json')
                prior = stage_record(job, 1)['response'] if stage == 2 else None
                if prior is not None: prior = {k: v for k, v in prior.items() if k != 'context_requests'}
                prompt = build_prompt(meta, read(packet_path(job, stage)), stage == 1, prior)
                assert prompt == read(resolved['input_path'])['prompt']
    return {'all_checks_passed': True, 'worlds': 24, 'arms': 3, 'logical_trajectories': 216,
            'logical_stage_slots': 432, 'stage_counts': manifest['stage_counts'], 'packet_rows': records,
            'native_calls_in_model_loop': 0, 'AST_pickle_loads_in_model_loop': 0}


def freeze():
    assert not list((R / 'actual_cli_starts').glob('*.json'))
    preflight = check(); save_equal(R / 'harness_preflight.json', preflight)
    algorithm = read(R / 'algorithm_seal.json')
    for name, digest in algorithm['files'].items(): assert sha(ROOT / name) == digest
    paths = [p for folder in ['cases', 'packets', 'workspace_manifests', 'preparation', 'candidate_history'] for p in (R / folder).rglob('*.json')]
    paths += [R / name for name in ['selected.json', 'scopes.json', 'labels.json', 'witness_validation.json',
                'schema_first.json', 'schema_final.json', 'grading_rules.json', 'PROTOCOL_KO.md',
                'stage_reuse_manifest.json', 'harness_preflight.json', 'algorithm_seal.json']]
    for name in ['packet_manifest.json', 'preparation_summary.json', 'preflight.json', 'packet_preflight.json', 'control_provenance.json', 'reference_seals.json', 'packing_intervention_summary.json', 'independent_packet_check.json']:
        if (R / name).exists(): paths.append(R / name)
    result = {'created_at': e.utc(), 'jobs': jobs(), 'arms': list(ARMS), 'logical_reviews': 216,
              'logical_stage_slots': 432, 'max_physical_calls': preflight['stage_counts'].get('fresh', 0),
              'reused_stage_slots': preflight['stage_counts'].get('reused', 0), 'model': MODEL,
              'reasoning_effort': 'low', 'repeats': 3, 'model_workers': 2, 'native_workers': 0,
              'minimum_global_CLI_start_interval_seconds': 3.0, 'source_tokens_cap': 5200, 'observation_tokens_cap': 800,
              'resource_circuit': 'first explicit rate-limit/resource error stops all further admissions; accepted calls drain; no retries',
              'model_admission': 'random case/world/repeat blocks; round-robin available B0/B1/B2 queues; stage2 priority within an arm',
              'resume_policy': 'verify append-only checkpoints, reuse successful completed stages, never replay actual-start/error/ambiguous stages; only missing stages may execute',
              'files': {str(p.relative_to(ROOT)): sha(p) for p in sorted(set(paths))}}
    save(R / 'freeze.json', result); print(json.dumps({k: result[k] for k in ['logical_reviews', 'logical_stage_slots', 'max_physical_calls', 'reused_stage_slots']}), flush=True)


def paced_call(row, root, model, schema):
    global _last_start
    with _start_lock:
        if _last_start is not None:
            delay = _last_start + START_INTERVAL_SECONDS - time.monotonic()
            if delay > 0: time.sleep(delay)
        now = time.monotonic(); _last_start = now
        save(R / 'actual_cli_starts' / f'{row["run_id"]}.json', {'run_id': row['run_id'], 'session_id': _session_id,
             'started_unix': time.time(), 'started_monotonic': now, 'minimum_start_interval_seconds': START_INTERVAL_SECONDS})
    return _transport(row, root, model, schema)


e.call_packet = paced_call


def evidence_paths(job):
    rid = job['run_id']; result = []
    for stage in [1, 2]:
        key = stage_key(job, stage)
        if resolve_stage(job, stage)['reused']: continue
        result.extend(R / folder / f'{key}{suffix}' for folder, suffix in [
            ('inputs', '.json'), ('raw', '.json'), ('raw', '.trace.json'), ('call_metrics', '.started.json'),
            ('call_metrics', '.finished.json'), ('actual_cli_starts', '.json'), ('stage_claims', '.json')])
    result.extend([R / 'retrieval' / f'{rid}.json', R / 'trajectories' / f'{rid}.json'])
    result.extend(p for s in [1, 2] for p in (R / 'admissions' / stage_key(job, s)).glob('*.json'))
    return [p for p in result if p.exists()]


def checkpoint(job, event):
    files = {str(p.relative_to(R)): sha(p) for p in evidence_paths(job)}
    save(R / 'checkpoints' / job['run_id'] / f'{time.time_ns()}.json', {
        'run_id': job['run_id'], 'session_id': _session_id, 'event': event, 'created_at': e.utc(),
        'files': files, 'stage_modes': {str(s): resolve_stage(job, s)['entry']['mode'] for s in [1, 2]}})


def validate_existing(all_jobs):
    for name, digest in {**read(R / 'algorithm_seal.json')['files'], **read(R / 'freeze.json')['files']}.items():
        assert sha(ROOT / name) == digest, ('frozen hash changed', name)
    known = {stage_key(j, s) for j in all_jobs for s in [1, 2] if not resolve_stage(j, s)['reused']}
    for folder in ['inputs', 'actual_cli_starts', 'stage_claims']:
        assert {p.stem for p in (R / folder).glob('*.json')} <= known
    assert {p.name.split('.trace')[0].removesuffix('.json') for p in (R / 'raw').glob('*.json')} <= known
    for job in all_jobs:
        records = sorted((R / 'checkpoints' / job['run_id']).glob('*.json'))
        for path in records:
            item = read(path)
            for name, digest in item['files'].items(): assert sha(R / name) == digest, ('checkpoint mismatch', name)
        for stage in [1, 2]:
            resolved = resolve_stage(job, stage)
            if resolved['reused']:
                for name, digest in resolved['entry']['original_file_sha256'].items(): assert sha(resolved[name]) == digest
            record = stage_record(job, stage)
            if record is not None:
                inp, trace = read(resolved['input_path']), read(resolved['trace_path'])
                assert record['run_id'] == resolved['original_stage_id'] and record['model'] == MODEL
                assert record['prompt_sha256'] == inp['prompt_sha256'] == e.digest(inp['prompt'])
                assert record['packet_sha256'] == inp['packet_sha256']
                assert normalized_command(trace['command']) == resolved['entry']['settings']['command']
                if not resolved['reused']: assert (R / 'actual_cli_starts' / f'{stage_key(job, stage)}.json').exists()
                if record['status'] == 'ok': assert record['tool_audit']['tool_call_count'] == 0
    return True


def stored_retrieval(job):
    packet = read(packet_path(job, 2)); rid = job['run_id']
    value = {'source': packet['source'], 'observations': packet['plain'], 'audit': packet['audit'],
             'packet_sha256': packet_digest(packet), 'actual_additional_native_trace': [],
             'fixed_navigation_reused': True, 'context_requests_ignored': True,
             'deterministic_prepared_packet': True, 'fresh_native_queries': 0}
    if job['arm'] == 'B2':
        old = PRIOR / 'lsp_fixed' / f'{job["case_id"]}_{job["world"]}.json'
        value.update(historical_native_trace_reference=str(old.relative_to(ROOT)), historical_native_trace_sha256=verified_prior(old))
    save_equal(R / 'retrieval' / f'{rid}.json', value)
    return packet


def uncertain_stage(job, stage):
    if resolve_stage(job, stage)['reused'] or stage_record(job, stage) is not None: return False
    key = stage_key(job, stage)
    return any((R / folder / f'{key}{suffix}').exists() for folder, suffix in [
        ('inputs', '.json'), ('raw', '.trace.json'), ('call_metrics', '.started.json'), ('actual_cli_starts', '.json')])


def run_scheduler(all_jobs):
    ready = {arm: collections.deque() for arm in ARMS}; states = {}; finished = []; futures = {}; admissions = []
    events = [read(p) for p in sorted((R / 'circuit_events').glob('*.json'))]
    halted = bool(events); arm_index = ARMS.index(all_jobs[0]['arm']) if all_jobs else 0; next_admission = 0.0

    def complete(job, status, error=None):
        rid = job['run_id']
        if (R / 'trajectories' / f'{rid}.json').exists():
            result = read(R / 'trajectories' / f'{rid}.json')
        else:
            first, final = stage_record(job, 1), stage_record(job, 2)
            actual = sum((R / 'actual_cli_starts' / f'{stage_key(job, s)}.json').exists() for s in [1, 2])
            result = {**job, 'status': status, 'physical_calls': actual, 'fresh_physical_calls': actual,
                      'logical_stage_slots': 2, 'stage_modes': {str(s): resolve_stage(job, s)['entry']['mode'] for s in [1, 2]},
                      'source_stage_ids': {str(s): resolve_stage(job, s)['original_stage_id'] for s in [1, 2]},
                      'accepted_requests': 0, 'context_requests_ignored': True, 'completed_at': e.utc()}
            if first and first.get('status') == 'ok': result.update(first_review=first['response']['review'], request_count=len(first['response']['context_requests']))
            if final: result['final_review'] = final.get('response')
            if error: result['error'] = str(error)
            save(R / 'trajectories' / f'{rid}.json', result); checkpoint(job, 'trajectory_finished')
        finished.append(result)

    def halt(found, job, stage):
        nonlocal halted
        if found and not halted:
            halted = True
            event = {'at': e.utc(), 'unix': time.time(), 'trigger_run_id': job['run_id'], 'stage': stage,
                     'explicit_signal': found, 'model_in_flight': len(futures), 'session_id': _session_id,
                     'action': 'stop all new admissions, drain accepted calls, retain unfinished trajectories as aborted_resource'}
            events.append(event); save(R / 'circuit_events' / '001.json', event)
            for queue in ready.values(): queue.clear()
            print(json.dumps({'resource_halt': event}), flush=True)

    # Detect a recorded resource failure even if a prior process stopped before
    # its coordinator could persist the circuit event.
    for job in all_jobs:
        for stage in [1, 2]:
            record = stage_record(job, stage)
            if record and record['status'] != 'ok': halt(e.resource_signal(record=record, trace_path=resolve_stage(job, stage)['trace_path']), job, stage)
    for job in all_jobs:
        rid = job['run_id']; states[rid] = {}
        if (R / 'trajectories' / f'{rid}.json').exists(): complete(job, 'unused'); continue
        first, final = stage_record(job, 1), stage_record(job, 2)
        if final is not None:
            assert first is not None and first['status'] == 'ok'
            stored_retrieval(job)
            complete(job, 'ok' if final['status'] == 'ok' else 'final_call_error'); continue
        if first is not None and first['status'] != 'ok': complete(job, 'first_call_error'); continue
        if uncertain_stage(job, 1) or uncertain_stage(job, 2):
            complete(job, 'interrupted_stage_not_retried', 'Existing input/start/trace without a complete raw record; never reissued.'); continue
        if halted: continue
        if first is not None:
            states[rid]['first'] = first; packet = stored_retrieval(job); checkpoint(job, 'restored_first_and_final_packet')
            ready[job['arm']].append((job, 2, packet, first['response']))
        else: ready[job['arm']].append((job, 1, None, None))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='ablation-model') as pool:
        while any(ready.values()) or futures:
            if _pause_requested:
                for queue in ready.values(): queue.clear()
            if not halted and not _pause_requested and len(futures) < 2 and any(ready.values()) and time.monotonic() >= next_admission:
                for _ in ARMS:
                    arm = ARMS[arm_index]; arm_index = (arm_index + 1) % len(ARMS)
                    if ready[arm]: break
                job, stage, packet, prior = ready[arm].popleft(); key = stage_key(job, stage)
                assert not resolve_stage(job, stage)['reused'] and not uncertain_stage(job, stage) and stage_record(job, stage) is None
                if stage == 1: packet = read(packet_path(job, 1))
                claim = R / 'stage_claims' / f'{key}.json'
                if not claim.exists(): save(claim, {'run_id': key, 'session_id': _session_id, 'admitted_at': e.utc()})
                checkpoint(job, f's{stage}_admitted')
                admission = {'run_id': job['run_id'], 'stage': stage, 'arm': arm, 'unix': time.time(),
                             'session_id': _session_id, 'in_flight_before': len(futures), 'admission_limit': 2}
                save(R / 'admissions' / key / f'{_session_id or time.time_ns()}.json', admission); admissions.append(admission)
                futures[pool.submit(e.model_stage, job, stage, packet, prior)] = (job, stage)
                next_admission = time.monotonic() + START_INTERVAL_SECONDS
            if not futures:
                if halted or _pause_requested: break
                time.sleep(min(.05, max(.001, next_admission - time.monotonic()))); continue
            done, _ = concurrent.futures.wait(futures, timeout=.1, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                job, stage = futures.pop(future); rid = job['run_id']
                try: record = future.result()
                except Exception as exc:
                    halt(e.resource_signal(exception=exc), job, stage); complete(job, 'infrastructure_error', repr(exc)); continue
                checkpoint(job, f's{stage}_finished')
                if record['status'] != 'ok':
                    halt(e.resource_signal(record=record, trace_path=resolve_stage(job, stage)['trace_path']), job, stage)
                    complete(job, 'first_call_error' if stage == 1 else 'final_call_error'); continue
                if stage == 2: complete(job, 'ok'); continue
                states[rid]['first'] = record
                if not halted and not _pause_requested:
                    packet = stored_retrieval(job); checkpoint(job, 'final_packet_ready')
                    ready[job['arm']].appendleft((job, 2, packet, record['response']))
    if halted:
        for job in all_jobs:
            if not (R / 'trajectories' / f'{job["run_id"]}.json').exists(): complete(job, 'aborted_resource', 'Resource circuit; no additional call admitted.')
    return finished, events, admissions


def pause_handler(signum, frame):
    global _pause_requested
    _pause_requested = True
    print(json.dumps({'pause_requested': True, 'signal': signum, 'action': 'stop admissions and drain accepted calls'}), flush=True)


def run(resume=False):
    global _session_id, _last_start, _pause_requested
    frozen = read(R / 'freeze.json'); all_jobs = frozen['jobs']; validate_existing(all_jobs)
    prior_sessions = sorted((R / 'runtime').glob('*.started.json'))
    assert resume or not prior_sessions, 'Use explicit --resume for an existing cohort.'
    assert not (R / 'run_summary.json').exists(), 'Cohort already has a final summary.'
    if not resume: assert not list((R / 'actual_cli_starts').glob('*.json'))
    lock = R / 'runner.lock'
    if lock.exists():
        pid = int(lock.read_text())
        try: os.kill(pid, 0)
        except ProcessLookupError:
            stale = R / 'stale_locks' / f'{time.time_ns()}.lock'; stale.parent.mkdir(exist_ok=True); lock.rename(stale)
        else: raise RuntimeError(f'Runner PID {pid} is still alive.')
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.write(fd, str(os.getpid()).encode()); os.close(fd)
    _session_id = str(time.time_ns()); started = time.time(); _pause_requested = False
    start = {'session_id': _session_id, 'started_at': e.utc(), 'started_unix': started, 'pid': os.getpid(),
             'resume': resume, 'model_workers': 2, 'native_workers': 0, 'frozen_manifest_sha256': sha(R / 'freeze.json')}
    old_handlers = {s: signal.signal(s, pause_handler) for s in [signal.SIGINT, signal.SIGTERM]}
    try:
        save(R / 'runtime' / f'{_session_id}.started.json', start)
        previous = [read(p)['started_unix'] for p in (R / 'actual_cli_starts').glob('*.json')]
        if previous: _last_start = time.monotonic() - max(0, started - max(previous))
        outcomes, circuits, admissions = run_scheduler(all_jobs); ended = time.time()
        session = {**start, 'ended_at': e.utc(), 'ended_unix': ended, 'elapsed_seconds': ended - started,
                   'status': 'paused' if _pause_requested and len(outcomes) < len(all_jobs) else 'finished',
                   'new_CLI_calls_this_process': e._started, 'completed_trajectories': len(outcomes),
                   'resource_circuit_count': len(circuits), 'admissions_this_session': len(admissions)}
        save(R / 'runtime' / f'{_session_id}.finished.json', session)
        if session['status'] == 'paused':
            save(R / 'pause_checkpoints' / f'{_session_id}.json', {**session,
                'stage_manifest_sha256': sha(R / 'stage_reuse_manifest.json'),
                'trajectories_pending': len(all_jobs) - len(outcomes), 'workers_drained': True,
                'automatic_resume': False})
            print(json.dumps(session), flush=True); return 130
        records = [read(p) for p in (R / 'raw').glob('*_s[12].json')]
        actual = list((R / 'actual_cli_starts').glob('*.json'))
        sessions = [read(p) for p in (R / 'runtime').glob('*.finished.json')]
        elapsed = sum(x['elapsed_seconds'] for x in sessions)
        counts = read(R / 'stage_reuse_manifest.json')['stage_counts']
        result = {'ended_at': e.utc(), 'logical_trajectories': len(all_jobs), 'completed_trajectories': len(outcomes),
                  'logical_stage_slots': len(all_jobs) * 2, 'planned_fresh_stage_slots': counts.get('fresh', 0),
                  'reused_stage_slots': counts.get('reused', 0), 'fresh_CLI_invocations': len(actual),
                  'fresh_raw_records': len(records), 'fresh_model_statuses': dict(collections.Counter(x['status'] for x in records)),
                  'trajectory_statuses': dict(collections.Counter(x['status'] for x in outcomes)),
                  'active_elapsed_seconds': elapsed, 'successful_fresh_calls_per_minute': sum(x['status'] == 'ok' for x in records) * 60 / elapsed,
                  'session_count': len(sessions), 'active_segments': [{'start': x['started_unix'], 'end': x['ended_unix']} for x in sessions],
                  'resource_circuit_count': len(circuits), 'fresh_native_queries': 0, 'model_workers': 2,
                  'actual_CLI_minimum_spacing_seconds': START_INTERVAL_SECONDS, 'runner_retries': 0}
        save(R / 'run_summary.json', result); print(json.dumps(result), flush=True); return 0
    finally:
        for signum, handler in old_handlers.items(): signal.signal(signum, handler)
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('command', choices=['reuse-manifest', 'check', 'freeze', 'run'])
    parser.add_argument('--launch', action='store_true'); parser.add_argument('--resume', action='store_true'); args = parser.parse_args()
    if args.command == 'reuse-manifest': make_reuse_manifest()
    elif args.command == 'check': print(json.dumps(check(), ensure_ascii=False), flush=True)
    elif args.command == 'freeze': freeze()
    else:
        assert args.launch, 'Parent launch authorization required after algorithm and input freeze.'
        raise SystemExit(run(resume=args.resume))
