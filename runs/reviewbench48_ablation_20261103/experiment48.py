"""Frozen staged reviewer execution. Evaluator gold is never a reviewer input."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import argparse,copy,json,random,time
import jsonschema
from common import ROOT,MODEL,ARMS,REPEATS,BUDGETS,WORKERS,load,sha,digest,write_new,source_tokens
from diff48 import diff_evidence,query_summary
from prompts48 import reviewer_prompt,controller_feedback
from schemas import REVIEW
from transport48 import complete,parse_payload,total_usage,admission
from claim_controller import ClaimController

STAGES={'B1':'AST','B2':'LSP','B3':'SCIP','C':'SCIP','B4':'SCIP'}

def context_for(case,arm):
 if arm=='B0':return dict(evidence=[],cost={},stages={},source_tokens=0,seconds=0,source_pool_sha256=digest(case['snapshots']))
 p=ROOT/'contexts'/case['id']/f'context_{STAGES[arm]}.json'
 return load(p)

def context_seconds(context):
 return sum(s.get('seconds',0)for s in context.get('stages',{}).values())+context.get('shutdown_seconds',0)

def index_charge(case,arm):
 return case.get('paired_index_seconds',0)if arm in('B3','C','B4')else 0

def evidence_for(case,context):return list(context.get('evidence',[]))+diff_evidence(case['diff'])

def aux_update(ctrl,payload,round_index):
 if payload is None:return None
 try:
  jsonschema.validate(payload,REVIEW)
  return ctrl.update(payload,round_index)
 except Exception as e:
  return dict(accepted=False,errors=[{'code':'auxiliary_update_exception','type':type(e).__name__,'message':str(e)}])

def report_binding(ctrl,payload):
 if payload is None or not isinstance(payload.get('findings'),list):return dict(bindings=[],errors=[],unresolved=[],unobservable=True)
 try:return ctrl.bind_locations(payload)
 except Exception as e:return dict(bindings=[],errors=[{'type':type(e).__name__,'message':str(e)}],unresolved=[],unobservable=False)

def stage_record(path,result,label):
 return dict(stage=label,reference=str(path),sha256=sha(path),physical_attempt_count=result.get('physical_attempt_count',0),provider_tokens=total_usage(result),status=result.get('status'),timing=result.get('timing',{}))

def save_delivery(folder,label,evidence,context,ctrl):
 """Exact pre-issuance checkpoint, including source bodies for crash recovery."""
 write_new(folder/(label+'.delivered_evidence.json'),dict(evidence_ids=[e['id']for e in evidence],evidence_sha256=digest(evidence),source_tokens=source_tokens(evidence),evidence=evidence,context=context,controller_before_call=ctrl.export(),not_proof_of_issuance=True))

def save_auxiliary(folder,label,aux,ctrl):
 write_new(folder/(label+'.auxiliary_update.json'),dict(update=aux,controller_after_call=ctrl.export()))

def final_result(case,rid,arm,repeat,context,evidence,ctrl,provider,stages,*,active_seconds,queries=(),initial_reference=None,backend_close=None,planned_followup_calls=0,auxiliary_final_update=None):
 raw=parse_payload(provider,require_ok=False)if provider else None
 produced=isinstance(raw,dict)and isinstance(raw.get('findings'),list)
 good=parse_payload(provider)if provider else None
 report_status='unattempted'if not provider or provider.get('status')=='unattempted'else'ok'if good is not None else'error'
 if good is not None:
  try:jsonschema.validate(good,REVIEW)
  except jsonschema.ValidationError:report_status='error'
 binding=report_binding(ctrl,raw);accepted=auxiliary_final_update.get('accepted')if isinstance(auxiliary_final_update,dict)else None
 known=sum(s['provider_tokens']or 0 for s in stages);unknown=any(s['provider_tokens']is None for s in stages)
 unknown_issue=sum(bool(s.get('physical_issue_unknown'))for s in stages)
 failures=[]
 if not produced:failures.append('final_not_produced')
 if report_status!='ok':failures.append('provider_or_schema_or_admission_failure')
 if accepted is False:failures.append('auxiliary_final_update_rejected')
 if binding.get('errors'):failures.append('one_or_more_location_bindings_invalid')
 if unknown:failures.append('provider_usage_unknown')
 if unknown_issue:failures.append('model_issue_state_unknown');report_status='error'
 actual_source_tokens=source_tokens(evidence)
 if actual_source_tokens>BUDGETS['max_source_tokens']:failures.append('actual_source_token_overrun');report_status='error'
 costs=context.get('cost',{})
 for key,limit in(('native_calls','max_native_calls'),('native_seconds','max_native_seconds'),('file_reads','max_file_reads')):
  if costs.get(key,0)>BUDGETS[limit]:failures.append('actual_'+key+'_overrun');report_status='error'
 if index_charge(case,arm)>BUDGETS['max_index_seconds']:failures.append('actual_paired_index_seconds_overrun');report_status='error'
 if sum(int(s['physical_attempt_count']>0)for s in stages)>BUDGETS['max_model_calls']:failures.append('actual_model_call_overrun');report_status='error'
 if not unknown and known>BUDGETS['max_model_tokens']:failures.append('actual_model_token_overrun');report_status='error'
 if active_seconds>BUDGETS['max_wall_seconds']:failures.append('actual_active_wall_overrun');report_status='error'
 rawfindings=raw.get('findings',[])if produced else[]
 # Binding failures are finding-level. Auxiliary failures remain compatible
 # diagnostics, not a new global report gate that punishes diff-only absence.
 status='ok'if report_status=='ok'and accepted is not False and not binding.get('errors')else'error'
 actual_issued=bool(provider and provider.get('physical_attempt_count',0)>0)
 return dict(review_id=rid,case_id=case['id'],arm=arm,repeat=repeat,language=case['language'],cohort='selected_reviewbench48',
  status=status,report_status=report_status,auxiliary_final_update_accepted=accepted,failures=failures,
  payload=raw,raw_final_payload=raw,accepted_final_payload=good,final_produced=produced,
  actual_raw_final_response_present=bool(provider and provider.get('raw_response')is not None),raw_findings_count=len(rawfindings),
  raw_findings_count_measurable=produced,evidence=evidence,final_input_evidence_ids=[e['id']for e in evidence]if actual_issued else[],
  binding=binding,controller=ctrl.export(),stages=stages,initial_reference=initial_reference,
  logical_model_calls=sum(int(s['physical_attempt_count']>0)for s in stages),actual_model_tokens=None if unknown else known,known_model_tokens=known,model_usage_unknown=unknown,
  model_issue_unknown_stages=unknown_issue,logical_model_calls_measurement_status='known_issued_lower_bound'if unknown_issue else'known',
  source_tokens=actual_source_tokens,context_cost=context.get('cost',{}),context_stages=context.get('stages',{}),context_reference_sha256=digest(context),
  paired_index_seconds_charged=index_charge(case,arm),active_wall_seconds=active_seconds,queries=list(queries),backend_close=backend_close,
  planned_followup_calls=planned_followup_calls,budgets=BUDGETS,runtime_probe_calls=0,freeze_sha256=sha(ROOT/'freeze.json'),
  source_pool_hash=digest(case['snapshots']),information_condition='Original public PR title/body and full fixed diff; no gold labels or private defect hints.',
  score_boundary='Primary report validity separates schema/provider/report status from auxiliary ledger acceptance; compatibility gate reported separately.')

def stage_scope(context):
 return dict(source_tokens=context.get('source_tokens',0),source_cap=context.get('policy_caps',{}),
  unresolved_counts={code:sum(v.get('reason')==code for v in context.get('unresolved',[]))for code in sorted({v.get('reason','unknown')for v in context.get('unresolved',[])})},
  static_only=True,metadata_does_not_prove_runtime=True)

def single_one(case,repeat,arm):
 rid=f'{case["id"]}_{arm}_r{repeat}';folder=ROOT/'results/reviews'/rid;p=folder/'review.result.json'
 if p.exists():return load(p)
 context=context_for(case,arm);evidence=evidence_for(case,context);ctrl=ClaimController(evidence,mapping_mode='active')
 raw=reviewer_prompt(case,evidence,query_results=stage_scope(context)if arm!='B0'else None)
 charged=context_seconds(context)+index_charge(case,arm)
 save_delivery(folder,'initial',evidence,context,ctrl)
 result=complete(folder,'initial',raw,REVIEW,remaining_wall=max(0,BUDGETS['max_wall_seconds']-charged));payload=parse_payload(result)
 aux=aux_update(ctrl,payload,0)
 if aux is not None and not ctrl.updates:ctrl.updates.append(aux)
 save_auxiliary(folder,'initial',aux,ctrl)
 ref=folder/'initial/initial.result.json';stages=[stage_record(ref,result,'initial')]
 seconds=charged+result.get('timing',{}).get('duration_seconds',0)
 r=final_result(case,rid,arm,repeat,context,evidence,ctrl,result,stages,active_seconds=seconds,auxiliary_final_update=aux)
 write_new(p,r);print(json.dumps({'review_id':rid,'report_status':r['report_status'],'raw_findings':r['raw_findings_count'],'logical_calls':r['logical_model_calls']}),flush=True);return r

def same_round_questions(payload,collector):
 qs=(payload or{}).get('questions',[])
 if not qs:qs=collector.seed_questions()
 out=[];seen=set()
 for q in qs:
  key=tuple(q.get(k)for k in('snapshot','file','line','column','query','symbol'))
  if key in seen:continue
  seen.add(key);out.append(q)
 return out

def branch_one(case,repeat,arm):
 assert arm in('C','B4');rid=f'{case["id"]}_{arm}_r{repeat}';folder=ROOT/'results/reviews'/rid;p=folder/'review.result.json'
 if p.exists():return load(p)
 initialp=ROOT/'results/reviews'/f'{case["id"]}_B3_r{repeat}'/'review.result.json';initial=load(initialp)
 context=context_for(case,'B3');evidence=evidence_for(case,context);ctrl=ClaimController(evidence,mapping_mode='active')
 initialstage=initial['stages'][0];initialprovider=load(initialstage['reference']);previous=parse_payload(initialprovider)
 aux=aux_update(ctrl,previous,0)
 if aux is not None and not ctrl.updates:ctrl.updates.append(aux)
 stages=[initialstage];queries=[];start=time.monotonic();base_seconds=initial['active_wall_seconds'];collector=None;close=None;provider=None;final_aux=None
 initial_ref={'path':str(initialp),'sha256':sha(initialp),'shared_actual_initial':True}
 if previous is None:
  # An initial failure is not retried or promoted. Still produce a planned row.
  write_new(folder/'not_reached.json',dict(reason='shared_initial_failed',initial_reference=initial_ref))
  r=final_result(case,rid,arm,repeat,context,evidence,ctrl,None,stages,active_seconds=base_seconds,initial_reference=initial_ref)
  write_new(p,r);return r
 used=initial['actual_model_tokens'];calls=initial['logical_model_calls']
 forecast=reviewer_prompt(case,evidence,previous=previous,feedback=controller_feedback(ctrl),query_results=[])
 estimate=admission(forecast,REVIEW,used,calls,max(0,900-base_seconds))
 # The same initial-only forecast sets the C/B4 schedule. Extra 16k covers
 # future source growth/serialized state; it is a prospective reserve estimate.
 followups=2 if estimate['remaining']is not None and estimate['remaining']>=2*(estimate['required_reserve']+16000)else 1
 write_new(folder/'followup_schedule.json',dict(followup_calls=followups,initial_forecast=estimate,extra_future_input_reserve=16000,scope='Common C/B4 initial-only forecast; actual future admission can still fail.'))
 try:
  if arm=='B4':
   from context48 import ContextCollector
   collector=ContextCollector(case,folder/'dynamic_queries',BUDGETS,initial_state=context)
  for round_index in range(1,followups+1):
   summaries=[]
   if collector is not None:
    rounds=[]
    for q in same_round_questions(previous,collector):
     if time.monotonic()-start+base_seconds>=BUDGETS['max_wall_seconds']:break
     answer=collector.query(q);rounds.append(answer);summaries.append(query_summary(answer))
     totals=collector.export()['cost']
     if totals.get('native_calls',0)>=BUDGETS['max_native_calls']or totals.get('native_seconds',0)>=BUDGETS['max_native_seconds']or totals.get('file_reads',0)>=BUDGETS['max_file_reads']:break
    context=collector.export();evidence=evidence_for(case,context);ctrl.add_evidence(evidence)
    queries.append(dict(round=round_index,queries=rounds,source_tokens_after=context['source_tokens']))
   else:summaries=[{'state':'same_frozen_source_only','new_native_queries':0,'new_evidence':0}]
   final=round_index==followups;label='final'if final else'verify'
   raw=reviewer_prompt(case,evidence,phase='final'if final else'verify',previous=previous,feedback=controller_feedback(ctrl),query_results=summaries,
    remaining={'provider_tokens':None if used is None else max(0,120000-used),'model_calls':max(0,3-calls),'source_tokens':max(0,12000-source_tokens(evidence))})
   save_delivery(folder,label,evidence,context,ctrl)
   reserve=0
   if not final:
    future=reviewer_prompt(case,evidence,phase='final',previous=previous,feedback=controller_feedback(ctrl),query_results=[])
    reserve=admission(future,REVIEW,used,calls)['required_reserve']+16000
   remaining_wall=max(0,BUDGETS['max_wall_seconds']-base_seconds-(time.monotonic()-start))
   response=complete(folder,label,raw,REVIEW,used,calls,remaining_wall,final_reserve=reserve)
   ref=folder/label/'initial.result.json';stages.append(stage_record(ref,response,label));n=total_usage(response)
   used=None if used is None or n is None else used+n;calls+=int(response.get('physical_attempt_count',0)>0)
   good=parse_payload(response)
   if final:provider=response
   if good is None:
    if not final:write_new(folder/'final.not_reached.json',dict(reason='verification_call_failed',reference=str(ref)))
    break
   aux=aux_update(ctrl,good,round_index)
   if final:final_aux=aux
   if aux is not None and not ctrl.updates:ctrl.updates.append(aux)
   save_auxiliary(folder,label,aux,ctrl)
   previous=good
 finally:
  if collector is not None:
   try:close=collector.close()
   finally:
    context=collector.export();write_new(folder/'latest_context_checkpoint.json',context)
 r=final_result(case,rid,arm,repeat,context,evidence,ctrl,provider,stages,
  active_seconds=base_seconds+time.monotonic()-start,queries=queries,initial_reference=initial_ref,backend_close=close,planned_followup_calls=followups,auxiliary_final_update=final_aux)
 write_new(p,r);print(json.dumps({'review_id':rid,'report_status':r['report_status'],'raw_findings':r['raw_findings_count'],'logical_calls':r['logical_model_calls']}),flush=True);return r

def _read_optional(path):
 try:return load(path)if path.exists()else None
 except (OSError,ValueError):return None

def _receipt(folder,label):
 """Only a receipt of this exact stage; a verify receipt is never final."""
 canonical=folder/label/'initial.result.json'
 if canonical.exists():return canonical,_read_optional(canonical)
 attempts=sorted((folder/label/'attempts/initial').glob('attempt_*/result.json'))
 if len(attempts)==1:return attempts[0],_read_optional(attempts[0])
 return None,None # ambiguous physical receipts are not silently selected

def _unknown_issue_stage(folder,label):
 """An admitted call without terminal receipt has unknown issue/usage, not 0."""
 admission=_read_optional(folder/(label+'.admission.json'))
 if admission is not None and admission.get('admitted')is False:return None
 if admission is not None and admission.get('admitted')is True or (folder/label).exists():
  return {'stage':label,'reference':str(folder/label/'initial.result.json'),'sha256':None,'physical_attempt_count':0,'physical_issue_unknown':True,'provider_tokens':None,'status':'issue_state_unknown','timing':{},'measurement_scope':'Physical count 0 is a known-issued lower bound; actual issuance is unknown and may not be charged as zero.'}
 return None

class RecoveryController:
 def __init__(self,reason):self.reason=reason;self.updates=[]
 def export(self):return {'recovery_controller_unavailable':True,'reason':self.reason,'updates':[]}
 def bind_locations(self,payload):return {'bindings':[],'errors':[{'code':'controller_unavailable_during_recovery','detail':self.reason}],'unresolved':[]}

def recover_terminal(case,repeat,arm,exception,elapsed):
 """Preserve real final response/cost without issuing or promoting any call."""
 rid=f'{case["id"]}_{arm}_r{repeat}';folder=ROOT/'results/reviews'/rid;p=folder/'review.result.json'
 if p.exists():return load(p)
 issues=[];stages=[];initial_ref=None;base_seconds=0
 try:context=context_for(case,arm)
 except Exception as e:context={'evidence':[],'cost':{},'stages':{}};issues.append('context_recovery_unavailable:'+type(e).__name__)
 if arm not in('C','B4'):base_seconds=context_seconds(context)+index_charge(case,arm)
 if arm in('C','B4'):
  initialp=ROOT/'results/reviews'/f'{case["id"]}_B3_r{repeat}'/'review.result.json';initial=_read_optional(initialp)
  initial_ref={'path':str(initialp),'sha256':sha(initialp)if initialp.exists()else None,'shared_actual_initial':True}
  if initial and initial.get('stages'):
   stages.append(initial['stages'][0]);base_seconds=initial.get('active_wall_seconds',0)
  else:
   ip,ir=_receipt(initialp.parent,'initial')
   if ir is not None:stages.append(stage_record(ip,ir,'initial_shared'));issues.append('shared_initial_terminal_missing_cost_recovered_from_receipt')
   else:
    unknown=_unknown_issue_stage(initialp.parent,'initial')
    if unknown:stages.append(unknown)
    issues.append('shared_initial_receipt_unavailable')
  labels=('verify','final');final_label='final'
 else:labels=('initial',);final_label='initial'
 provider=None;provider_path=None
 for label in labels:
  ref,value=_receipt(folder,label)
  if value is not None:
   stages.append(stage_record(ref,value,label))
   if label==final_label:provider=value;provider_path=ref
  else:
   unknown=_unknown_issue_stage(folder,label)
   if unknown:stages.append(unknown)
 delivery_path=folder/(final_label+'.delivered_evidence.json');delivery=_read_optional(delivery_path)
 # Collected source may remain for diagnostics even when final was never issued.
 try:evidence=evidence_for(case,context)
 except Exception:evidence=[];issues.append('collected_evidence_reconstruction_unavailable')
 delivered_ok=False
 if isinstance(delivery,dict):
  captured=delivery.get('evidence',evidence)
  if isinstance(captured,list)and digest(captured)==delivery.get('evidence_sha256')and[e.get('id')for e in captured]==delivery.get('evidence_ids'):
   evidence=captured;delivered_ok=True
   if isinstance(delivery.get('context'),dict):context=delivery['context']
  else:issues.append('final_delivery_manifest_hash_or_ids_mismatch')
 elif provider is not None and provider.get('physical_attempt_count',0)>0:issues.append('actual_final_receipt_without_delivery_manifest')
 latest=_read_optional(folder/'latest_context_checkpoint.json')
 if isinstance(latest,dict):context=latest
 try:ctrl=ClaimController(evidence,mapping_mode='active')
 except Exception as e:ctrl=RecoveryController(type(e).__name__+': '+str(e));issues.append('controller_reconstruction_failed')
 auxiliary=_read_optional(folder/(final_label+'.auxiliary_update.json'));aux=auxiliary.get('update')if isinstance(auxiliary,dict)else None
 schedule=_read_optional(folder/'followup_schedule.json')or{}
 r=final_result(case,rid,arm,repeat,context,evidence,ctrl,provider,stages,active_seconds=base_seconds+elapsed,initial_reference=initial_ref,planned_followup_calls=schedule.get('followup_calls',0),auxiliary_final_update=aux)
 # Only an actually issued canonical final plus its exact delivery checkpoint
 # can establish delivery. Never attach the B3/verify manifest to branch final.
 if not(provider and provider.get('physical_attempt_count',0)>0 and delivered_ok):
  r['final_input_evidence_ids']=[]
  if provider is not None and provider.get('physical_attempt_count',0)>0:r['report_status']='error';r['failures'].append('final_delivery_recovery_incomplete')
 if r['report_status']!='ok'or aux is not None and aux.get('accepted')is False:r['status']='error'
 r['failures'].append('runner_exception_preserved')
 r['terminal_recovery']={'recovered':True,'exception_type':type(exception).__name__,'exception_message':str(exception),'actual_final_receipt':str(provider_path)if provider_path else None,'exact_delivery_recovered':delivered_ok,'actual_final_issued':bool(provider and provider.get('physical_attempt_count',0)>0),'delivery_manifest_reference':str(delivery_path)if delivery else None,'auxiliary_after_call':auxiliary,'issues':issues,'no_model_retry':True,'intermediate_promoted':False}
 if isinstance(auxiliary,dict)and isinstance(auxiliary.get('controller_after_call'),dict):r['controller']=auxiliary['controller_after_call']
 elif isinstance(delivery,dict):r['controller_before_final_from_checkpoint']=delivery.get('controller_before_call')
 write_new(p,r);return r

def minimal_terminal(case,repeat,arm,exception,recovery_exception,elapsed):
 """Last-resort row retains costs/raw receipt refs, with no invented delivery."""
 rid=f'{case["id"]}_{arm}_r{repeat}';folder=ROOT/'results/reviews'/rid;p=folder/'review.result.json';stages=[];provider=None;initial_ref=None
 if p.exists():return load(p)
 labels=('verify','final')if arm in('C','B4')else('initial',)
 if arm in('C','B4'):
  ip=ROOT/'results/reviews'/f'{case["id"]}_B3_r{repeat}'/'review.result.json';initial=_read_optional(ip);initial_ref={'path':str(ip),'sha256':sha(ip)if ip.exists()else None,'shared_actual_initial':True}
  if initial and initial.get('stages'):stages.append(initial['stages'][0])
  else:
   ref,r=_receipt(ip.parent,'initial')
   if r is not None:stages.append(stage_record(ref,r,'initial_shared'))
   else:
    unknown=_unknown_issue_stage(ip.parent,'initial')
    if unknown:stages.append(unknown)
 for label in labels:
  ref,r=_receipt(folder,label)
  if r is not None:
   stages.append(stage_record(ref,r,label))
   if label==labels[-1]:provider=r
  else:
   unknown=_unknown_issue_stage(folder,label)
   if unknown:stages.append(unknown)
 raw=parse_payload(provider,require_ok=False)if provider else None;produced=isinstance(raw,dict)and isinstance(raw.get('findings'),list);known=sum(s['provider_tokens']or 0 for s in stages);unknown=any(s['provider_tokens']is None for s in stages)
 row=dict(review_id=rid,case_id=case['id'],arm=arm,repeat=repeat,language=case['language'],status='error',report_status='error'if unknown or provider and provider.get('physical_attempt_count',0)>0 else'unattempted',auxiliary_final_update_accepted=None,
  failures=['runner_exception_preserved','terminal_recovery_failed','final_delivery_not_reconstructed'],raw_final_payload=raw,payload=raw,final_produced=produced,raw_findings_count=len(raw['findings'])if produced else 0,raw_findings_count_measurable=produced,
  evidence=[],final_input_evidence_ids=[],stages=stages,initial_reference=initial_ref,logical_model_calls=sum(int(s['physical_attempt_count']>0)for s in stages),actual_model_tokens=None if unknown else known,known_model_tokens=known,model_usage_unknown=unknown,
  model_issue_unknown_stages=sum(bool(s.get('physical_issue_unknown'))for s in stages),logical_model_calls_measurement_status='known_issued_lower_bound'if any(s.get('physical_issue_unknown')for s in stages)else'known',
  source_tokens=None,context_cost={},context_stages={},paired_index_seconds_charged=index_charge(case,arm),active_wall_seconds=elapsed,queries=[],runtime_probe_calls=0,cost_measurement_incomplete=True,cost_measurement_status='unknown_native_source_wall_after_recovery_failure',
  terminal_recovery={'recovered':False,'exception_type':type(exception).__name__,'exception_message':str(exception),'recovery_exception_type':type(recovery_exception).__name__,'recovery_exception_message':str(recovery_exception),'no_model_retry':True,'intermediate_promoted':False})
 write_new(p,row);return row

def safe_job(case,repeat,arm):
 # Every planned row gets a terminal record. Recovery makes zero model calls.
 start=time.monotonic()
 try:return single_one(case,repeat,arm)if arm in('B0','B1','B2','B3')else branch_one(case,repeat,arm)
 except Exception as e:
  rid=f'{case["id"]}_{arm}_r{repeat}';p=ROOT/'results/reviews'/rid/'runner_exception.json'
  if not p.exists():write_new(p,dict(type=type(e).__name__,message=str(e),automatic_retry=False))
  try:r=recover_terminal(case,repeat,arm,e,time.monotonic()-start)
  except Exception as recovery_error:r=minimal_terminal(case,repeat,arm,e,recovery_error,time.monotonic()-start)
  print(json.dumps({'review_id':rid,'runner_exception':type(e).__name__+': '+str(e),'terminal_result_preserved':True}),flush=True);return r

def run():
 from freeze48 import verify_freeze
 verify_freeze();assert load(ROOT/'private/gold_validation_seal.json')['complete']
 cases=load(ROOT/'prepared/cases.json');assert len(cases)==48
 for arms in[('B0','B1','B2','B3'),('C','B4')]:
  jobs=[(c,n,a)for c in cases for n in range(1,REPEATS+1)for a in arms];random.Random(20261103).shuffle(jobs)
  with ProcessPoolExecutor(max_workers=WORKERS)as pool:
   for future in as_completed([pool.submit(safe_job,*job)for job in jobs]):future.result()
 print(json.dumps({'planned_reviews':len(cases)*len(ARMS)*REPEATS,'actual_review_results':len(list((ROOT/'results/reviews').glob('*/review.result.json')))}),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['run']);a=p.parse_args();run()
