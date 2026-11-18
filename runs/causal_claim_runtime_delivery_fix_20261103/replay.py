"""Minimal delivery supplement: zero new planner/native, one probe, two finals."""
import copy,json,time
from pathlib import Path
from common import ROOT,BUDGETS,PROBE_BUDGETS,ARMS,load,write_new,sha,digest,source_tokens
from replay_controller import ReplayController
from replay_prompts import review_prompt
from replay_transport import complete,admission,accepted_payload,raw_review_payload,total_usage
from schemas import REVIEW
from probe import validate_probe,execute,evidence
PARENT=ROOT.parent/'causal_claim_runtime_replay_20261103'

def setup():return load(ROOT/'prepared/cases.json')[0],load(ROOT/'prepared/common_evidence.json'),load(ROOT/'prepared/seed_payload_v2.json'),load(ROOT/'prepared/shared_context.json')

def controller(ev,seed,context):
    c=ReplayController(copy.deepcopy(ev));c.update(seed,0)
    for n,p in enumerate(context['previous_stages'],1):c.update(p,n)
    return c

def preflight(receipt_name='preflight_v2.json'):
    case,ev,seed,ctx=setup();probe=ctx['plan']['probes'][0]
    assert sha(ROOT/'prepared/unchanged_probe.py')==ctx['probe_code_sha256']
    assert (ROOT/'prepared/unchanged_probe.py').read_bytes()==probe['code'].encode()
    validate_probe(probe,ctx['questions'],load(ROOT/'prepared/public_imports.json'))
    c=controller(ev,seed,ctx);raw=review_prompt(case,ev,ctx['previous_stages'][-1],c,ctx['query_records'],'final',ctx['plan'],dict(scope='analysis_profile',observations_provided=False,reason='Matched plan-only control; no observations are supplied.',deployed_runtime_confirmed=False))
    assert source_tokens(ev)<=BUDGETS['max_source_tokens']
    a=admission(raw,REVIEW,ctx['shared_actual_model_tokens'],ctx['shared_model_calls']);assert a['admitted']
    write_new(ROOT/'audit'/receipt_name,dict(passed=True,actual_common_state=c.export(),unchanged_probe_policy_accepted=True,final_noobs_stdin=a,inherited_probe_code_sha256=ctx['probe_code_sha256'],inherited_source_union_sha256=digest(ev),source_reference_tokens=source_tokens(ev),source_token_cap=BUDGETS['max_source_tokens'],shared_calls=ctx['shared_model_calls'],shared_tokens=ctx['shared_actual_model_tokens'],shared_active_wall_seconds=ctx['shared_active_wall_seconds'],remaining_active_wall=BUDGETS['max_wall_seconds']-ctx['shared_active_wall_seconds'],new_model_calls=0,new_native_calls=0,actual_Django_probes=0,scope='Exact inherited plan accepted statically; actual source full bodies retained. No probe is executed before independent audit/freeze.'))
    return a

def freeze():
    assert load(ROOT/'audit/independent_audit.json')['passed']and load(ROOT/'audit/tests.json')['passed']and load(ROOT/'audit/preflight_v2.json')['passed']
    assert(PARENT/'results/completed.json').exists()and not(ROOT/'results').exists()
    files={str(p.relative_to(ROOT)):sha(p)for p in ROOT.rglob('*')if p.is_file()and p.suffix not in('.pyc','.log')and not any(x in p.parts for x in ('__pycache__','.pytest_cache','.pycache'))and p.name not in('freeze.json','execution_authorization.json')}
    external=dict(load(PARENT/'freeze.json')['external_files'])
    seal=PARENT/'completed_result_seal.json';external[str(seal)]=sha(seal)
    for rel,h in load(seal)['files'].items():external[str(PARENT/rel)]=h
    for p,v in load(ROOT/'prepared/shared_context.json')['external_references'].items():external[p]=v['sha256']
    for p,h in external.items():assert sha(p)==h
    write_new(ROOT/'freeze.json',dict(files=files,external_files=external,model_limits=BUDGETS,probe_limits=PROBE_BUDGETS,arms=ARMS,repeats=1,max_new_model_calls=2,max_new_native_calls=0,max_actual_probe_calls=1,inherited_common_model_calls=3,scope='Technical delivery supplement; parent result preserved, same actual probe code and common state, no F1/generalization.'))
    return dict(files=len(files),external_files=len(external))

def verify_freeze():
    f=load(ROOT/'freeze.json')
    for p,h in f['files'].items():assert sha(ROOT/p)==h,('frozen changed',p)
    for p,h in f['external_files'].items():assert sha(p)==h,('external changed',p)
    return True

def run():
    physical_started=time.monotonic();verify_freeze();assert load(ROOT/'audit/execution_authorization.json')['authorized']
    assert not(ROOT/'results/started.json').exists(),'No automatic replay of saved partial calls.'
    case,ev,seed,ctx=setup();assert source_tokens(ev)<=BUDGETS['max_source_tokens']
    write_new(ROOT/'results/started.json',dict(freeze_sha256=sha(ROOT/'freeze.json'),parent_completed_sha256=sha(PARENT/'results/completed.json')))
    p=ctx['plan']['probes'][0];assert p['code'].encode()==(ROOT/'prepared/unchanged_probe.py').read_bytes()
    probe_started=time.monotonic();remaining=BUDGETS['max_wall_seconds']-ctx['shared_active_wall_seconds']
    receipt=None;observed=None;observation_status='No successful scoped observation.'
    try:
        receipt=execute(p,ctx['questions'],load(ROOT/'prepared/public_imports.json'),ROOT/'results/probe',remaining_wall=remaining)
        if receipt['valid']:observed=evidence(p,receipt,ROOT/'results/probe/receipt.json')
        observation_status='Actual isolated analysis observation supplied.'if observed else'Probe failed; raw failure preserved and no observation evidence supplied.'
    except Exception as exc:
        observation_status='Probe rejected/unavailable: '+type(exc).__name__+': '+str(exc);write_new(ROOT/'results/probe_failure.json',dict(reason=observation_status,unchanged_probe_sha256=ctx['probe_code_sha256']))
    probe_elapsed=time.monotonic()-probe_started;new_results=[];outcomes=[]
    for arm in ARMS:
        start=time.monotonic();c=controller(ev,seed,ctx)
        if arm=='observation'and observed:c.add_evidence([observed])
        status=dict(scope='analysis_profile',observations_provided=arm=='observation'and observed is not None,reason=observation_status if arm=='observation'else'Matched plan-only control; no observations are supplied.',deployed_runtime_confirmed=False)
        raw=review_prompt(case,list(c.evidence.values()),ctx['previous_stages'][-1],c,ctx['query_records'],'final',ctx['plan'],status)
        extra=probe_elapsed if arm=='observation'else 0
        remaining=BUDGETS['max_wall_seconds']-ctx['shared_active_wall_seconds']-extra-(time.monotonic()-start)
        result=complete(ROOT/'results'/arm,'final',raw,ctx['shared_actual_model_tokens'],ctx['shared_model_calls'],remaining_wall=remaining);new_results.append(result)
        raw_payload=raw_review_payload(result);accepted=accepted_payload(result);update=c.update(accepted,3)if accepted else None
        try:binding=c.bind_locations(raw_payload)if raw_payload else None
        except(TypeError,KeyError,ValueError,AttributeError)as exc:binding=dict(errors=[dict(reason='raw_schema_invalid',message=str(exc))])
        elapsed=time.monotonic()-start;tokens=total_usage(result);failures=[]
        if accepted is None:failures.append('no_accepted_final_payload')
        if update and not update['accepted']:failures.append('invalid_final_claim_update')
        if tokens is None:failures.append('unknown_model_usage')
        elif ctx['shared_actual_model_tokens']+tokens>BUDGETS['max_model_tokens']:failures.append('model_token_budget_overrun')
        if ctx['shared_active_wall_seconds']+extra+elapsed>BUDGETS['max_wall_seconds']:failures.append('active_wall_budget_exceeded')
        out=dict(arm=arm,status='error'if failures else'ok',transport_status=result['status'],failures=failures,raw_final_payload=raw_payload,accepted_final_payload=accepted,raw_final_count=len(raw_payload['findings'])if raw_payload else None,final_produced=raw_payload is not None,update=update,binding=binding,controller=c.export(),evidence=list(c.evidence.values()),inherited_shared_context_sha256=sha(ROOT/'prepared/shared_context.json'),source_pool_sha256=digest(ev),same_actual_probe_code_sha256=ctx['probe_code_sha256'],same_plan_sha256=ctx['plan_sha256'],observation_supplied=bool(arm=='observation'and observed),new_model_tokens=tokens,shared_model_tokens=ctx['shared_actual_model_tokens'],logical_model_tokens=None if tokens is None else ctx['shared_actual_model_tokens']+tokens,new_physical_model_calls=result.get('physical_attempt_count',0),logical_model_calls=ctx['shared_model_calls']+result.get('physical_attempt_count',0),new_native_calls=0,active_wall_seconds=dict(shared_common_planner=ctx['shared_active_wall_seconds'],new_probe=extra,new_final=elapsed,total=ctx['shared_active_wall_seconds']+extra+elapsed,other_branch_wait_included=False),source_locations_of_probe_are_repository=False)
        write_new(ROOT/'results'/arm/'review.result.json',out);outcomes.append(dict(arm=arm,status=out['status'],final_produced=out['final_produced']))
    attempted=[r for r in new_results if r.get('physical_attempt_count',0)>0];tokens=[total_usage(r)for r in attempted]
    write_new(ROOT/'results/completed.json',dict(outcomes=outcomes,new_physical_model_calls=sum(r.get('physical_attempt_count',0)for r in attempted),new_physical_model_tokens=None if any(t is None for t in tokens)else sum(tokens),known_partial_new_tokens=sum(t for t in tokens if t is not None),shared_historical_physical_model_calls=3,shared_historical_physical_model_tokens=ctx['shared_actual_model_tokens'],new_native_calls=0,new_probe_attempts=1 if receipt is not None else 0,actual_observation_delivered=observed is not None,physical_wall_seconds=time.monotonic()-physical_started,not_F1_or_generalization_experiment=True))

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('action',choices=('preflight','freeze','verify','run'));a=p.parse_args();print(globals()['verify_freeze'if a.action=='verify'else a.action]())
