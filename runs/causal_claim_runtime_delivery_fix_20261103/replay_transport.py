"""Existing V1 CLI client, generalized schema and compact-prompt admission only."""
import json,time,hashlib
from common import ROOT,MODEL,BUDGETS,load,write_new,digest
from schemas import REVIEW
from budget_transport import BudgetedClient,total_usage,accepted_payload,raw_review_payload
import cli_transport as legacy
import reliable_transport as rt
import oar_core

def admission(raw,schema,used_tokens,used_calls):
    text,_=legacy.compose_input(legacy.DEFAULT_ORDER,raw['system'],raw.get('shared',''),raw['prompt'],raw.get('suffix',''))
    prompt_tokens=oar_core.count_reference_tokens(text);schema_tokens=oar_core.count_reference_tokens(json.dumps(schema))
    reserve=prompt_tokens+schema_tokens+24000
    remaining=None if used_tokens is None else BUDGETS['max_model_tokens']-used_tokens
    return dict(prompt_reference_tokens=prompt_tokens,schema_reference_tokens=schema_tokens,CLI_overhead_and_output_reserve=24000,required_reserve=reserve,remaining=remaining,used_tokens=used_tokens,used_calls=used_calls,admitted=remaining is not None and used_calls<BUDGETS['max_model_calls']and remaining>=reserve,composed_sha256=hashlib.sha256(text.encode()).hexdigest(),scope='Whole actual composed stdin measured; reference token admission is not provider hard output cap.')

def complete(folder,label,raw,used_tokens,used_calls,schema=REVIEW,remaining_wall=None):
    began=time.monotonic()
    folder=__import__('pathlib').Path(folder);dest=folder/label/'initial.result.json'
    if dest.exists():return load(dest)
    # Both model and execution runtime paths are root-triggered after audit.
    from replay import verify_freeze
    verify_freeze();assert load(ROOT/'audit/execution_authorization.json')['authorized']is True
    a=admission(raw,schema,used_tokens,used_calls)
    wall=(BUDGETS['max_wall_seconds']if remaining_wall is None else remaining_wall)-(time.monotonic()-began)
    # This run has one live CLI and a 3s start interval. Reserve that bounded
    # gate wait before handing the remaining active wall time to the CLI.
    cli_wall=wall-3
    a.update(remaining_active_wall_seconds=wall,start_gate_reserve_seconds=3,CLI_timeout_seconds=max(0,min(240,cli_wall)))
    a['admitted']=a['admitted']and cli_wall>0
    write_new(folder/(label+'.admission.json'),a)
    if not a['admitted']:
        write_new(folder/(label+'.unattempted.json'),a)
        return dict(status='unattempted',actual_usage={'per_turn':[],'total':{}},raw_response=None,failure_kinds=['active_wall_budget'if cli_wall<=0 else'model_admission_budget'],physical_attempt_count=0)
    client=BudgetedClient(a['remaining'],out_dir=folder,review_id=label,schema=schema,model=MODEL,gate=rt.AdmissionGate(ROOT/'results/gate',max_live_calls=1,min_interval=3),max_calls=1,timeout=min(240,cli_wall))
    client.begin_stage('initial')
    try:client.complete(**raw)
    except rt.CallFailed:pass
    return load(dest)
