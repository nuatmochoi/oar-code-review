"""Whole initial-prefill -> native phases -> final orchestration, model0."""
import json
from pathlib import Path
from unittest.mock import patch
import jsonschema
from common import ROOT,load,write_new
import causal_loop as c
import protected_run as p
from openai_transport import request_body

calls=[]
def fake_complete(folder,label,raw,schema,*args,**kwargs):
    dest=Path(folder)/label/'initial.result.json'
    if dest.exists():return load(dest)
    value=dict(claims=[],questions=[],coverage=[],explorations=[],diff_facts=[],reasoning='Orchestration simulation only.',termination_reason='Simulation only.')
    if 'findings'in schema['properties']:value.update(assessment='uncertain',findings=[])
    if 'coverage'not in schema['properties']:
        for k in('coverage','explorations','diff_facts'):value.pop(k,None)
    if 'answer_assessments'in schema['properties']:value['answer_assessments']=[]
    if 'candidate_decisions'in schema['properties']:value['candidate_decisions']={}
    jsonschema.validate(value,schema)
    receipt=dict(status='ok',raw_response=json.dumps(value),actual_usage={'per_turn':[{'input_tokens':1,'output_tokens':1}],'total':{'input_tokens':1,'output_tokens':1}},physical_attempt_count=1,failure_kinds=[],timing={'duration_seconds':7 if not calls else 0},SIMULATION_ONLY_NO_NETWORK_OR_BILLING=True)
    calls.append(dict(label=label,remaining_wall=args[2]if len(args)>2 else None))
    write_new(Path(folder)/label/'request.json',request_body(raw,schema));write_new(dest,receipt);return receipt

def run():
    temp=ROOT/'audit/workflow_simulation_v2';temp.mkdir();case=p.cases()[0]
    context=temp/'contexts'/case['id']/'context_LSP.json';context.parent.mkdir(parents=True);context.write_bytes((ROOT/'contexts'/case['id']/'context_LSP.json').read_bytes())
    write_new(temp/'prepared/method.json',dict(final_policy='LEDGER'))
    with patch.object(p,'ROOT',temp),patch.object(c,'ROOT',temp),patch.object(p,'complete',fake_complete),patch.object(c,'complete',fake_complete):
        pilot=p.initial_stage((case,1));assert pilot['normal'];shared=c.common_prefix(case,1);assert shared['complete'];assert shared['active_seconds']>=7
        c.extra_replan(case,1);out=p.finish_unit((case,1));assert out['logical_model_calls']==4 and out['final_produced'];assert out['arm']=='PROTECTED'
    assert len(calls)==4;assert next(v['remaining_wall']for v in calls if v['label']=='replan')<893
    write_new(ROOT/'audit/workflow_smoke.json',dict(passed=True,actual_model_calls=0,simulated_logical_calls=4,prefilled_initial_seconds_charged=True,candidate_ledger_final_schema_tested=True,source_delivery_and_location_decode_tested=True,scope='Orchestration and accounting only; no causal-accuracy claim.'))
    return dict(passed=True,actual_model_calls=0)
if __name__=='__main__':print(json.dumps(run()))
