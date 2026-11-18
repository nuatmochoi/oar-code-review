"""Prepare exact G0 controls and one bounded native H follow-up per shared first.

`controls` is read/copy-only. `native --launch-native` requires the parent's
algorithm seal and is the only entry point which invokes a language server.
Claimed/failed native preparations are never retried. A failed H preparation
remains an explicit zero-model-call trajectory in the original denominator.
"""
import argparse
import collections
import concurrent.futures
import gc
import json
from pathlib import Path
import time

import runner as r

R, ROOT, PRIOR = r.R, r.ROOT, r.PRIOR
read, save, save_equal, sha = r.read, r.save, r.save_equal, r.sha


def copy_control(source, target):
    """Copy a verified immutable source without overwriting an existing file."""
    r.verified_prior(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        assert target.read_bytes() == source.read_bytes(), str(target)
    else:
        with target.open('xb') as stream:
            stream.write(source.read_bytes())


def controls():
    assert not list((R / 'actual_cli_starts').glob('*.json'))
    for name in ['selected.json', 'scopes.json', 'labels.json', 'witness_validation.json',
                 'schema_first.json', 'schema_final.json']:
        copy_control(PRIOR / name, R / name)
    provenance = {}
    pairs = []
    for cid in sorted(read(R / 'labels.json')):
        for world in ['a', 'b']:
            stem = f'{cid}_{world}'
            for folder in ['cases', 'workspace_manifests']:
                source = PRIOR / folder / f'{stem}.json'
                target = R / folder / source.name
                copy_control(source, target)
                provenance[str(target.relative_to(ROOT))] = {'origin_path': str(source.relative_to(ROOT)), 'sha256': sha(source)}
            seed_source = PRIOR / 'packets' / 'seed' / f'{stem}_initial.json'
            seed_target = R / 'packets' / 'seed' / seed_source.name
            copy_control(seed_source, seed_target)
            provenance[str(seed_target.relative_to(ROOT))] = {'origin_path': str(seed_source.relative_to(ROOT)), 'sha256': sha(seed_source)}
            for rep in [1, 2, 3]:
                oldrid = f'{cid}_{world}_G_r{rep}'
                pair = f'{cid}_{world}_r{rep}'
                first_paths = {'raw_path': PRIOR / 'raw' / f'{oldrid}_s1.json',
                               'input_path': PRIOR / 'inputs' / f'{oldrid}_s1.json',
                               'trace_path': PRIOR / 'raw' / f'{oldrid}_s1.trace.json',
                               'schema_path': PRIOR / 'schema_first.json'}
                first_hashes = {field: r.verified_prior(path) for field, path in first_paths.items()}
                first = read(first_paths['raw_path'])
                assert first['status'] == 'ok' and first['tool_audit']['tool_call_count'] == 0
                retrieval_path = PRIOR / 'retrieval' / f'{oldrid}.json'
                final_input_path = PRIOR / 'inputs' / f'{oldrid}_s2.json'
                r.verified_prior(retrieval_path)
                r.verified_prior(final_input_path)
                old = read(retrieval_path)
                packet = {'source': old['source'], 'plain': old['observations'], 'audit': old['audit']}
                assert r.packet_digest(packet) == old['packet_sha256'] == read(final_input_path)['packet_sha256']
                target = R / 'packets' / 'G0' / f'{pair}_final.json'
                save_equal(target, packet)
                prior = {k: v for k, v in first['response'].items() if k != 'context_requests'}
                prompt = r.build_prompt(read(R / 'cases' / f'{stem}.json'), packet, False, prior)
                assert prompt == read(final_input_path)['prompt'], oldrid
                response_hash = r.h.e.digest(json.dumps(first['response'], ensure_ascii=False, sort_keys=True))
                baseline = {'packet_path': str(target.relative_to(ROOT)), 'packet_file_sha256': sha(target),
                            'packet_sha256': r.packet_digest(packet),
                            'original_retrieval_path': str(retrieval_path.relative_to(ROOT)),
                            'original_retrieval_sha256': sha(retrieval_path),
                            'original_final_input_path': str(final_input_path.relative_to(ROOT)),
                            'original_final_input_sha256': sha(final_input_path)}
                record = {'status': 'ok', 'pair_id': pair, 'arm': 'G0',
                          'shared_first_stage_id': oldrid + '_s1', 'first_response_sha256': response_hash,
                          'fresh_native_query_count': 0, 'native_query_count': len(old['audit']['additional_queries']),
                          'trace_complete': True, 'native_trace_is_historical': True,
                          'native_trace_path': str(retrieval_path.relative_to(ROOT)),
                          'native_trace_sha256': sha(retrieval_path), **baseline}
                save_equal(R / 'preparation' / 'G0' / f'{pair}.json', record)
                pairs.append({'pair_id': pair, 'case_id': cid, 'world': world, 'repeat': rep,
                              'original_run_id': oldrid, 'seed_path': str(seed_target.relative_to(ROOT)),
                              'seed_sha256': sha(seed_target),
                              'shared_first': {**{field: str(path.relative_to(ROOT)) for field, path in first_paths.items()},
                                               'sha256': first_hashes},
                              'first_response_sha256': response_hash, 'G0': baseline,
                              'workspace_manifest_path': str((R / 'workspace_manifests' / f'{stem}.json').relative_to(ROOT)),
                              'base_workspace_manifest_path': str((R / 'workspace_manifests' / f"{cid}_{'b' if world == 'a' else 'a'}.json").relative_to(ROOT))})
    plan = {'pairs': pairs, 'unique_shared_first_responses': 72, 'H_native_preparations': 72,
            'native_workers': 2, 'maximum_fresh_native_queries': 432, 'seed_queries_cap': 12,
            'new_queries_cap': 6, 'logical_queries_cap': 18, 'query_depth_cap': 3,
            'source_tokens_cap': 5200, 'plain_tokens_cap': 800,
            'selection': 'Every fixed original G first response; no outcome-based pair selection.'}
    assert len(pairs) == 72
    save_equal(R / 'preparation_plan.json', plan)
    save_equal(R / 'control_provenance.json', {'copied_controls': provenance, 'G0_dict_exact': True,
                                            'G0_final_prompts_exact': True, 'fresh_native_queries': 0})
    save_equal(R / 'reference_seals.json', {
        'original_G_result_seal': {'path': str((PRIOR / 'result_seal.json').relative_to(ROOT)), 'sha256': sha(PRIOR / 'result_seal.json')},
        'original_G_algorithm_seal': {'path': str((PRIOR / 'algorithm_seal.json').relative_to(ROOT)), 'sha256': r.verified_prior(PRIOR / 'algorithm_seal.json')},
        'scheduler': {'path': str(r.SCHEDULER.relative_to(ROOT)), 'sha256': sha(r.SCHEDULER)}})
    r.ensure_output_directory()
    print(json.dumps({'controls_ready': True, 'pairs': 72, 'G0_packets': 72, 'native_calls': 0}))


def verify_algorithm():
    seal = read(R / 'algorithm_seal.json')
    for name, expected in seal['files'].items():
        assert sha(ROOT / name) == expected, ('algorithm changed', name)
    return sha(R / 'algorithm_seal.json')


def verify_workspaces():
    """Inspect source hashes only; never change a production or prior checkout."""
    for path in (R / 'workspace_manifests').glob('*.json'):
        record = read(path)
        root = Path(record['root'])
        assert read(root / 'pyrightconfig.json') == record['pyrightconfig']
        for filename, expected in record['sources'].items():
            assert r.h.e.digest((root / filename).read_text()) == expected, (str(path), filename)


def assert_result(result, seed):
    packet = result['packet']
    audit = packet['audit']
    extra = audit['additional_queries']
    actual = [q for q in result['actual_trace'] if q.get('method') in ['textDocument/definition', 'textDocument/references']]
    assert packet['source'].startswith(seed['source'])
    assert audit['queries'] == seed['audit']['queries'] + extra
    assert len(extra) == len(actual) <= 6 and len(audit['queries']) <= 18
    assert all(q.get('depth', 0) <= 3 for q in audit['queries'])
    assert r.h.e.token_count(packet['source']) <= 5200 and r.h.e.token_count(packet['plain']) <= 800
    for field in ['source_lines', 'base_lines']:
        for file, lines in seed['audit'][field].items():
            assert set(lines) <= set(audit[field].get(file, []))
    assert not result.get('error') and not any(q.get('error') for q in extra), 'native query failure'
    return len(actual)


def failure_trajectory(pair, error):
    job = next(j for j in r.jobs() if j['arm'] == 'H' and j['pair_id'] == pair['pair_id'])
    destination = R / 'trajectories' / f"{job['run_id']}.json"
    if destination.exists():
        prior = read(destination)
        assert prior['status'] == 'preparation_error' and prior['physical_calls'] == 0
        return prior
    first = read(ROOT / pair['shared_first']['raw_path'])
    value = {**job, 'status': 'preparation_error', 'physical_calls': 0, 'fresh_physical_calls': 0,
             'logical_stage_slots': 2, 'stage_modes': {'1': 'reused', '2': 'fresh'},
             'source_stage_ids': {'1': job['shared_first_stage_id'], '2': job['run_id'] + '_s2'},
             'first_review': first['response']['review'], 'request_count': len(first['response']['context_requests']),
             'context_requests_ignored': False, 'accepted_requests': None,
             'request_usage_status': 'preparation_failed_or_interrupted', 'native_calls_in_model_trajectory': 0,
             'completed_at': r.h.e.utc(), 'error': error, 'preparation_failure_not_retried': True}
    save(destination, value)


def prepare_one(pair, seal_hash):
    """One native invocation per pair; a durable claim prohibits any replay."""
    pair_name = pair['pair_id']
    output = R / 'preparation' / 'H' / f'{pair_name}.json'
    if output.exists():
        prior = read(output)
        assert prior['algorithm_seal_sha256'] == seal_hash, 'Prepared output belongs to a different algorithm seal.'
        if prior['status'] != 'ok':
            failure_trajectory(pair, prior['error'])
        return prior
    claim = R / 'native_claims' / f'{pair_name}.json'
    started = time.time()
    if claim.exists():
        assert read(claim)['algorithm_seal_sha256'] == seal_hash, 'Native claim belongs to a different algorithm seal.'
        error = 'Interrupted native claim without a completed result; no retry and no replacement packet.'
        trace_path = R / 'native_traces' / 'H' / f'{pair_name}.json'
        prior_trace = read(trace_path) if trace_path.exists() else None
        record = {'status': 'error', 'pair_id': pair_name, 'arm': 'H', 'algorithm_seal_sha256': seal_hash,
                  'shared_first_stage_id': pair['original_run_id'] + '_s1',
                  'first_response_sha256': pair['first_response_sha256'],
                  'native_query_count': prior_trace.get('native_query_count') if prior_trace is not None else None,
                  'attempted_native_query_count': prior_trace.get('native_query_count') if prior_trace is not None else None,
                  'observed_native_trace_count': sum(q.get('method') in ['textDocument/definition', 'textDocument/references']
                                                     for q in prior_trace['actual_trace']) if prior_trace is not None else 0,
                  'trace_complete': prior_trace.get('trace_complete', False) if prior_trace is not None else False,
                  'error': error, 'interrupted_claim': True,
                  'started_unix': started, 'ended_unix': time.time()}
        if prior_trace is not None:
            record.update(native_trace_path=str(trace_path.relative_to(ROOT)), native_trace_sha256=sha(trace_path))
        orphan = R / 'packets' / 'H' / f'{pair_name}_final.json'
        if orphan.exists():
            record.update(ineligible_orphan_packet_path=str(orphan.relative_to(ROOT)), ineligible_orphan_packet_sha256=sha(orphan))
        save(output, record)
        failure_trajectory(pair, error)
        return record
    save(claim, {'pair_id': pair_name, 'started_unix': started, 'algorithm_seal_sha256': seal_hash,
                 'first_response_sha256': pair['first_response_sha256'], 'maximum_queries': 6})
    result = None
    exception = None
    native_invoked = False
    try:
        import graph_policy
        for field, expected in pair['shared_first']['sha256'].items():
            assert sha(ROOT / pair['shared_first'][field]) == expected
        seed = read(ROOT / pair['seed_path'])
        assert sha(ROOT / pair['seed_path']) == pair['seed_sha256']
        first = read(ROOT / pair['shared_first']['raw_path'])['response']
        root = Path(read(ROOT / pair['workspace_manifest_path'])['root'])
        base_root = Path(read(ROOT / pair['base_workspace_manifest_path'])['root'])
        meta = read(R / 'cases' / f"{pair['case_id']}_{pair['world']}.json")
        scope = read(R / 'scopes.json')[meta['instance_id']]['scope']
        native_invoked = True
        result = graph_policy.followup(root, base_root, seed, scope, first['context_requests'], True, review=first)
        count = assert_result(result, seed)
    except Exception as exc:
        exception = exc
        count = None
    trace = {'actual_trace': result.get('actual_trace', []) if result else getattr(exception, 'partial_trace', []),
             'initialization': result.get('initialization') if result else getattr(exception, 'initialization', None),
             'stderr': result.get('stderr', []) if result else getattr(exception, 'stderr', []),
             'partial_queries': getattr(exception, 'partial_queries', []) if exception else [],
             'error': repr(exception) if exception else None, 'algorithm_seal_sha256': seal_hash}
    # A timeout can be recorded in query_records even though the inherited
    # Client did not append a response trace. Never fabricate the missing trace
    # or collapse request attempts into observed-response counts.
    attempted_queries = (result['packet']['audit'].get('additional_queries')
                         if result is not None and 'packet' in result
                         else getattr(exception, 'partial_queries', None))
    observed_count = sum(q.get('method') in ['textDocument/definition', 'textDocument/references']
                         for q in trace['actual_trace'])
    count = len(attempted_queries) if attempted_queries is not None else 0 if not native_invoked else None
    trace_complete = count is not None and observed_count == count
    trace.update(attempted_queries=attempted_queries, native_query_count=count, attempted_native_query_count=count,
                 observed_native_trace_count=observed_count,
                 native_query_count_source='query_records' if attempted_queries is not None else 'not_invoked' if not native_invoked else 'unknown')
    trace['trace_complete'] = trace_complete
    trace_path = R / 'native_traces' / 'H' / f'{pair_name}.json'
    save(trace_path, trace)
    record = {'status': 'error' if exception else 'ok', 'pair_id': pair_name, 'arm': 'H',
              'algorithm_seal_sha256': seal_hash, 'shared_first_stage_id': pair['original_run_id'] + '_s1',
              'first_response_sha256': pair['first_response_sha256'], 'started_unix': started,
              'ended_unix': time.time(), 'elapsed_seconds': time.time() - started,
              'native_query_count': count, 'trace_complete': trace_complete,
              'attempted_native_query_count': count,
              'observed_native_trace_count': observed_count,
              'native_query_count_source': trace['native_query_count_source'],
              'native_followup_invoked': native_invoked,
              'native_trace_path': str(trace_path.relative_to(ROOT)), 'native_trace_sha256': sha(trace_path),
              'native_trace_is_historical': False, 'error': repr(exception) if exception else None}
    if exception is None:
        packet_path = R / 'packets' / 'H' / f'{pair_name}_final.json'
        save(packet_path, result['packet'])
        record.update(packet_path=str(packet_path.relative_to(ROOT)), packet_file_sha256=sha(packet_path),
                      packet_sha256=r.packet_digest(result['packet']))
    else:
        if result is not None and 'packet' in result:
            partial = R / 'preparation_failures' / 'H' / f'{pair_name}.packet.json'
            save(partial, result['packet'])
            record.update(partial_packet_path=str(partial.relative_to(ROOT)), partial_packet_sha256=sha(partial))
        failure_trajectory(pair, repr(exception))
    save(output, record)
    print(json.dumps({'prepared': pair_name, 'status': record['status'], 'native_queries': count}), flush=True)
    return record


def native():
    """Launch only after parent seals the algorithm; do not start any models."""
    assert not (R / 'freeze.json').exists(), 'Model inputs are already frozen.'
    assert not list((R / 'actual_cli_starts').glob('*.json'))
    seal_hash = verify_algorithm()
    verify_workspaces()
    pairs = read(R / 'preparation_plan.json')['pairs']
    if (R / 'preparation_summary.json').exists():
        prior = read(R / 'preparation_summary.json')
        assert prior['algorithm_seal_sha256'] == seal_hash
        assert all((R / 'preparation' / 'H' / f"{pair['pair_id']}.json").exists() for pair in pairs)
        print(json.dumps(prior), flush=True)
        return prior
    started = time.time()
    session = str(time.time_ns())
    save(R / 'preparation_runtime' / f'{session}.started.json',
         {'started_unix': started, 'algorithm_seal_sha256': seal_hash, 'native_workers': 2,
          'pairs': len(pairs), 'explicit_native_launch': True})
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='graph-native') as pool:
        for offset in range(0, len(pairs), 2):
            rows.extend(pool.map(lambda pair: prepare_one(pair, seal_hash), pairs[offset:offset + 2]))
            gc.collect()
    assert verify_algorithm() == seal_hash
    verify_workspaces()
    summary = {'pairs': len(rows), 'statuses': dict(collections.Counter(row['status'] for row in rows)),
               'known_fresh_native_queries': sum(row['native_query_count'] or 0 for row in rows),
               'known_attempted_native_queries': sum(row['native_query_count'] or 0 for row in rows),
               'observed_native_trace_queries': sum(row.get('observed_native_trace_count', 0) for row in rows),
               'native_query_count_unknown_pairs': [row['pair_id'] for row in rows if row['native_query_count'] is None],
               'algorithm_seal_sha256': seal_hash, 'native_workers': 2,
               'elapsed_seconds_this_process': time.time() - started, 'workspaces_unchanged': True,
               'fresh_model_calls': 0, 'retry_policy': 'No native or model retry; every fixed pair retained.'}
    save(R / 'preparation_runtime' / f'{session}.finished.json', summary)
    save_equal(R / 'preparation_summary.json', summary)
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['controls', 'native'])
    parser.add_argument('--launch-native', action='store_true')
    args = parser.parse_args()
    if args.command == 'controls':
        controls()
    else:
        assert args.launch_native, 'Parent must approve the algorithm seal before actual native calls.'
        native()
