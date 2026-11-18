"""Recorded-source final path with a simulated provider; model/native calls0.

Fixtures live only under audit/, never under real results/evaluation roots.
Exercises actual branch -> nested schema -> decode -> projection -> oar update.
"""
import copy,json
from pathlib import Path
from unittest.mock import patch
import jsonschema
from common import ROOT,PROJECT,load,write_new,digest
import causal_loop as c
import oar_core
from sequential import configure

def run():
 case=c.cases()[0];n=1;up=PROJECT/'runs/lsp_step2b_strict_budget_20261105';prior=PROJECT/'runs/lsp_step3b_scope_contract_20261105';shared=load(up/'results/shared'/f'{case["id"]}_r1'/'shared.json');extra=load(up/'results/shared'/f'{case["id"]}_r1'/'extra_replan.json');trace=load(up/'results/reviews'/f'{case["id"]}_FOUR_QUERY_r1'/'query_round_trace.json')
 actual=load(prior/'results/reviews'/f'{case["id"]}_REPORT2_r1'/'review.result.json');receipt=load(actual['actual_final_provider_reference']['path']);fixture=ROOT/'audit/workflow_simulation'
 write_new(fixture/'results/shared'/f'{case["id"]}_r1'/'shared.json',shared);write_new(fixture/'results/shared'/f'{case["id"]}_r1'/'extra_replan.json',extra);write_new(fixture/'contexts'/case['id']/'context_LSP.json',load(ROOT/'contexts'/case['id']/'context_LSP.json'));write_new(fixture/'prepared/method.json',dict(final_policy='LEDGER'))
 configure();updates=[];original=oar_core.Ledger.apply_update
 def spy(self,*args,**kwargs):updates.append(True);return original(self,*args,**kwargs)
 def provider(folder,label,raw,schema,*args,**kwargs):
  jsonschema.validate(json.loads(receipt['raw_response']),schema)
  value=copy.deepcopy(receipt);value['simulation_only']=True
  write_new(folder/label/'initial.result.json',value);write_new(folder/label/'request.json',dict(simulation_only=True,raw=raw,schema=schema))
  return value
 record=trace['additional_round']
 class Tool:
  def __init__(self,*args,**kwargs):pass
  def set_phase(self,*args):pass
  def close(self):pass
  def export(self):return copy.deepcopy(record['context'])
 def recorded_execution(frontier,plan,tool,**kwargs):
  from reexplore_run import RecordedAnswers
  from frontier import request_key
  from loop_policy import attach
  for q in plan['questions']:
   if q['query']=='search':frontier.cache.pop(request_key(q),None)
  actual=frontier.execute_plan(plan,RecordedAnswers(record['core']),enable_queries=True);assert digest(actual)==digest(record['core'])
  attach(frontier,record['context']['evidence']);return copy.deepcopy(record)
 with patch.object(c,'ROOT',fixture),patch.object(c,'complete',provider),patch.object(c,'ContextCollector',Tool),patch.object(c,'execute',recorded_execution),patch.object(oar_core.Ledger,'apply_update',spy):
  r=c._branch(case,n,'FOUR_QUERY')
 assert r['final_produced']and updates
 folder=fixture/'results/reviews'/f'{case["id"]}_FOUR_QUERY_r1';projection=load(folder/'controller_projection.json');assert all('causal_scope'not in x for x in projection['payload']['claims'])
 assert all('causal_scope'in x for x in r['payload']['claims'])
 from scope_contract import ledger_projection
 assert projection['payload']==ledger_projection(r['payload'])
 assert r['raw_wire_payload']==json.loads(receipt['raw_response'])
 write_new(ROOT/'audit/workflow_smoke.json',dict(passed=True,actual_oar_apply_update_calls=len(updates),real_native_or_model_calls=0,provider='simulation_of_existing_response',fixture_root=str(fixture),nested_scope_retained_in_original_output=True,legacy_semantic_values_preserved=True,not_an_experiment_response=True))
 print('PASS:actual final branch/nested schema/projection/oar update; simulated provider only.')
if __name__=='__main__':run()
