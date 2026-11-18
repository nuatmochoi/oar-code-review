"""Reconstruct the real prior controller, reuse exact acquired source, new final.

No native backend is imported/called here. Query/collection cost is logically
charged from the real v2 receipt and never represented as fresh tool execution.
"""
import copy,time
from pathlib import Path
from common import ROOT,PREVIOUS_METHOD,load,sha,digest,write_new,source_tokens
from warning_tracker import WarningTracker
from role_prompts import final_prompt
from condition_roles import with_condition_roles,audit
from schemas import REVIEW as BASE_REVIEW
from transport48 import complete

class FrozenAnswers:
 def __init__(self,execution):self.answers={a['question']['id']:a for a in execution.get('answers',[])};self.requests=[]
 def query(self,q):
  from frontier import request_key
  old=self.answers[q['id']]
  assert request_key(q)==request_key(old['question']),'Replay cannot invent or redirect a query'
  self.requests.append(q['id'])
  return copy.deepcopy(old)

def old_result(case,repeat,arm):
 p=PREVIOUS_METHOD/'results/reviews'/f'{case["id"]}_{arm}_r{repeat}'/'review.result.json'
 return p,load(p)

def prepare_inputs(case,repeat,arm):
 import experiment_lsp48 as e
 base=load(e.baseline_path(case,repeat));plan=load(ROOT/'results/plans'/f'{case["id"]}_r{repeat}'/'plan.result.json')
 oldpath,old=old_result(case,repeat,arm);folder=oldpath.parent
 context=copy.deepcopy(e.context_for(case));tracker=WarningTracker(base.get('payload'),base['evidence'])
 if isinstance(plan.get('payload'),dict):tracker.record_plan(plan['payload'],plan.get('validation'))
 if plan['status']!='ok':return dict(base=base,plan=plan,context=context,tracker=tracker,old=old,oldpath=oldpath,ready=False)
 execution=load(folder/'query_execution.json')if(folder/'query_execution.json').exists()else old.get('execution')
 if not execution or not execution.get('accepted'):return dict(base=base,plan=plan,context=context,tracker=tracker,old=old,oldpath=oldpath,execution=execution,ready=False)
 deliverypath=folder/'final.delivered_evidence.json'
 assert deliverypath.exists(),'Accepted previous execution requires its actual pre-call delivery'
 delivery=load(deliverypath);view,pbase,_=e.planning_view(case,base);f,_=e.initial_frontier(view,pbase)
 replay=FrozenAnswers(execution)
 rebuilt=f.execute_plan(plan['payload'],replay,enable_queries=arm=='AGENTIC')
 assert rebuilt['accepted']==execution['accepted']
 evidence=delivery['evidence'];context=copy.deepcopy(delivery['context']);f.controller.add_evidence(evidence)
 assert digest(f.controller.export())==digest(delivery['controller_before_call']),'Controller replay state drift'
 assert digest(f.export())==digest(delivery['frontier_before_call']),'Frontier replay state drift'
 assert evidence==old.get('evidence')or not old.get('final_new_generated'),'Same method source pool changed'
 tracker.record_answers(execution);tracker.add_evidence(evidence)
 return dict(base=base,plan=plan,context=context,tracker=tracker,old=old,oldpath=oldpath,
  ready=True,execution=execution,delivery=delivery,deliverypath=deliverypath,frontier=f,replay_requests=replay.requests)

def historical_seconds(inputs):
 base,plan,old=inputs['base'],inputs['plan'],inputs['old']
 planner=sum(s.get('timing',{}).get('duration_seconds',0)for s in plan.get('stages',[]))
 final=sum(s.get('timing',{}).get('duration_seconds',0)for s in old.get('stages',[])if s.get('stage')=='new_final')
 if not final:
  p=inputs['oldpath'].parent/'final/initial.result.json'
  final=load(p).get('timing',{}).get('duration_seconds',0)if p.exists()else 0
 query=max(0,old.get('active_wall_seconds',0)-base.get('active_wall_seconds',0)-planner-final)
 return planner,query

def new_raw_schema(case,inputs):
 base,plan=inputs['base'],inputs['plan'];used=base['actual_model_tokens'];n=plan['stages'][0]['provider_tokens']
 used=None if used is None or n is None else used+n
 calls=base['logical_model_calls']+sum(int(s['physical_attempt_count']>0)for s in plan['stages'])
 evidence=inputs['delivery']['evidence'];remaining={'provider_tokens':None if used is None else 120000-used,'model_calls':3-calls,'source_tokens':12000-source_tokens(evidence)}
 raw=final_prompt(case,evidence,base.get('payload'),plan['payload'],inputs['execution'],inputs['delivery']['frontier_before_call'],remaining,inputs['tracker'].prompt_state())
 schema=with_condition_roles(BASE_REVIEW,warning_ids=[w['warning_id']for w in inputs['tracker'].catalog()],
  evidence_ids=[e['id']for e in evidence],question_ids=[q['id']for q in plan['payload']['questions']])
 return raw,schema,used,calls

def branch_one(case,repeat,arm):
 import experiment_lsp48 as e
 rid=f'{case["id"]}_{arm}_r{repeat}';folder=ROOT/'results/reviews'/rid;dest=folder/'review.result.json'
 if dest.exists():return load(dest)
 started=time.monotonic();i=prepare_inputs(case,repeat,arm);base,plan,tracker=i['base'],i['plan'],i['tracker']
 context=i['context'];execution=i.get('execution');provider=None;delivery=None;aux=None;literal=None;waudit=None;roles=None
 planner_seconds,query_seconds=historical_seconds(i)
 if i['ready']:
  raw,schema,used,calls=new_raw_schema(case,i);delivery=copy.deepcopy(i['delivery'])
  delivery['reused_acquisition_reference']={'path':str(i['deliverypath']),'sha256':sha(i['deliverypath']),'new_native_calls':0,'evidence_bytes_and_ids_unchanged':True}
  write_new(folder/'query_execution.json',execution);write_new(folder/'final.delivered_evidence.json',delivery)
  write_new(folder/'final.review_schema.json',schema)
  provider=complete(folder,'final',raw,schema,used,calls,max(0,900-base['active_wall_seconds']-planner_seconds-query_seconds-(time.monotonic()-started)))
  payload=e.valid_payload(provider,e.REVIEW)
  if payload is not None:
   f=i['frontier'];literal=f.check_literal_conditions(payload);waudit=tracker.audit_final(payload,delivered_evidence=delivery['evidence'])
   roles=audit(payload,delivery['evidence'],case['public_contract'],prior_claims=plan['payload']['claims'])
   aux=f.controller.update(payload,len(f.versions)+1)
   write_new(folder/'condition_role_audit.json',roles)
   write_new(folder/'final.auxiliary_update.json',{'update':aux,'controller_after_call':f.controller.export(),'literal_fact_check':literal,'warning_audit':waudit,'condition_role_audit':roles})
 if waudit is None:tracker.record_no_new_final('No normal newly generated role-aware final; retain exact actual initial review.')
 write_new(folder/'warning_trace.json',tracker.export())
 row=e.build_result(case,repeat,arm,base,plan,provider=provider,delivery=delivery,context=context,execution=execution,
  extra_seconds=planner_seconds+query_seconds+time.monotonic()-started,final_aux=aux,warning_trace=waudit,literal_fact_check=literal)
 row.update(condition_role_audit=roles,method_completed=bool(row['method_completed']and roles and roles['accepted']),
  acquisition_reused=True,new_native_calls=0,new_planner_calls=0,acquisition_reference={'path':str(i['oldpath']),'sha256':sha(i['oldpath'])},
  logically_charged_prior_planner_seconds=planner_seconds,logically_charged_prior_query_seconds=query_seconds,
  reporting_intervention='condition roles + explicit conditional report decision; unchanged prior PLAN and actual acquired source',
  semantic_role_correctness_guaranteed=False)
 write_new(dest,row)
 print(__import__('json').dumps({'review_id':rid,'origin':row['final_origin'],'role_audit':(roles or{}).get('accepted')}),flush=True)
 return row
