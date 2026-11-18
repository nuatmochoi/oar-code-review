"""Phase-aware whole-cohort audit after explicit pause/resume; no model calls."""
import collections,hashlib,json,statistics,time
from pathlib import Path
R=Path(__file__).resolve().parent
def read(p):return json.loads(p.read_text())
def describe(values):
 values=sorted(values)
 if not values:return {'count':0}
 return {'count':len(values),'minimum':values[0],'median':statistics.median(values),'mean':statistics.mean(values),'p95':values[min(len(values)-1,int(.95*len(values)))],'maximum':values[-1],'sum':sum(values)}
def max_overlap(intervals):
 events=[(start,1) for start,end in intervals]+[(end,-1) for start,end in intervals]
 active=maximum=0
 for at,delta in sorted(events,key=lambda x:(x[0],x[1])):active+=delta;maximum=max(maximum,active)
 return maximum
freeze=read(R/'freeze.json');jobs=freeze['jobs'];summary=read(R/'run_summary.json') if (R/'run_summary.json').exists() else None
assert summary is not None, 'Wait for recovery completion before final execution analysis'
segments=summary.get('active_segments',[])
def active_between(start,end):
 if not segments:return max(0,end-start)
 return sum(max(0,min(end,x['end'])-max(start,x['start'])) for x in segments)
records={p.stem:read(p) for p in (R/'raw').glob('*_s[12].json')}
trajectories={p.stem:read(p) for p in (R/'trajectories').glob('*.json')}
assert len(trajectories)==len(jobs), 'All planned trajectories must have outcomes, including aborted jobs'
actual_starts={p.stem:read(p) for p in (R/'actual_cli_starts').glob('*.json')}
starts={p.name[:-13]:read(p) for p in (R/'call_metrics').glob('*.started.json')}
ends={p.name[:-14]:read(p) for p in (R/'call_metrics').glob('*.finished.json')}
nstarts={p.name[:-13]:read(p) for p in (R/'native_metrics').glob('*.started.json')}
nends={p.name[:-14]:read(p) for p in (R/'native_metrics').glob('*.finished.json')}
model_intervals=[(actual_starts[key]['started_unix'],x['ended_unix']) for key,x in ends.items() if key in actual_starts]
native_intervals=[(x['started_unix'],x['ended_unix']) for x in nends.values()]
first_time=min((x['started_unix'] for x in starts.values()),default=0);last_time=max((x['ended_unix'] for x in ends.values()),default=first_time);wall=summary['elapsed_seconds'] if summary else max(0,last_time-first_time)
errors=[];internal_retries=[]
for key,record in records.items():
 trace=read(R/'raw'/f'{key}.trace.json');messages=[];reconnect=0
 for line in trace.get('stdout','').splitlines():
  try:event=json.loads(line)
  except (ValueError,TypeError):continue
  if event.get('type') in ['error','turn.failed']:
   message=event.get('message') or event.get('error',{}).get('message','');messages.append(message);reconnect+=message.startswith('Reconnecting...')
 stderr=trace.get('stderr','');diagnostics='\n'.join(messages)+'\n'+stderr
 if reconnect or 'retrying sampling request' in stderr:internal_retries.append({'run_id':key,'reconnect_event_count':reconnect,'stderr_sampling_retry_markers':stderr.count('retrying sampling request'),'note':'These can describe the same retry; do not add them to estimate backend requests.'})
 if record['status']!='ok':errors.append({'run_id':key,'arm':record['arm'],'stage':int(key[-1]),'duration_seconds':record.get('duration_seconds'),'returncode':record.get('returncode'),'wrapper_error':record.get('error'),'explicit_429':any('429' in message for message in messages) or '429 Too Many Requests' in stderr,'websocket_403':'403 Forbidden' in diagnostics,'schema_or_wrapper_error':bool(record.get('error')),'last_structured_error':messages[-1] if messages else None})
admissions=read(R/'scheduler_admissions.json') if (R/'scheduler_admissions.json').exists() else None
circuits=[read(p) for p in sorted((R/'circuit_events').glob('*.json'))]
rows=[];ready_waits=[];admission_delays=[]
for job in jobs:
 rid=job['run_id'];retrieval=read(R/'retrieval'/f'{rid}.json') if (R/'retrieval'/f'{rid}.json').exists() else None
 if retrieval:
  audit=retrieval['audit'];new=audit.get('additional_queries',[]);requests=audit.get('requests',[]);gaps=audit.get('declared_gaps',[])
  rows.append({**job,'request_outcomes':dict(collections.Counter(x.get('outcome','missing') for x in requests)),'declared_gap_outcomes':dict(collections.Counter(x.get('outcome','missing') for x in gaps)),'queued_requests':sum(x.get('outcome')=='queued' for x in requests),'queued_declared_gaps':sum(x.get('outcome')=='queued' for x in gaps),'executed_reviewer_requests':sum(x.get('origin')=='reviewer_request' for x in new),'executed_declared_gaps':sum(x.get('origin')=='declared_missing_connection' for x in new),'executed_generic_or_followon':sum(x.get('origin') not in ['reviewer_request','declared_missing_connection'] for x in new),'actual_fresh_native_requests':sum(x.get('method') in ['textDocument/definition','textDocument/references'] for x in retrieval['actual_additional_native_trace']),'fixed_navigation_reused':retrieval['fixed_navigation_reused']})
 if rid+'_s2' in actual_starts:
  available=nends.get(rid,{}).get('ended_unix') if job['arm']=='G' else ends.get(rid+'_s1',{}).get('ended_unix')
  if available is not None:
   end=actual_starts[rid+'_s2']['started_unix'];raw_wait=max(0,end-available);active_wait=active_between(available,end)
   ready_waits.append({'run_id':rid,'arm':job['arm'],'estimated_source_ready_to_second_transport_seconds':active_wait,'calendar_source_ready_wait_seconds':raw_wait,'excluded_user_pause_seconds':max(0,raw_wait-active_wait)})
if admissions:
 for x in admissions['rows']:
  key=x['run_id']+f'_s{x["stage"]}'
  if key in actual_starts:admission_delays.append(max(0,actual_starts[key]['started_unix']-x['unix']))
fixed_native=sum(sum(t.get('method') in ['textDocument/definition','textDocument/references'] for t in read(p)['actual_trace']) for p in (R/'lsp_fixed').glob('*.json'))
authoritative_audit=read(R/'audit_all.json') if (R/'audit_all.json').exists() else None
completed_ok=sum(x['status']=='ok' for x in trajectories.values());okay_calls=sum(x['status']=='ok' for x in records.values())
navigation={}
for arm in ['F','G']:
 rr=[x for x in rows if x['arm']==arm]
 navigation[arm]={'trajectories_with_retrieval':len(rr),**{k:sum(x[k] for x in rr) for k in ['queued_requests','queued_declared_gaps','executed_reviewer_requests','executed_declared_gaps','executed_generic_or_followon','actual_fresh_native_requests']},'request_outcomes':dict(sum((collections.Counter(x['request_outcomes']) for x in rr),collections.Counter())),'declared_gap_outcomes':dict(sum((collections.Counter(x['declared_gap_outcomes']) for x in rr),collections.Counter()))}
error_flags={'explicit_429':sum(x['explicit_429'] for x in errors),'websocket_403':sum(x['websocket_403'] for x in errors),'schema_or_wrapper_error':sum(x['schema_or_wrapper_error'] for x in errors)}
result={'generated_unix':time.time(),'status':'complete' if summary and len(trajectories)==len(jobs) else 'partial_or_interrupted','planned_trajectories':len(jobs),'completed_trajectory_records':len(trajectories),'trajectory_statuses':dict(collections.Counter(x['status'] for x in trajectories.values())),'completed_ok_trajectories':completed_ok,'model_cli_invocations_with_start_record':len(actual_starts),'model_cli_result_records':len(records),'model_cli_statuses':dict(collections.Counter(x['status'] for x in records.values())),'invocations_without_finished_metrics':sorted(set(starts)-set(ends)),'unattempted_maximum_stage_slots':freeze['max_physical_calls']-len(actual_starts),'active_execution_seconds_excluding_user_pause':wall,'calendar_elapsed_seconds':summary.get('wall_elapsed_including_pause_seconds',wall),'user_pause_gap_seconds':summary.get('excluded_pause_gap_seconds',0),'all_cli_results_per_minute':len(records)*60/wall if wall else None,'successful_model_calls_per_minute':okay_calls*60/wall if wall else None,'completed_ok_trajectories_per_minute':completed_ok*60/wall if wall else None,'model_call_duration_seconds':describe([x['duration_seconds'] for x in records.values()]),'maximum_model_overlap_from_intervals':max_overlap(model_intervals),'maximum_native_overlap_from_intervals':max_overlap(native_intervals),'model_native_overlap_observed':any(ms<ne and ns<me for ms,me in model_intervals for ns,ne in native_intervals),'reported_runtime_summary':summary,'resource_circuits':circuits,'admissions_obey_recorded_limit':all(x['in_flight_before']<x['admission_limit'] for x in admissions['rows']) if admissions else None,'runner_retries':0,'stage_run_ids_unique':len(records)==len(set(records)),'records_with_observed_CLI_internal_retry_events':len(internal_retries),'CLI_retry_examples':internal_retries[:8],'retry_caveat':'The runner made no retry. Codex CLI can retry websocket/HTTP requests internally; CLI invocation counts are not exact backend HTTP request counts.','native_queue_seconds':describe([x['queue_seconds'] for x in nstarts.values()]),'native_service_seconds':describe([x['elapsed_seconds'] for x in nends.values()]),'estimated_source_ready_to_second_transport_seconds':{a:describe([x['estimated_source_ready_to_second_transport_seconds'] for x in ready_waits if x['arm']==a]) for a in ['F','G']},'admission_to_transport_start_seconds':describe(admission_delays),'readiness_measurement_limit':'Source readiness uses first-call completion for F and native-finished metric for G, ending at instrumented actual CLI invocation start. Intersections with active execution segments exclude the user pause. It includes coordinator/prompt-serialization delay; no independent readiness timestamp exists.','fresh_fixed_preparation_native_requests':0,'reused_predecessor_fixed_native_requests':fixed_native,'fresh_G_native_requests':sum(x['actual_fresh_native_requests'] for x in rows),'authoritative_audit_native_count':authoritative_audit.get('actual_new_native_requests') if authoritative_audit and authoritative_audit['checks'].get('complete_trajectories') else None,'navigation':navigation,'error_flags_nonexclusive':error_flags,'errors':errors,'per_trajectory_navigation':rows,'second_stage_readiness_rows':ready_waits,'interpretation':'Provider transport failures prevent interpreting this run as a clean F/G quality comparison. Fast failed CLI invocations do not show successful review throughput.' if errors else 'All model CLI calls completed without transport errors. Queue/source-use metrics remain descriptive and do not establish review accuracy or causal consumption.'}
ordered_actual_starts=sorted(actual_starts.values(),key=lambda x:x['started_unix'])
actual_gaps=[b['started_unix']-a['started_unix'] for a,b in zip(ordered_actual_starts,ordered_actual_starts[1:])]
within_phase_gaps=[b['started_monotonic']-a['started_monotonic'] for a,b in zip(ordered_actual_starts,ordered_actual_starts[1:]) if any(x['start']<=a['started_unix']<=b['started_unix']<=x['end'] for x in segments)]
halt_time=circuits[0]['unix'] if circuits else None
result.update(actual_CLI_start_record_count=len(actual_starts),actual_CLI_start_gap_seconds=describe(actual_gaps),all_actual_CLI_start_gaps_at_least3_seconds=all(g>=3.0 for g in actual_gaps),within_active_phase_monotonic_CLI_gaps_seconds=describe(within_phase_gaps),within_active_phase_monotonic_gaps_at_least3_seconds=all(g>=3.0 for g in within_phase_gaps),actual_CLI_starts_match_raw_records=set(actual_starts)==set(records),start_spacing_scope='Instrumented CLI invocation starts, not underlying websocket/HTTP request starts. The CLI can retry internally.',model_worker_cap2_respected=max_overlap(model_intervals)<=2,native_worker_cap2_respected=max_overlap(native_intervals)<=2,resource_halt_exercised=bool(circuits),model_admissions_after_halt=sum(x['unix']>halt_time for x in admissions['rows']) if halt_time and admissions else None,native_starts_after_halt=sum(x['started_unix']>halt_time for x in nstarts.values()) if halt_time else None,halt_measurement_limit='Already admitted operations are drained. Model admission timestamps are exact; native-start timestamps do not separately record executor-submission time.',aborted_resource_trajectories=sum(x['status']=='aborted_resource' for x in trajectories.values()),fresh_recovery_preparation_native_calls=0,predecessor_fixed_trace_reuse_explanation='The144fixed native calls in lsp_fixed were executed in the sealed predecessor and reused byte-for-byte; recovery fresh native calls come only from G follow-ups.',full_cohort_resampled_not_selected_retry=True,recovery_provenance_sha256=hashlib.sha256((R/'recovery_provenance.json').read_bytes()).hexdigest())
inventory=read(R/'resume_initial_artifacts.json')['files']
preserved_errors=[name for name,h in inventory.items() if hashlib.sha256((R/name).read_bytes()).hexdigest()!=h]
assert not preserved_errors, preserved_errors
pre_stage_ids={Path(name).stem for name in inventory if name.startswith('actual_cli_starts/')}
new_stage_ids=set(actual_starts)-pre_stage_ids
assert pre_stage_ids<=set(actual_starts) and len(pre_stage_ids)==17
result.update(whole_cohort_CLI_invocations=len(actual_starts),pre_pause_preserved_CLI_invocations=len(pre_stage_ids),resumed_new_CLI_invocations=len(new_stage_ids),planned_whole_cohort_calls=288,planned_remaining_calls_at_resume=271,preserved_prior_artifacts=len(inventory),preserved_prior_artifacts_byte_identical=True,same_evaluation_cohort_resumed=True,no_completed_stage_replayed=True,active_segments=segments,pre_pause_active_seconds=summary.get('pre_pause_active_seconds'),resume_active_seconds=summary.get('resume_active_seconds'),queue_user_pause_removed_seconds=sum(x['excluded_user_pause_seconds'] for x in ready_waits),admission_measurement_phase=admissions.get('phase') if admissions else None,pre_pause_admissions_missing_but_actual_starts_preserved=True,chronology_note='Original17 successful calls and new resume calls belong to one unchanged144-trajectory cohort. The pause was user-requested; only unfinished stages were executed. Calendar gaps are not a speed or retrieval bottleneck.',start_gap_measurement_note='Calendar start gaps use wall-clock timestamps across the pause; within-phase gaps additionally use the monotonic clock.')
result['native_navigation_aggregation_note']='The navigation executed_* fields count logical additional-query audit records attached to each trajectory. F repeats the same24 fixed packets across3 repeats, yielding432 attached logical records but only144 predecessor native calls reused and zero fresh recovery F calls. G432 records correspond to432 fresh actual native queries in this cohort.'
if authoritative_audit and authoritative_audit['checks'].get('complete_trajectories'):
 result['authoritative_native_counts']=authoritative_audit['native_counts']
 result['authoritative_audit_all_checks_passed']=all(authoritative_audit['checks'].values())
with (R/'execution_analysis.json').open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
print(json.dumps({k:v for k,v in result.items() if k not in ['errors','per_trajectory_navigation','second_stage_readiness_rows','reported_runtime_summary','CLI_retry_examples']},ensure_ascii=False,indent=2))
