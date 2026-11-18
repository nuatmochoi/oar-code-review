"""G0/H final-stage comparison using the sealed paced, resumable scheduler.

First responses remain immutable references to the original G cohort. Only
stage two can be physically invoked here. Native preparation is a separate
explicitly launched command in prepare.py; no native work occupies model slots.
"""
import argparse
import collections
import importlib.util
from pathlib import Path
import random

R = Path(__file__).resolve().parent
ROOT = R.parents[1]
PRIOR = ROOT / 'runs/lsp_agentic_preserved_recovery_20261023'
SCHEDULER = ROOT / 'runs/lsp_cumulative_ablation_20261024/experiment.py'
spec = importlib.util.spec_from_file_location('sealed_symbol_graph_scheduler', SCHEDULER)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
h.R = R
h.e.R = R
h.ARMS = ('G0', 'H')
ARMS = h.ARMS
read, save, save_equal, sha = h.read, h.save, h.save_equal, h.sha
build_prompt, packet_digest, MODEL = h.build_prompt, h.packet_digest, h.MODEL
settings, normalized_command, stage_key = h.settings, h.normalized_command, h.stage_key
resolve_stage, stage_record, stage_manifest = h.resolve_stage, h.stage_record, h.stage_manifest
verified_prior = h.verified_prior


def pair_id(job):
    return f"{job['case_id']}_{job['world']}_r{job['repeat']}"


def original_id(job):
    return f"{job['case_id']}_{job['world']}_G_r{job['repeat']}"


def jobs():
    """Randomize 72 paired blocks and arm order, with a deterministic seed."""
    rng = random.Random(2026102501)
    blocks = [(cid, world, rep) for cid in sorted(read(R / 'labels.json'))
              for world in ['a', 'b'] for rep in [1, 2, 3]]
    rng.shuffle(blocks)
    rows = []
    for block, (cid, world, rep) in enumerate(blocks):
        arms = list(ARMS)
        rng.shuffle(arms)
        for arm in arms:
            rows.append({'run_id': f'{cid}_{world}_{arm}_r{rep}', 'case_id': cid,
                         'world': world, 'arm': arm, 'repeat': rep, 'block': block,
                         'pair_id': f'{cid}_{world}_r{rep}',
                         'shared_first_stage_id': f'{cid}_{world}_G_r{rep}_s1'})
    return rows


def packet_path(job, stage):
    if stage == 1:
        return R / 'packets' / 'seed' / f"{job['case_id']}_{job['world']}_initial.json"
    return R / 'packets' / job['arm'] / f'{pair_id(job)}_final.json'


def preparation_path(job):
    return R / 'preparation' / job['arm'] / f'{pair_id(job)}.json'


def stored_retrieval(job):
    """Bind each final to prepared evidence; never replay native queries."""
    packet = read(packet_path(job, 2))
    prepared = read(preparation_path(job))
    assert prepared['status'] == 'ok'
    value = {'source': packet['source'], 'observations': packet['plain'], 'audit': packet['audit'],
             'packet_sha256': packet_digest(packet), 'actual_additional_native_trace': [],
             'native_trace_prepared_once': True, 'model_loop_fresh_native_queries': 0,
             'fixed_navigation_reused': False, 'context_requests_ignored': False,
             'prepared_before_model_calls': True, 'preparation_record': str(preparation_path(job).relative_to(ROOT)),
             'preparation_record_sha256': sha(preparation_path(job)),
             'shared_first_stage_id': job['shared_first_stage_id'],
             'native_trace_reference': prepared['native_trace_path'],
             'native_trace_reference_sha256': prepared['native_trace_sha256'],
             'native_trace_is_historical': job['arm'] == 'G0'}
    save_equal(R / 'retrieval' / f"{job['run_id']}.json", value)
    return packet


h.packet_path = packet_path
h.stored_retrieval = stored_retrieval


def save_scheduler_artifact(path, value):
    """Correct inherited fixed-packet metadata before the first durable write.

The scheduler itself is sealed. Its cumulative experiment marked every request
ignored; here requests were consumed during native preparation, before final
model scheduling. Count queued requests separately from executed query origins.
    """
    path = Path(path)
    if path.parent == R / 'trajectories':
        value = dict(value)
        value['context_requests_ignored'] = False
        value['native_calls_in_model_trajectory'] = 0
        prepared = read(preparation_path(value))
        if prepared['status'] == 'ok':
            audit = read(packet_path(value, 2))['audit']
            requests = audit.get('requests', [])
            gaps = audit.get('declared_gaps', [])
            additional = audit.get('additional_queries', [])
            value.update(request_usage_status='consumed_by_preparation',
                         accepted_requests=sum(row.get('outcome') == 'queued' for row in requests),
                         request_outcomes=dict(collections.Counter(row.get('outcome', 'missing') for row in requests)),
                         declared_gap_outcomes=dict(collections.Counter(row.get('outcome', 'missing') for row in gaps)),
                         executed_query_origins=dict(collections.Counter(row.get('origin', 'unspecified') for row in additional)),
                         native_queries_in_prepared_packet=len(additional),
                         native_query_trace_is_historical=value['arm'] == 'G0')
        else:
            value.update(request_usage_status='preparation_failed_or_interrupted', accepted_requests=None)
    save(path, value)


h.save = save_scheduler_artifact


def ensure_output_directory():
    """Catch the previous missing-output-parent failure without a model call."""
    root = R / 'raw'
    root.mkdir(parents=True, exist_ok=True)
    probe = root / '.write_probe'
    with probe.open('x') as stream:
        stream.write('raw output parent ready')
    assert probe.read_text() == 'raw output parent ready'
    probe.unlink()


def make_reuse_manifest():
    assert not list((R / 'actual_cli_starts').glob('*.json'))
    all_jobs = jobs()
    entries = {}
    for job in all_jobs:
        seed = read(packet_path(job, 1))
        meta = read(R / 'cases' / f"{job['case_id']}_{job['world']}.json")
        oldkey = original_id(job) + '_s1'
        first_paths = {'raw_path': PRIOR / 'raw' / f'{oldkey}.json',
                       'input_path': PRIOR / 'inputs' / f'{oldkey}.json',
                       'trace_path': PRIOR / 'raw' / f'{oldkey}.trace.json',
                       'schema_path': PRIOR / 'schema_first.json'}
        hashes = {field: verified_prior(path) for field, path in first_paths.items()}
        first = read(first_paths['raw_path'])
        first_input = read(first_paths['input_path'])
        assert first['status'] == 'ok' and first['tool_audit']['tool_call_count'] == 0
        assert first['model'] == MODEL and first['run_id'] == oldkey
        first_prompt = build_prompt(meta, seed, True)
        assert first_prompt == first_input['prompt']
        assert first['prompt_sha256'] == first_input['prompt_sha256'] == h.e.digest(first_prompt)
        assert first['packet_sha256'] == first_input['packet_sha256'] == packet_digest(seed)
        prior = {k: v for k, v in first['response'].items() if k != 'context_requests'}
        prepared = read(preparation_path(job))
        for stage in [1, 2]:
            key = stage_key(job, stage)
            schema = R / ('schema_first.json' if stage == 1 else 'schema_final.json')
            config = settings(schema)
            available = stage == 1 or prepared['status'] == 'ok'
            packet = seed if stage == 1 else read(packet_path(job, 2)) if available else None
            prompt = first_prompt if stage == 1 else build_prompt(meta, packet, False, prior) if available else None
            entry = {'logical_stage_id': key, 'job': job, 'stage': stage,
                     'mode': 'reused' if stage == 1 else 'fresh',
                     'logical_packet_path': str(packet_path(job, stage).relative_to(ROOT)),
                     'logical_packet_file_sha256': sha(packet_path(job, stage)) if available else None,
                     'logical_packet_sha256': packet_digest(packet) if available else None,
                     'expected_prompt_sha256': h.e.digest(prompt) if prompt is not None else None,
                     'settings': config, 'preparation_status': prepared['status'],
                     'preparation_record_path': str(preparation_path(job).relative_to(ROOT)),
                     'raw_path': str((R / 'raw' / f'{key}.json').relative_to(ROOT)),
                     'input_path': str((R / 'inputs' / f'{key}.json').relative_to(ROOT)),
                     'trace_path': str((R / 'raw' / f'{key}.trace.json').relative_to(ROOT)),
                     'schema_path': str(schema.relative_to(ROOT)), 'original_stage_id': key}
            if stage == 1:
                assert hashes['schema_path'] == config['schema_sha256']
                assert normalized_command(read(first_paths['trace_path'])['command']) == config['command']
                entry.update(original_stage_id=oldkey,
                             **{field: str(path.relative_to(ROOT)) for field, path in first_paths.items()},
                             original_file_sha256=hashes, original_packet_sha256=first_input['packet_sha256'],
                             source_result_seal_path=str((PRIOR / 'result_seal.json').relative_to(ROOT)),
                             source_result_seal_sha256=sha(PRIOR / 'result_seal.json'))
            elif job['arm'] == 'G0':
                old_final = PRIOR / 'inputs' / f'{original_id(job)}_s2.json'
                verified_prior(old_final)
                assert prompt == read(old_final)['prompt'], ('G0 final prompt differs', key)
                assert packet_digest(packet) == read(old_final)['packet_sha256']
                entry['baseline_comparison'] = {'original_final_input_path': str(old_final.relative_to(ROOT)),
                                                'original_final_input_sha256': sha(old_final),
                                                'prompt_identical': True, 'packet_identical': True}
            entries[key] = entry
    result = {'created_at': h.e.utc(), 'logical_trajectories': 144, 'logical_stage_slots': 288,
              'stage_counts': dict(collections.Counter(e['mode'] for e in entries.values())),
              'unique_shared_first_stage_ids': 72,
              'reuse_rule': 'Both arms reference the same original G first response for each pair. Every eligible final is fresh.',
              'entries': entries}
    save(R / 'stage_reuse_manifest.json', result)
    return result


def check():
    manifest = stage_manifest()
    assert len(manifest['entries']) == 288 and manifest['stage_counts'] == {'reused': 144, 'fresh': 144}
    source_ids = set()
    failed = []
    for job in jobs():
        meta = read(R / 'cases' / f"{job['case_id']}_{job['world']}.json")
        prepared = read(preparation_path(job))
        for stage in [1, 2]:
            resolved = resolve_stage(job, stage)
            entry = resolved['entry']
            assert entry['job'] == job and entry['stage'] == stage
            assert entry['settings'] == settings(R / ('schema_first.json' if stage == 1 else 'schema_final.json'))
            if stage == 2 and prepared['status'] != 'ok':
                assert job['arm'] == 'H' and entry['logical_packet_file_sha256'] is None
                outcome = read(R / 'trajectories' / f"{job['run_id']}.json")
                assert outcome['status'] == 'preparation_error' and outcome['physical_calls'] == 0
                failed.append(job['run_id'])
                continue
            packet = read(packet_path(job, stage))
            assert sha(packet_path(job, stage)) == entry['logical_packet_file_sha256']
            assert packet_digest(packet) == entry['logical_packet_sha256']
            assert h.e.token_count(packet['source']) <= 5200 and h.e.token_count(packet['plain']) <= 800
            first = stage_record(job, 1)
            prior = {k: v for k, v in first['response'].items() if k != 'context_requests'}
            prompt = build_prompt(meta, packet, stage == 1, None if stage == 1 else prior)
            assert h.e.digest(prompt) == entry['expected_prompt_sha256']
            if stage == 1:
                source_ids.add(resolved['original_stage_id'])
                for field, digest in entry['original_file_sha256'].items():
                    assert sha(resolved[field]) == digest
                assert prompt == read(resolved['input_path'])['prompt']
    assert len(source_ids) == 72
    ensure_output_directory()
    return {'all_checks_passed': True, 'logical_trajectories': 144, 'logical_stage_slots': 288,
            'reused_first_slots': 144, 'unique_first_responses': 72, 'planned_fresh_finals': 144,
            'eligible_fresh_finals': 144 - len(failed), 'preparation_failed_trajectories': failed,
            'new_native_calls_in_model_loop': 0, 'raw_directory_write_probe': True}


def freeze():
    """Seal prepared outputs after the earlier algorithm/native-preparation seal."""
    assert not list((R / 'actual_cli_starts').glob('*.json'))
    preflight = check()
    save_equal(R / 'harness_preflight.json', preflight)
    for name, digest in read(R / 'algorithm_seal.json')['files'].items():
        assert sha(ROOT / name) == digest, name
    paths = [p for folder in ['cases', 'packets', 'workspace_manifests', 'preparation', 'preparation_failures',
                             'native_traces', 'native_claims', 'preparation_runtime', 'trajectories']
             for p in (R / folder).rglob('*.json')]
    names = ['selected.json', 'scopes.json', 'labels.json', 'witness_validation.json', 'schema_first.json',
             'schema_final.json', 'grading_rules.json', 'PROTOCOL_KO.md', 'preparation_plan.json',
             'preparation_summary.json', 'control_provenance.json', 'reference_seals.json',
             'stage_reuse_manifest.json', 'harness_preflight.json', 'algorithm_seal.json']
    paths += [R / name for name in names]
    save(R / 'freeze.json', {'created_at': h.e.utc(), 'jobs': jobs(), 'arms': list(ARMS),
         'logical_reviews': 144, 'logical_stage_slots': 288, 'max_physical_calls': 144,
         'eligible_physical_calls': preflight['eligible_fresh_finals'], 'reused_stage_slots': 144,
         'unique_shared_first_responses': 72, 'model': MODEL, 'reasoning_effort': 'low', 'repeats': 3,
         'model_workers': 2, 'native_workers': 0, 'native_preparation_workers': 2,
         'minimum_global_CLI_start_interval_seconds': 3.0, 'source_tokens_cap': 5200,
         'observation_tokens_cap': 800, 'logical_queries_cap': 18, 'new_queries_cap': 6, 'query_depth_cap': 3,
         'model_admission': 'Random paired blocks; alternate available G0/H final queues; no native work in the model queue.',
         'resource_circuit': 'First explicit rate-limit/resource error stops new admission and drains accepted calls; no retry.',
         'files': {str(p.relative_to(ROOT)): sha(p) for p in sorted(set(paths))}})


def run(resume=False):
    ensure_output_directory()
    # Reject any accidental first-stage invocation before handing off to the
    # unchanged scheduler; only original immutable first references are valid.
    assert all(resolve_stage(job, 1)['reused'] and not resolve_stage(job, 2)['reused'] for job in jobs())
    return h.run(resume=resume)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['manifest', 'check', 'freeze', 'run'])
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.command == 'manifest':
        print(make_reuse_manifest()['stage_counts'])
    elif args.command == 'check':
        print(check())
    elif args.command == 'freeze':
        freeze()
    else:
        assert args.launch, 'Parent must approve sealed preflight before model invocation.'
        raise SystemExit(run(resume=args.resume))
