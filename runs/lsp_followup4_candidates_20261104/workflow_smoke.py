"""Exact input reproduction and final location decode, without model dispatch."""
import copy,json,shutil
from pathlib import Path
from unittest.mock import patch
import jsonschema
import candidate_run as c
from common import ROOT,PROJECT,load,write_new
from openai_transport import request_body

def fake_complete(folder,label,raw,schema,*args,**kwargs):
    payload=dict(assessment='uncertain',findings=[],claims=[],questions=[],reasoning='Orchestration simulation only.',termination_reason='Simulation only.')
    if 'candidate_decisions'in schema['properties']:payload['candidate_decisions']={}
    jsonschema.validate(payload,schema)
    receipt=dict(status='ok',raw_response=json.dumps(payload),actual_usage={'per_turn':[{'input_tokens':1,'output_tokens':1}],'total':{'input_tokens':1,'output_tokens':1}},physical_attempt_count=1,failure_kinds=[],SIMULATION_ONLY_NO_NETWORK_OR_BILLING=True)
    write_new(Path(folder)/label/'request.json',request_body(raw,schema));write_new(Path(folder)/label/'initial.result.json',receipt);return receipt

def run():
    source_fixture=PROJECT/'runs/lsp_followup3_replanning_20261104/audit/workflow_simulation';fixture=ROOT/'audit/upstream_fixture';shutil.copytree(source_fixture,fixture)
    temp=ROOT/'audit/workflow_simulation';temp.mkdir(exist_ok=True)
    case=c.cases()[0]
    write_new(fixture/'results/reviews'/f'{case["id"]}_FOUR_QUERY_r1'/'final.admission.json',dict(used_tokens=6,used_calls=3,CLI_timeout_seconds=300,SIMULATION_ONLY=True))
    with patch.object(c,'UP',fixture),patch.object(c,'ROOT',temp),patch.object(c,'complete',fake_complete):
        a=c.inputs(case,1,'PLAIN');b=c.inputs(case,1,'LEDGER')
        assert a['delivery']==b['delivery'];assert a['upstream_stages']==b['upstream_stages'];assert not b['candidates']
        for arm in('INITIAL','PLAIN','LEDGER'):c.one((case,1,arm))
        out=[load(temp/'results/reviews'/f'{case["id"]}_{arm}_r1'/'review.result.json')for arm in('PLAIN','LEDGER')]
    assert [r['logical_model_calls']for r in out]==[4,4]
    write_new(ROOT/'audit/workflow_smoke.json',dict(passed=True,exact_plain_request_reproduced=True,source_and_prefix_identical=True,logical_simulated_calls=[4,4],actual_model_calls=0,scope='Controller/schema/request/delivery/cost orchestration only; no causal-accuracy claim.'))
    print(json.dumps(dict(passed=True,actual_model_calls=0)))
if __name__=='__main__':run()
