"""Synthetic model-only transport checks; no real model or LSP calls."""
import importlib.util,json,tempfile,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
def load():
 s=importlib.util.spec_from_file_location('recovery_check',HERE/'experiment.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def exercise(limit):
 m=load();e=m.e;calls=[];first=e.read(e.LEGACY/'raw'/'N01_a_P0Q0_r1_s1.json')['response'];final=e.read(e.LEGACY/'raw'/'N01_a_P0Q0_r1_s2.json')['response'];assessment={'used_evidence':[],'dismissed_evidence':[],'missing_connections':[]};first={**first,'context_assessment':assessment};final={**final,'context_assessment':assessment}
 with tempfile.TemporaryDirectory(prefix='.recovery-selfcheck-',dir=HERE) as td:
  e.R=Path(td);m.START_INTERVAL_SECONDS=.025
  for name in ['schema_first.json','schema_final.json']:e.copy_equal(HERE/name,e.R/name)
  e.copy_equal(HERE/'cases'/'N01_a.json',e.R/'cases'/'N01_a.json');seed=e.read(HERE/'packets'/'seed'/'N01_a_initial.json');e.save(e.R/'packets'/'seed'/'N01_a_initial.json',seed);e.save(e.R/'packets'/'F'/'N01_a_fixed.json',seed)
  def fake(row,root,model,schema):
   calls.append(row['run_id']);bad=limit and row['run_id']=='N01_a_F_r1_s1';time.sleep(.30 if bad else .45 if limit else .06)
   rec={'status':'error' if bad else 'ok','response':None if bad else first if row['run_id'].endswith('_s1') else final,'error':None,'model':model,'prompt_sha256':row['prompt_sha256']}
   e.save(root/(row['run_id']+'.json'),rec);e.save(root/(row['run_id']+'.trace.json'),{'stderr':'','stdout':json.dumps({'type':'error','message':'429 Too Many Requests'}) if bad else ''});return rec
  def native(job,response,queued):time.sleep(.02);return e.store_retrieval(job,seed,[],False)
  m._transport=fake;e.agentic_retrieval=native
  jobs=[{'run_id':f'N01_a_{a}_r{i}','case_id':'N01','world':'a','arm':a,'repeat':i} for i in range(1,5) for a in ['F','G']]
  outcomes,events,admission=m.run_scheduler(jobs)
  starts=sorted(e.read(p)['started_monotonic'] for p in (e.R/'actual_cli_starts').glob('*.json'));intervals=[b-a for a,b in zip(starts,starts[1:])]
  assert all(x>=.025 for x in intervals) and len(calls)==len(set(calls)) and e._max_active==2
  assert len(outcomes)==8
  if limit:
   assert len(calls)==2 and len(events)==1 and admission==0
   assert sum(x['status']=='aborted_resource' for x in outcomes)==7
   assert sum(x['status']=='first_call_error' for x in outcomes)==1
   assert not list((e.R/'retrieval').glob('*.json')),'native admitted after halt'
   logs=e.read(e.R/'scheduler_admissions.json')['rows'];assert all(x['unix']<events[0]['unix'] for x in logs)
   assert sum(x['physical_calls'] for x in outcomes)==2,'drained in-flight call omitted'
  else:assert len(calls)==16 and all(x['status']=='ok' for x in outcomes) and not events
  return {'resource_limit_injected':limit,'trajectories_retained':8,'synthetic_CLI_calls':len(calls),'max_model_overlap':e._max_active,'minimum_observed_scaled_interval_seconds':min(intervals),'scaled_interval_for_test':.025,'production_interval_seconds':3.0,'halt_then_drained_accepted_calls':bool(limit),'aborted_resource_count':sum(x['status']=='aborted_resource' for x in outcomes),'no_retries':True,'actual_model_calls':0,'actual_native_calls':0}
result={'status':'passed','normal':exercise(False),'explicit429':exercise(True),'production_defaults':{'model_workers':2,'native_workers':2,'actual_CLI_start_interval_seconds':3.0},'same_frozen_prompt_builder':True}
with (HERE/'RECOVERY_SELF_CHECK.json').open('x') as f:json.dump(result,f,indent=2)
print(json.dumps(result,indent=2))
