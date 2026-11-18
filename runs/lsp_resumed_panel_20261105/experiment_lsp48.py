"""SCIP-free fixed/body-control/condition-bound LSP comparison, no hidden retries."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import argparse,copy,json,random,re,time
import jsonschema
from common import ROOT,PREVIOUS,PREVIOUS_METHOD,ARMS,REPEATS,WORKERS,BUDGETS,load,sha,digest,write_new,source_tokens
from schemas import REVIEW as BASE_REVIEW
from warning_schema import with_warning_dispositions
from warning_tracker import WarningTracker
from condition_roles import with_condition_roles
REVIEW=with_condition_roles(BASE_REVIEW)
from planner_schema import PLAN
from frontier import Frontier
from diff48 import diff_evidence
from prompts_lsp48 import planning_prompt,final_prompt,changed_operations,summarize_answer
from transport48 import complete,admission,parse_payload,total_usage

PLAN_JSON_REFERENCE_CAP=4000
FUTURE_SOURCE_AND_STATE_RESERVE=14000

def baseline_path(case,repeat):return PREVIOUS/'results/reviews'/f'{case["id"]}_B2_r{repeat}'/'review.result.json'
def context_for(case):return load(ROOT/'contexts'/case['id']/'context_LSP.json')
def evidence_for(case,context):return list(context['evidence'])+diff_evidence(case['diff'])
def provider_ref(path,label):
 p=Path(path)
 if(p.parent/'reused.actual.json').exists():
  ref=load(p.parent/'reused.actual.json');assert sha(ref['provider_result'])==ref['provider_sha256'];p=Path(ref['provider_result'])
 r=load(p)
 return {'stage':label,'reference':str(p),'sha256':sha(p),'physical_attempt_count':r.get('physical_attempt_count',0),
  'provider_tokens':total_usage(r),'status':r.get('status'),'timing':r.get('timing',{})}
def valid_payload(provider,schema):
 p=parse_payload(provider)
 if p is None:return None
 try:jsonschema.validate(p,schema)
 except jsonschema.ValidationError:return None
 return p

def planning_view(case,base):
 """Complete referenced file sections, not clipped hunks or phantom ID spans.

 Every structured source/DIFF ID from the complete prior payload must remain
 visible. If the projection cannot ensure that, use the full input instead.
 Candidate-free exploration retains the full diff rather than hiding changes.
 """
 es=base['evidence'];byid={e['id']:e for e in es};payload=base.get('payload')or{}
 def strings(v):
  if isinstance(v,str):yield v
  elif isinstance(v,dict):
   for x in v.values():yield from strings(x)
  elif isinstance(v,list):
   for x in v:yield from strings(x)
 def explicit_refs(v):
  if isinstance(v,dict):
   for k,x in v.items():
    if k=='evidence_id'and isinstance(x,str)and x:yield x
    elif k=='evidence_ids'and isinstance(x,list):yield from(e for e in x if isinstance(e,str)and e)
    else:yield from explicit_refs(x)
  elif isinstance(v,list):
   for x in v:yield from explicit_refs(x)
 refs={v for v in strings(payload)if v in byid};unknown=sorted(set(explicit_refs(payload))-set(byid))
 common_scope={'all_known_prior_evidence_ids_visible':True,'all_structured_prior_evidence_ids_visible':not unknown,'unknown_prior_evidence_ids':unknown}
 changed=set(case['changed_files']);paths={byid[v]['path']for v in refs if byid[v]['kind']=='diff_hunk'or byid[v]['path']in changed}
 for finding in payload.get('findings',[]):
  locations=[finding.get('regression_anchor'),finding.get('failure_operation'),*finding.get('supporting_evidence',[])]
  paths.update(v['path']for v in locations if isinstance(v,dict)and v.get('path')in changed)
 sections={e['content']for e in es if e['kind']=='diff_hunk'and e['path']in paths}
 if not payload.get('claims')or not sections:return case,base,{'mode':'full_diff',**common_scope}
 # DIFF citation content excludes one terminal newline. Reuse the original
 # complete section slices so adjacent file headers never merge on projection.
 raw_sections=re.split(r'(?=^diff --git )',case['diff'],flags=re.MULTILINE)
 ordered=[s for s in raw_sections if s.removesuffix('\n')in sections]
 if{s.removesuffix('\n')for s in ordered}!=sections:return case,base,{'mode':'full_diff_projection_sections_not_exact',**common_scope}
 visible=[e for e in es if e['kind']!='diff_hunk'or e['content']in sections]
 if not refs<={e['id']for e in visible}:return case,base,{'mode':'full_diff_projection_not_safe',**common_scope}
 actual_paths={e['path']for e in visible if e['kind']=='diff_hunk'}
 scope={'mode':'complete_referenced_file_diff_sections','full_original_diff_sha256':case['diff_reference']['sha256'],
  'selected_paths':sorted(actual_paths),'omitted_paths':sorted(changed-actual_paths),**common_scope,
  'reference_ids':sorted(refs),'no_individual_file_section_truncated':True,'full_original_diff_is_restored_for_final':True}
 return {**case,'diff':''.join(ordered),'planning_view':scope},{**base,'evidence':visible},scope

def initial_frontier(case,base):
 evidence=base['evidence']
 # Exploration is forbidden while prior candidates exist. Do not serialize a
 # large unused operation inventory into those candidate-bound planning calls.
 ops=[]if(base.get('payload')or{}).get('claims')else changed_operations(case,evidence)
 f=Frontier(base.get('payload'),evidence,changed_operations=ops);summaries=[]
 # Only old fixed LSP query receipts, never the SCIP export or later queries.
 for p in sorted((PREVIOUS/'contexts'/case['id']/'raw').glob('query-*.json')):
  a=load(p);ids=set(a.get('selected_evidence_ids',[])+a.get('already_available_evidence_ids',[])+[e['id']for e in a.get('evidence',[])])
  if not ids<=set(f.evidence):continue
  a={**a,'query_receipt':str(p)};f.register_cached_answer(a['question'],a);summaries.append(summarize_answer({**a,'all_answer_evidence_ids':sorted(ids),'execution':'already_executed_fixed_LSP_lookup','cache_hit':True}))
 return f,summaries

def planning_inputs(case,repeat):
 base=load(baseline_path(case,repeat));ctx=context_for(case);view,pbase,scope=planning_view(case,base);f,cached=initial_frontier(view,pbase);f.planning_scope=scope
 raw=planning_prompt(view,pbase['evidence'],base.get('payload'),f,cached)
 empty={'accepted':True,'validation':None,'controller_update':None,'coverage':[],'answers':[],'new_evidence_ids':[],'selected_evidence_ids':[],'backend_query_attempts':0,'cache_hits':0,'semantic_truth_guaranteed':False}
 future=final_prompt(case,base['evidence'],base.get('payload'),None,empty,f.export(),{'scope':'Prospective exact base final, with separate source/PLAN/query reserve.'})
 final_reserve=admission(future,REVIEW)['required_reserve']+FUTURE_SOURCE_AND_STATE_RESERVE
 return base,ctx,f,cached,raw,final_reserve

def shared_plan(case,repeat):
 folder=ROOT/'results/plans'/f'{case["id"]}_r{repeat}';out=folder/'plan.result.json'
 if out.exists():return load(out)
 base,ctx,f,cached,raw,reserve=planning_inputs(case,repeat)
 bpath=baseline_path(case,repeat);bref={'path':str(bpath),'sha256':sha(bpath)}
 if base['report_status']!='ok'or not base.get('final_produced'):
  row={'case_id':case['id'],'repeat':repeat,'status':'not_reached','reason':'fixed_initial_final_not_available','baseline_reference':bref,'provider_reference':None,'payload':None,'validation':None,'stages':[],'no_model_retry':True}
 else:
  visible=list(f.evidence.values())
  write_new(folder/'planner.delivered_evidence.json',{'evidence':visible,'evidence_ids':[e['id']for e in visible],'evidence_sha256':digest(visible),'frontier':f.export(),'cached_answers':cached,'source_tokens':source_tokens(visible),'actual_initial_is_final':True,'planning_scope':f.planning_scope})
  response=complete(folder,'planner',raw,PLAN,used_tokens=base['actual_model_tokens'],used_calls=base['logical_model_calls'],remaining_wall=max(0,900-base['active_wall_seconds']),final_reserve=reserve)
  pref=folder/'planner/initial.result.json';p=valid_payload(response,PLAN);error=None
  if p is not None:
   from oar_core import count_reference_tokens
   if count_reference_tokens(response['raw_response'])>PLAN_JSON_REFERENCE_CAP:error='planner_state_reference_cap_exceeded';p=None
  validation=f.validate_plan(p)if p is not None else None
  row={'case_id':case['id'],'repeat':repeat,'status':'ok'if p is not None and validation['accepted']else'invalid_or_unattempted','reason':error,
   'baseline_reference':bref,'provider_reference':{'path':str(pref),'sha256':sha(pref)},'payload':p,'raw_payload':parse_payload(response,require_ok=False),'validation':validation,
   'stages':[provider_ref(pref,'shared_planner')],'final_reserve':reserve,'initial_frontier':f.export(),'planning_scope':f.planning_scope,'no_model_retry':True}
 write_new(out,row);print(json.dumps({'plan':case['id'],'repeat':repeat,'status':row['status']}),flush=True);return row

def safe_shared_plan(case,repeat):
 try:return shared_plan(case,repeat)
 except Exception as e:
  folder=ROOT/'results/plans'/f'{case["id"]}_r{repeat}';p=folder/'plan.result.json'
  if p.exists():return load(p)
  canonical=folder/'planner/initial.result.json';stages=[provider_ref(canonical,'shared_planner')]if canonical.exists()else[]
  row={'case_id':case['id'],'repeat':repeat,'status':'runner_exception','reason':type(e).__name__+': '+str(e),'baseline_reference':{'path':str(baseline_path(case,repeat)),'sha256':sha(baseline_path(case,repeat))},'provider_reference':{'path':str(canonical),'sha256':sha(canonical)}if canonical.exists()else None,'payload':None,'validation':None,'stages':stages,'no_model_retry':True}
  if(folder/'planner.admission.json').exists()and load(folder/'planner.admission.json')['admitted']and not canonical.exists():row['model_issue_state_unknown']=True
  write_new(p,row);return row

def save_delivery(folder,evidence,execution,frontier,context,tracker=None):
 write_new(folder/'final.delivered_evidence.json',{'evidence':evidence,'evidence_ids':[e['id']for e in evidence],'evidence_sha256':digest(evidence),
  'source_tokens':source_tokens(evidence),'execution':execution,'context':copy.deepcopy(context),'controller_before_call':frontier.controller.export(),'frontier_before_call':frontier.export(),'warning_catalog':tracker.catalog()if tracker else None,'warning_tracker_before_call':tracker.export()if tracker else None,'not_proof_of_issuance':True})

def build_result(case,repeat,arm,base,plan,*,provider=None,delivery=None,context=None,execution=None,extra_seconds=0,exception=None,final_aux=None,warning_trace=None,literal_fact_check=None):
 rid=f'{case["id"]}_{arm}_r{repeat}';bpath=baseline_path(case,repeat);stages=copy.deepcopy(base['stages'])
 if plan:stages+=copy.deepcopy(plan.get('stages',[]))
 folder=ROOT/'results/reviews'/rid;finalp=folder/'final/initial.result.json'
 if finalp.exists():stages.append(provider_ref(finalp,'new_final'))
 new=valid_payload(provider,REVIEW)if provider else None
 delivered=bool(delivery and digest(delivery.get('evidence',[]))==delivery.get('evidence_sha256')and[e['id']for e in delivery['evidence']]==delivery.get('evidence_ids'))
 choose_new=bool(new is not None and delivered and provider.get('physical_attempt_count',0)>0)
 # Only an actually generated normal final can supersede the prior actual final.
 # The PLAN can never be selected, and collected query source cannot be grafted
 # onto the retained original report's delivered evidence.
 row=copy.deepcopy(base)
 row.update(review_id=rid,arm=arm,repeat=repeat,cohort='SCIP_free_LSP_agentic48',baseline_reference={'path':str(bpath),'sha256':sha(bpath)},initial_reference={'path':str(bpath),'sha256':sha(bpath),'shared_actual_initial_final':True},
  final_origin='new_final'if choose_new else'shared_B2_initial_final'if base.get('final_produced')else'none',fallback_to_initial=bool(arm!='FIXED'and not choose_new and base.get('final_produced')),
  final_new_generated=choose_new,method_completed=bool(arm!='FIXED'and choose_new and plan and plan['status']=='ok'and execution and execution.get('accepted')and(execution.get('validation')or{}).get('complete',False)and warning_trace and warning_trace.get('accepted')and literal_fact_check and literal_fact_check.get('accepted')and final_aux and final_aux.get('accepted')),
  query_completed=bool(arm=='AGENTIC'and execution and execution.get('accepted')and execution.get('answers')and all(a.get('execution')in('backend_query','cached_answer_reused','unresolved')for a in execution['answers'])),
  verification_completed=bool(choose_new and execution and execution.get('accepted')),semantic_verification_success_guaranteed=False,
  plan_reference={'path':str(ROOT/'results/plans'/f'{case["id"]}_r{repeat}'/'plan.result.json'),'sha256':sha(ROOT/'results/plans'/f'{case["id"]}_r{repeat}'/'plan.result.json')}if plan else None,
  planner_status=plan.get('status')if plan else'not_used',execution=execution,stages=stages,scip_used=False,paired_index_seconds_charged=0,runtime_probe_calls=0,
  freeze_sha256=sha(ROOT/'freeze.json'),exception=exception,context_cost=(context or{}).get('cost',base.get('context_cost',{})),
  context_stages=(context or{}).get('stages',base.get('context_stages',{})),collected_source_tokens=(context or{}).get('source_tokens',base.get('source_tokens',0)),
  active_wall_seconds=base['active_wall_seconds']+extra_seconds,
  source_pool_hash=case.get('source_content_hash'),information_condition='Same public PR and unchanged fixed AST+LSP source. No SCIP. No private gold in planning/review.',
  method_version='v3_condition_roles_reporting_only',warning_trace=warning_trace,literal_fact_check=literal_fact_check,new_physical_model_in_this_run=bool(choose_new),current_selection_complete=bool((execution or{}).get('validation',{}).get('complete',False)))
 failures=[]
 if choose_new:
  row.update(payload=new,raw_final_payload=new,accepted_final_payload=new,final_produced=True,raw_findings_count=len(new['findings']),raw_findings_count_measurable=True,
   evidence=delivery['evidence'],final_input_evidence_ids=delivery['evidence_ids'],source_tokens=source_tokens(delivery['evidence']),report_status='ok',actual_raw_final_response_present=True,
   auxiliary_final_update_accepted=final_aux.get('accepted')if isinstance(final_aux,dict)else None,controller=delivery.get('controller_before_call'),
   actual_final_provider_reference={'path':str(finalp),'sha256':sha(finalp)})
  from claim_controller import ClaimController
  ctl=ClaimController(row['evidence'],mapping_mode='active');row['binding']=ctl.bind_locations(new)
  if(finalfile:=folder/'final.auxiliary_update.json').exists():row['controller']=load(finalfile)['controller_after_call']
 else:
  row['actual_final_provider_reference']=copy.deepcopy(base['stages'][-1])if base.get('final_produced')else None
  if arm!='FIXED':failures.append('initial_actual_final_retained_no_new_normal_final')
 if plan and plan.get('status')!='ok':failures.append('planner_not_valid_or_not_reached')
 if exception:failures.append('runner_exception_preserved')
 known=sum(s.get('provider_tokens')or 0 for s in stages);unknown=any(s.get('provider_tokens')is None for s in stages)or bool(plan and plan.get('model_issue_state_unknown'))
 row.update(logical_model_calls=sum(int(s.get('physical_attempt_count',0)>0)for s in stages),actual_model_tokens=None if unknown else known,known_model_tokens=known,model_usage_unknown=unknown)
 if unknown:failures.append('method_usage_unknown');row['report_status']='error'
 row['delivered_source_tokens_measured']=source_tokens(row.get('evidence',[]))
 row['context_cost_unknown']=bool((context or{}).get('cost_unknown'))
 if row['context_cost_unknown']:failures.append('context_cost_unknown');row['report_status']='error'
 for value,maximum,reason in ((known,BUDGETS['max_model_tokens'],'model_token_overrun'),(row['logical_model_calls'],3,'model_call_overrun'),(row['active_wall_seconds'],900,'active_wall_overrun'),(row['collected_source_tokens'],12000,'collected_source_overrun'),(row['delivered_source_tokens_measured'],12000,'delivered_source_overrun')):
  if value>maximum:failures.append(reason);row['report_status']='error'
 for key,maximum in (('native_calls',48),('native_seconds',180),('file_reads',256)):
  if row['context_cost'].get(key,0)>maximum:failures.append(key+'_overrun');row['report_status']='error'
 row['failures']=list(dict.fromkeys((base.get('failures',[])if not choose_new else[])+failures))
 row['method_completed']=bool(row['method_completed']and row['report_status']=='ok')
 row['status']='ok'if row['report_status']=='ok'and row.get('auxiliary_final_update_accepted')is not False and not row.get('binding',{}).get('errors')else'error'
 return row

def fixed_one(case,repeat):
 rid=f'{case["id"]}_FIXED_r{repeat}';p=ROOT/'results/reviews'/rid/'review.result.json'
 if p.exists():return load(p)
 base=load(baseline_path(case,repeat));row=build_result(case,repeat,'FIXED',base,None,context=context_for(case));write_new(p,row);return row

def previous_one(case,repeat,arm):
 assert arm in('OLD_EXTRA','OLD_AGENTIC')
 rid=f'{case["id"]}_{arm}_r{repeat}';out=ROOT/'results/reviews'/rid/'review.result.json'
 if out.exists():return load(out)
 source=PREVIOUS_METHOD/'results/reviews'/f'{case["id"]}_{arm.removeprefix("OLD_")}_r{repeat}'/'review.result.json';row=copy.deepcopy(load(source))
 row.update(review_id=rid,arm=arm,cohort='condition_roles48',method_version='previous_v2',reused_previous_method_output=True,new_physical_model_in_this_run=False,
  previous_method_reference={'path':str(source),'sha256':sha(source)},previous_freeze_sha256=row.get('freeze_sha256'),freeze_sha256=sha(ROOT/'freeze.json'))
 source_trace=source.parent/'warning_trace.json'
 if source_trace.exists():write_new(out.parent/'warning_trace.json',load(source_trace))
 write_new(out,row);return row

def branch_one(case,repeat,arm):
 from replay_final import branch_one as role_branch
 return role_branch(case,repeat,arm)

def safe_branch(case,repeat,arm):
 start=time.monotonic()
 try:return branch_one(case,repeat,arm)
 except Exception as e:
  rid=f'{case["id"]}_{arm}_r{repeat}';folder=ROOT/'results/reviews'/rid;out=folder/'review.result.json'
  if out.exists():return load(out)
  error={'type':type(e).__name__,'message':str(e),'no_model_retry':True};write_new(folder/'runner_exception.json',error)
  base=load(baseline_path(case,repeat));plan=load(ROOT/'results/plans'/f'{case["id"]}_r{repeat}'/'plan.result.json')
  def optional(p):
   try:return load(p)if p.exists()else None
   except(ValueError,OSError):return None
  provider=optional(folder/'final/initial.result.json');delivery=optional(folder/'final.delivered_evidence.json');context=optional(folder/'latest_context_checkpoint.json')or(delivery or{}).get('context')
  if context is None:
   context=copy.deepcopy(context_for(case))
   if arm=='AGENTIC'and(folder/'dynamic_queries').exists():context['cost_unknown']=True
  execution=optional(folder/'query_execution.json');auxfile=optional(folder/'final.auxiliary_update.json')or{};aux=auxfile.get('update');waudit=auxfile.get('warning_audit');literal=auxfile.get('literal_fact_check')
  try:
   tracker=WarningTracker(base.get('payload'),base['evidence'])
   if isinstance(plan.get('payload'),dict):tracker.record_plan(plan['payload'],plan.get('validation'))
   if execution:tracker.record_answers(execution)
   if delivery:tracker.add_evidence(delivery['evidence'])
   normal=valid_payload(provider,REVIEW)if provider else None
   if normal is not None:waudit=tracker.audit_final(normal,delivered_evidence=(delivery or{}).get('evidence',[]))
   else:tracker.record_no_new_final('Runner exception preserved; no normal final found.')
   if not(folder/'warning_trace.json').exists():write_new(folder/'warning_trace.json',tracker.export())
  except Exception as tracker_error:
   waudit={'accepted':False,'tracking_failed':True,'errors':[{'code':'warning_tracking_recovery_failed','type':type(tracker_error).__name__,'message':str(tracker_error)}],'raw_final_unchanged':True}
   if not(folder/'warning_tracking_exception.json').exists():write_new(folder/'warning_tracking_exception.json',waudit)
  seconds=sum(s.get('timing',{}).get('duration_seconds',0)for s in plan.get('stages',[]))+time.monotonic()-start
  try:row=build_result(case,repeat,arm,base,plan,provider=provider,delivery=delivery,context=context,execution=execution,extra_seconds=seconds,exception=error,final_aux=aux,warning_trace=waudit,literal_fact_check=literal)
  except Exception as recovery_error:
   # Last-resort terminal still selects a normal actual final if one exists.
   # A bookkeeping failure is not permission to discard a new review. It marks
   # the report/usage uncertain, without issuing another model call.
   row=copy.deepcopy(base)
   row.update(review_id=rid,arm=arm,repeat=repeat,final_origin='shared_B2_initial_final'if base.get('final_produced')else'none',
    final_new_generated=False,fallback_to_initial=bool(base.get('final_produced')),method_completed=False,query_completed=False,verification_completed=False,
    semantic_verification_success_guaranteed=False,report_status='error',status='error',model_usage_unknown=True,actual_model_tokens=None,
    known_model_tokens=base.get('known_model_tokens',base.get('actual_model_tokens')),context_cost_unknown=True,
    failures=list(base.get('failures',[]))+['runner_exception_preserved','terminal_recovery_failed_usage_unknown'],exception=error,
    recovery_exception={'type':type(recovery_error).__name__,'message':str(recovery_error)},scip_used=False,runtime_probe_calls=0,
    actual_final_provider_reference=copy.deepcopy(base['stages'][-1])if base.get('final_produced')else None)
   normal=valid_payload(provider,REVIEW)if provider else None
   exact=bool(delivery and digest(delivery.get('evidence',[]))==delivery.get('evidence_sha256')and[e['id']for e in delivery['evidence']]==delivery.get('evidence_ids'))
   if normal is not None and exact and provider.get('physical_attempt_count',0)>0:
    p=folder/'final/initial.result.json'
    row.update(payload=normal,raw_final_payload=normal,accepted_final_payload=normal,final_produced=True,
     final_origin='new_final',final_new_generated=True,fallback_to_initial=False,
     raw_findings_count=len(normal['findings']),raw_findings_count_measurable=True,actual_raw_final_response_present=True,
     evidence=delivery['evidence'],final_input_evidence_ids=delivery['evidence_ids'],source_tokens=delivery['source_tokens'],
     actual_final_provider_reference={'path':str(p),'sha256':sha(p)},controller=delivery.get('controller_before_call'),
     auxiliary_final_update_accepted=None,binding={'errors':[{'code':'runner_binding_postprocessing_failed'}]})
  if(folder/'final.admission.json').exists()and load(folder/'final.admission.json')['admitted']and not(folder/'final/initial.result.json').exists():
   row.update(actual_model_tokens=None,model_usage_unknown=True,report_status='error',status='error');row['failures'].append('admitted_final_issue_state_unknown')
  write_new(out,row);return row

def run():
 from freeze_lsp48 import verify_freeze
 verify_freeze();cases=load(ROOT/'prepared/cases.json');assert len(cases)==48
 for c in cases:
  for n in range(1,3):
   fixed_one(c,n)
   for arm in('OLD_EXTRA','OLD_AGENTIC'):previous_one(c,n,arm)
 assert all((ROOT/'results/plans'/f'{c["id"]}_r{n}'/'plan.result.json').exists()for c in cases for n in(1,2)), 'Only frozen reused plans are allowed'
 jobs=[(c,n,a)for c in cases for n in(1,2)for a in('EXTRA','AGENTIC')];random.Random(20261104).shuffle(jobs)
 with ProcessPoolExecutor(max_workers=WORKERS)as pool:
  for future in as_completed([pool.submit(safe_branch,*job)for job in jobs]):future.result()
 assert len(list((ROOT/'results/reviews').glob('*/review.result.json')))==480

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=('run',));p.parse_args();run()
