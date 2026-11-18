"""Post-run telemetry only; never imports the experiment or invokes a model.

This file is deliberately outside the pre-call algorithm seal. It reads the
frozen manifests and append-only operational records without modifying them.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics

R = Path(__file__).resolve().parent
ROOT = R.parents[1]


def read(path):
    return json.loads(Path(path).read_text())


def utc():
    return datetime.now(timezone.utc).isoformat()


def unix(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stats(values):
    values = sorted(values)
    if not values:
        return {'n': 0, 'sum': 0, 'minimum': None, 'median': None, 'mean': None, 'p95': None, 'maximum': None}
    return {'n': len(values), 'sum': sum(values), 'minimum': values[0],
            'median': statistics.median(values), 'mean': statistics.mean(values),
            'p95': values[min(len(values) - 1, int((len(values) - 1) * .95))], 'maximum': values[-1]}


def maximum_overlap(intervals):
    events = sorted([(start, 1) for start, end in intervals] + [(end, -1) for start, end in intervals])
    active = maximum = 0
    for _, change in events:
        active += change
        maximum = max(maximum, active)
    return maximum


def union_duration(intervals):
    total = 0.0
    current = None
    for start, end in sorted(intervals):
        if current is None:
            current = [start, end]
        elif start <= current[1]:
            current[1] = max(current[1], end)
        else:
            total += current[1] - current[0]
            current = [start, end]
    return total + (current[1] - current[0] if current else 0)


def intersect_duration(start, end, segments):
    return sum(max(0, min(end, b) - max(start, a)) for a, b in segments)


def usage_summary(records):
    totals = collections.Counter()
    missing = []
    events = 0
    for key, record in records.items():
        usages = record.get('usage') or []
        if not usages:
            missing.append(key)
        for row in usages:
            events += 1
            for name, value in row.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[name] += value
    return {'records': len(records), 'records_with_usage': len(records) - len(missing),
            'usage_events': events, 'missing_usage_stage_ids': missing, 'reported_token_fields': dict(totals),
            'reported_input_plus_output_tokens': totals['input_tokens'] + totals['output_tokens'],
            'interpretation': 'Sum of reported turn.completed usage events. Cached input is part of input, and reasoning output is part of output; these subfields are not added again.'}


def snapshot():
    rows = [read(p) for p in (R / 'trajectories').glob('*.json')]
    raw = [read(p) for p in (R / 'raw').glob('*_s[12].json')]
    return {'at': utc(), 'trajectory_statuses': dict(collections.Counter(x['status'] for x in rows)),
            'completed_trajectories': len(rows), 'actual_CLI_entries': len(list((R / 'actual_cli_starts').glob('*.json'))),
            'persisted_fresh_raw_statuses': dict(collections.Counter(x['status'] for x in raw)),
            'circuit_events': len(list((R / 'circuit_events').glob('*.json'))),
            'final_summary_present': (R / 'run_summary.json').exists()}


def analyze():
    manifest = read(R / 'stage_reuse_manifest.json')
    frozen = read(R / 'freeze.json')
    summary = read(R / 'run_summary.json')
    jobs = frozen['jobs']
    trajectories = {j['run_id']: read(R / 'trajectories' / f"{j['run_id']}.json") for j in jobs}
    assert len(trajectories) == len(jobs) == 216, 'Wait for all trajectory outcomes.'
    assert not (R / 'runner.lock').exists(), 'Wait for the runner to exit.'
    entries = manifest['entries']
    starts = {p.stem: read(p) for p in (R / 'actual_cli_starts').glob('*.json')}
    metrics = {p.name.removesuffix('.finished.json'): read(p) for p in (R / 'call_metrics').glob('*.finished.json')}
    raw = {p.stem: read(p) for p in (R / 'raw').glob('*_s[12].json')}
    inputs = {p.stem: read(p) for p in (R / 'inputs').glob('*.json')}
    stage_started = {p.name.removesuffix('.started.json'): read(p) for p in (R / 'call_metrics').glob('*.started.json')}
    reused = {key: read(ROOT / entry['raw_path']) for key, entry in entries.items() if entry['mode'] == 'reused'}
    admissions = [read(p) for p in (R / 'admissions').rglob('*.json')]
    sessions = sorted([read(p) for p in (R / 'runtime').glob('*.finished.json')], key=lambda x: x['started_unix'])
    session_starts = [read(p) for p in (R / 'runtime').glob('*.started.json')]
    assert len(sessions) == len(session_starts), 'An operational phase has no completion record.'
    segments = [(x['started_unix'], x['ended_unix']) for x in sessions]
    active = sum(b - a for a, b in segments)
    calendar = segments[-1][1] - segments[0][0]
    paused = calendar - union_duration(segments)

    # The shared transport returned no raw/trace for the six missing-directory
    # failures. Their end is only bounded by coordinator failure completion.
    exact_intervals = []
    bounded_intervals = []
    missing_raw = []
    transport_latency = []
    for key, start in starts.items():
        if key in metrics:
            end = metrics[key]['ended_unix']
            exact_intervals.append((start['started_unix'], end))
            transport_latency.append(end - start['started_unix'])
        else:
            job = entries[key]['job']
            end = unix(trajectories[job['run_id']]['completed_at'])
            missing_raw.append({'logical_stage_id': key, 'started_unix': start['started_unix'],
                                'coordinator_failure_completed_unix_upper_bound': end,
                                'trajectory_status': trajectories[job['run_id']]['status']})
        bounded_intervals.append((start['started_unix'], end))
    per_session = []
    within_session_gaps = []
    for session in sessions:
        rows = sorted([s for s in starts.values() if s['session_id'] == session['session_id']], key=lambda x: x['started_monotonic'])
        gaps = [b['started_monotonic'] - a['started_monotonic'] for a, b in zip(rows, rows[1:])]
        within_session_gaps.extend(gaps)
        per_session.append({**session, 'actual_CLI_entry_count': len(rows), 'CLI_start_spacing_seconds': stats(gaps)})
    chronological = sorted(starts.values(), key=lambda x: x['started_unix'])
    all_wall_gaps = [b['started_unix'] - a['started_unix'] for a, b in zip(chronological, chronological[1:])]
    cross_session_gaps = [b['started_unix'] - a['started_unix'] for a, b in zip(chronological, chronological[1:]) if a['session_id'] != b['session_id']]

    # Hash every frozen input/algorithm and all original files actually reused.
    hash_cache = {}
    hash_errors = []
    def verify(path, expected, category):
        path = Path(path)
        if path not in hash_cache:
            hash_cache[path] = sha(path) if path.exists() else None
        if hash_cache[path] != expected:
            hash_errors.append({'category': category, 'path': str(path), 'expected': expected, 'actual': hash_cache[path]})
    for name in ['algorithm_seal.json', 'freeze.json']:
        for path, expected in read(R / name)['files'].items():
            verify(ROOT / path, expected, name)
    original_paths = set()
    for key, entry in entries.items():
        if entry['mode'] == 'reused':
            for field, expected in entry['original_file_sha256'].items():
                path = ROOT / entry[field]
                original_paths.add(path)
                verify(path, expected, 'original_reused_artifact')
            verify(ROOT / entry['source_result_seal_path'], entry['source_result_seal_sha256'], 'original_result_seal')
        verify(ROOT / entry['logical_packet_path'], entry['logical_packet_file_sha256'], 'logical_packet')
    checkpoint_count = 0
    final_packet_ready = {}
    for path in (R / 'checkpoints').rglob('*.json'):
        checkpoint_count += 1
        checkpoint_record = read(path)
        if checkpoint_record['event'] in ['final_packet_ready', 'restored_first_and_final_packet']:
            rid = checkpoint_record['run_id']
            ready_time = unix(checkpoint_record['created_at'])
            final_packet_ready[rid] = min(ready_time, final_packet_ready.get(rid, ready_time))
        for rel, expected in checkpoint_record['files'].items():
            verify(R / rel, expected, 'append_only_checkpoint')
    for key, metric in metrics.items():
        verify(R / 'raw' / f'{key}.json', metric['raw_record_sha256'], 'metric_raw')
        verify(R / 'raw' / f'{key}.trace.json', metric['trace_sha256'], 'metric_trace')
    input_binding_errors = []
    for key, inp in inputs.items():
        actual_prompt = hashlib.sha256(inp['prompt'].encode()).hexdigest()
        expected = entries[key].get('expected_prompt_sha256')
        if actual_prompt != inp['prompt_sha256'] or (expected is not None and actual_prompt != expected):
            input_binding_errors.append(key)
        if key in raw and raw[key]['prompt_sha256'] != actual_prompt:
            input_binding_errors.append(key)
    admission_keys = [f"{x['run_id']}_s{x['stage']}" for x in admissions]
    admission_counts = collections.Counter(admission_keys)
    planned_fresh = {key for key, entry in entries.items() if entry['mode'] == 'fresh'}
    unattempted = sorted(planned_fresh - set(starts))
    checks = {
        'all_frozen_and_checkpoint_hashes_match': not hash_errors,
        'all_known_input_prompt_bindings_match': not input_binding_errors,
        'logical_slots_equal_432': len(entries) == 432,
        'reused_slots_equal_141': len(reused) == 141,
        'fresh_planned_slots_equal_291': len(planned_fresh) == 291,
        'fresh_started_only_planned_stages': set(starts) <= planned_fresh,
        'no_fresh_artifacts_created_for_reused_stages': not (set(reused) & (set(starts) | set(inputs) | set(raw))),
        'no_duplicate_stage_admissions': max(admission_counts.values(), default=0) <= 1,
        'every_actual_start_has_input_and_stage_start': set(starts) <= (set(inputs) & set(stage_started)),
        'every_persisted_raw_has_start_and_finished_metric': set(raw) <= (set(starts) & set(metrics)),
        'model_overlap_upper_bound_at_most_two': maximum_overlap(bounded_intervals) <= 2,
        'minimum_within_session_monotonic_start_gap_at_least_3s': all(x >= 3 - 1e-6 for x in within_session_gaps),
        'all_global_wall_start_gaps_at_least_3s': all(x >= 3 - 1e-3 for x in all_wall_gaps),
        'all_admissions_limit_two': all(x['admission_limit'] == 2 and x['in_flight_before'] < 2 for x in admissions),
        'all_logical_trajectory_outcomes_retained': len(trajectories) == 216,
        'summary_counts_agree': summary['fresh_CLI_invocations'] == len(starts) and summary['fresh_raw_records'] == len(raw),
    }
    retrievals = [read(p) for p in (R / 'retrieval').glob('*.json')]
    fresh_native_trace_count = sum(
        event.get('method') in ['textDocument/definition', 'textDocument/references']
        for record in retrievals for event in record.get('actual_additional_native_trace', []))
    checks['no_fresh_native_retrieval_trace'] = fresh_native_trace_count == 0

    trace_diagnostics = []
    resource_pattern = re.compile(r'\b429\b|\brate_limit_exceeded\b|\bRESOURCE_EXHAUSTED\b|\bENOMEM\b|\bEMFILE\b|Cannot allocate memory|Too many open files', re.I)
    retry_pattern = re.compile(r'reconnecting|retrying|retry limit|retry attempt', re.I)
    for key, record in raw.items():
        trace = read(R / 'raw' / f'{key}.trace.json')
        errors = []
        event_counts = collections.Counter()
        for line in trace.get('stdout', '').splitlines():
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            event_counts[event.get('type', 'missing')] += 1
            if event.get('type') in ['error', 'turn.failed']:
                errors.append(json.dumps(event))
        diagnostic = trace.get('stderr', '') + '\n' + '\n'.join(errors)
        resources = resource_pattern.findall(diagnostic)
        retries = retry_pattern.findall(diagnostic)
        if errors or resources or retries or record['status'] != 'ok':
            trace_diagnostics.append({'stage_id': key, 'status': record['status'], 'structured_error_events': len(errors),
                                      'resource_signal_matches': resources, 'retry_marker_matches': retries,
                                      'event_counts': dict(event_counts)})
    circuits = [read(p) for p in (R / 'circuit_events').glob('*.json')]
    admissions_after_halt = [x for x in admissions if circuits and x['unix'] > min(c['unix'] for c in circuits)]
    checks['no_new_admission_after_resource_halt'] = not admissions_after_halt

    final_queue = []
    source_ready_queue = []
    final_packet_materialization = []
    for job in jobs:
        first_key, final_key = job['run_id'] + '_s1', job['run_id'] + '_s2'
        if first_key in metrics and final_key in starts:
            begin, end = metrics[first_key]['ended_unix'], starts[final_key]['started_unix']
            final_queue.append({'run_id': job['run_id'], 'wall_seconds': end - begin,
                                'active_seconds': intersect_duration(begin, end, segments)})
        if job['run_id'] in final_packet_ready and final_key in starts:
            begin, end = final_packet_ready[job['run_id']], starts[final_key]['started_unix']
            source_ready_queue.append({'run_id': job['run_id'], 'wall_seconds': end - begin,
                                       'active_seconds': intersect_duration(begin, end, segments)})
            if first_key in metrics:
                final_packet_materialization.append(begin - metrics[first_key]['ended_unix'])
    per_arm = {}
    for arm in ['B0', 'B1', 'B2']:
        keys = {key for key, entry in entries.items() if entry['job']['arm'] == arm}
        arm_raw = {key: rec for key, rec in raw.items() if key in keys}
        arm_reused = {key: rec for key, rec in reused.items() if key in keys}
        per_arm[arm] = {
            'logical_trajectories': sum(j['arm'] == arm for j in jobs), 'logical_stage_slots': len(keys),
            'planned_fresh_stage_slots': len(keys & planned_fresh), 'reused_stage_slots': len(keys & set(reused)),
            'fresh_CLI_entries': len(keys & set(starts)), 'persisted_fresh_raw_records': len(arm_raw),
            'lost_raw_after_entry': len(keys & (set(starts) - set(raw))),
            'unattempted_planned_stages': sorted(keys & set(unattempted)),
            'trajectory_statuses': dict(collections.Counter(t['status'] for t in trajectories.values() if t['arm'] == arm)),
            'fresh_attempted_prompt_token_estimates': sum(inputs[k]['prompt_tokens'] for k in keys & set(starts)),
            'fresh_persisted_usage': usage_summary(arm_raw), 'inherited_usage_for_logical_slots': usage_summary(arm_reused),
        }
    prep = read(R / 'preparation_summary.json')
    successful = sum(x['status'] == 'ok' for x in raw.values())
    result = {
        'created_at': utc(), 'analysis_scope': 'Post-hoc operational telemetry; no model calls, outcome relabeling, or frozen input changes.',
        'source_summary_sha256': sha(R / 'run_summary.json'),
        'integrity': {'checks': checks, 'all_checks_passed': all(checks.values()), 'hash_errors': hash_errors,
                      'input_binding_errors': input_binding_errors, 'unique_hashed_files': len(hash_cache),
                      'original_reused_artifact_paths_verified': len(original_paths), 'checkpoints_verified': checkpoint_count},
        'counts': {'logical_trajectories': 216, 'logical_stage_slots': len(entries),
                   'planned_fresh_stage_slots': len(planned_fresh), 'reused_stage_slots': len(reused),
                   'unique_original_stage_ids_reused': len({entries[k]['original_stage_id'] for k in reused}),
                   'fresh_actual_CLI_entries': len(starts), 'persisted_fresh_raw_records': len(raw),
                   'lost_raw_after_CLI_entry': len(set(starts) - set(raw)), 'unattempted_planned_stages': unattempted,
                   'available_logical_raw_records': len(raw) + len(reused),
                   'missing_logical_raw_stage_ids': sorted(set(entries) - set(raw) - set(reused)),
                   'fresh_raw_statuses': dict(collections.Counter(x['status'] for x in raw.values())),
                   'trajectory_statuses': dict(collections.Counter(x['status'] for x in trajectories.values()))},
        'per_arm': per_arm,
        'timing': {'active_elapsed_seconds': active, 'calendar_elapsed_seconds': calendar,
                   'operational_pause_excluded_seconds': paused, 'sessions': per_session,
                   'active_time_scope': 'Sum of runner started-to-finished phase intervals; excludes the drained repair gap and pre-run preparation/freeze checks.',
                   'successful_persisted_fresh_calls_per_active_minute': successful * 60 / active,
                   'actual_CLI_entries_per_active_minute': len(starts) * 60 / active,
                   'completed_logical_reviews_per_active_minute': len(trajectories) * 60 / active,
                   'logical_review_rate_caveat': 'Includes reused results and failures, so it is not new-model throughput.',
                   'persisted_call_CLI_entry_to_finished_metric_seconds': stats(transport_latency),
                   'first_finish_to_final_CLI_entry_wall_seconds': stats([x['wall_seconds'] for x in final_queue]),
                   'first_finish_to_final_CLI_entry_active_seconds': stats([x['active_seconds'] for x in final_queue]),
                   'final_queue_scope': 'Only trajectories with a fresh persisted first call; historical B2 first timestamps are excluded.',
                   'packet_ready_to_final_CLI_entry_wall_seconds': stats([x['wall_seconds'] for x in source_ready_queue]),
                   'packet_ready_to_final_CLI_entry_active_seconds': stats([x['active_seconds'] for x in source_ready_queue]),
                   'fresh_first_finish_to_packet_ready_seconds': stats(final_packet_materialization),
                   'source_ready_scope': 'Uses the first current-cohort packet-ready checkpoint, including current-cohort restoration of B2 historical first responses; excludes the repair gap from active waits.'},
        'parallelism': {'configured_model_workers': 2, 'fresh_native_workers': 0,
                        'max_exact_persisted_model_overlap': maximum_overlap(exact_intervals),
                        'max_all_model_overlap_using_failure_completion_upper_bounds': maximum_overlap(bounded_intervals),
                        'max_started_metric_in_flight': max((m['in_flight_at_start'] for m in stage_started.values()), default=0),
                        'actual_CLI_start_monotonic_spacing_seconds_within_sessions': stats(within_session_gaps),
                        'all_CLI_start_wall_spacing_seconds': stats(all_wall_gaps),
                        'cross_session_start_wall_gaps_seconds': cross_session_gaps,
                        'timing_scope': 'CLI invocation start, not provider HTTP request start. Lost calls have no exact finish metric; coordinator failure completion supplies an upper bound.',
                        'source_preparation_in_model_slots': False,
                        'known_model_interval_seconds_per_two_worker_active_capacity': sum(b - a for a, b in exact_intervals) / (2 * active),
                        'model_interval_fraction_with_lost_finish_upper_bounds': sum(b - a for a, b in bounded_intervals) / (2 * active),
                        'speedup_claim': 'No causal speedup claim; this cohort uses fewer new calls and different contexts.'},
        'errors_and_retries': {'runner_retry_count': sum(max(0, count - 1) for count in admission_counts.values()),
                               'resource_circuit_count': len(circuits), 'resource_circuits': circuits,
                               'admissions_after_resource_halt': admissions_after_halt,
                               'persisted_trace_diagnostics': trace_diagnostics,
                               'lost_response_attempts': missing_raw,
                               'lost_attempts_provider_status_usage_internal_retries': 'Unknown; no raw or trace survives. No same-ID reissue within this primary cohort; a separately named recovery cohort is outside this analysis.',
                               'CLI_internal_retry_observability': 'Reported markers cover persisted stderr and structured error events only. Absence of markers does not prove the provider received only one HTTP request.'},
        'tokens': {'fresh_attempted_prompt_estimate_records': len(starts),
                   'fresh_attempted_prompt_token_estimate_total': sum(inputs[k]['prompt_tokens'] for k in starts),
                   'fresh_persisted_prompt_token_estimate_total': sum(x['prompt_tokens'] for x in raw.values()),
                   'lost_attempt_prompt_token_estimate_total': sum(inputs[k]['prompt_tokens'] for k in set(starts) - set(raw)),
                   'inherited_prompt_token_estimate_total': sum(x['prompt_tokens'] for x in reused.values()),
                   'fresh_persisted_usage': usage_summary(raw), 'inherited_usage_not_new_spend': usage_summary(reused),
                   'available_logical_slot_usage': usage_summary({**raw, **reused}),
                   'usage_unknown_after_entry': sorted(set(starts) - set(raw)),
                   'interpretation': 'prompt_tokens is the local experiment tokenizer estimate, not provider billed input. Provider usage includes other CLI/schema overhead. This run usage excludes inherited usage, and total actual cost cannot be recovered for six lost calls.'},
        'native_context': {'fresh_native_queries': 0,
                           'fresh_queries_in_persisted_retrieval_traces': fresh_native_trace_count,
                           'retrieval_records': len(retrievals),
                           'historical_cached_seed_queries_by_world': prep['cached_seed_native_queries_by_world'],
                           'historical_cached_fixed_followup_queries_by_world': prep['cached_fixed_followup_native_queries_by_world'],
                           'historical_count_scope': 'Counted once per prepared world, not multiplied by repetitions or logical reused model slots.',
                           'B2_changed_worlds': prep['B2_changed_worlds'],
                           'B2_unchanged_final_worlds': prep['B2_final_identical_worlds']},
        'interpretation_limits': [
            'All 216 trajectories remain in the primary outcome denominator, including six infrastructure failures.',
            'B2 shares 141 exact historical F stage responses; these are not independent fresh model observations.',
            'F/G and reused B2 responses are a previous chronological cohort; B0/B1 and three planned changed B2 finals are later.',
            'Only N10_a changes B2 final packing, a safe world. Two of its three planned fresh final attempts were lost to the directory failure. Actual surviving raw status and usage are reported separately.',
            'The missing-directory operational repair created raw/ only. It did not change sealed algorithm files or replay lost stage IDs within this primary cohort. Separately named recovery measurements are excluded here.',
        ],
    }
    if (R / 'operational_repair_001.json').exists():
        result['operational_repair'] = read(R / 'operational_repair_001.json')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', action='store_true')
    args = parser.parse_args()
    if args.snapshot:
        print(json.dumps(snapshot(), ensure_ascii=False))
    else:
        result = analyze()
        output = R / 'execution_analysis.json'
        with output.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps({'path': str(output), 'counts': result['counts'],
                          'all_integrity_checks_passed': result['integrity']['all_checks_passed'],
                          'active_seconds': result['timing']['active_elapsed_seconds']}, ensure_ascii=False))
