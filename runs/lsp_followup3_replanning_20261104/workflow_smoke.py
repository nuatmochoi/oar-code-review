"""Full file/provider/controller orchestration simulation, zero model dispatches."""
import copy,json
from pathlib import Path
from unittest.mock import patch
import jsonschema
import causal_loop as c
from common import ROOT,load,write_new,digest
from openai_transport import request_body

def fake_complete(folder,label,raw,schema,*args,**kwargs):
    p=dict(claims=[],questions=[],coverage=[],explorations=[],diff_facts=[],reasoning='No supported provisional defect in this orchestration-only simulation.',termination_reason='Simulation only.')
    if 'findings'in schema['properties']:p.update(assessment='uncertain',findings=[])
    if 'coverage'not in schema['properties']:
        p={k:v for k,v in p.items()if k not in('coverage','explorations','diff_facts')}
    if 'answer_assessments'in schema['properties']:p['answer_assessments']=[]
    jsonschema.validate(p,schema)
    receipt=dict(status='ok',raw_response=json.dumps(p),actual_usage={'per_turn':[{'input_tokens':1,'output_tokens':1}],'total':{'input_tokens':1,'output_tokens':1}},physical_attempt_count=1,failure_kinds=[],SIMULATION_ONLY_NO_NETWORK_OR_BILLING=True)
    write_new(Path(folder)/label/'request.json',request_body(raw,schema));write_new(Path(folder)/label/'initial.result.json',receipt);return receipt

def run():
    case=c.cases()[0];temp=ROOT/'audit/workflow_simulation';temp.mkdir()
    p=temp/'contexts'/case['id']/'context_LSP.json';p.parent.mkdir(parents=True);p.write_bytes((ROOT/'contexts'/case['id']/'context_LSP.json').read_bytes())
    with patch.object(c,'ROOT',temp),patch.object(c,'complete',fake_complete):
        shared=c.common_prefix(case,1);assert shared['complete'];c.extra_replan(case,1)
        results=[c.branch(case,1,a)for a in ['THREE','FOUR_SAME','FOUR_QUERY']]
    assert [r['logical_model_calls']for r in results]==[3,4,4]
    assert all(r['final_produced']for r in results)
    write_new(ROOT/'audit/workflow_smoke.json',dict(passed=True,logical_simulated_calls=[3,4,4],actual_model_calls=0,network_calls=0,scope='Wire schema, saved requests/responses, controller replay, source identity, final location decode and per-arm call accounting. Causal accuracy not tested.'))
    return dict(passed=True,actual_model_calls=0)

if __name__=='__main__':print(json.dumps(run()))
