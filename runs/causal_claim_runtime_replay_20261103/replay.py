"""One historical seed, shared static review, matched plan/control observations.

No model call, actual-case runtime probe, or source mutation is made by prepare,
preflight, freeze, or imports. Root must invoke run after an independent audit.
"""
import argparse,copy,json,time
from pathlib import Path
from common import ROOT,BUDGETS,PROBE_BUDGETS,ARMS,load,write_new,sha,digest,source_tokens,normalize_block
from replay_controller import ReplayController
from python_tools import PythonTools
from replay_prompts import review_prompt,planner_prompt,compact_binding
from replay_transport import complete,total_usage,accepted_payload,raw_review_payload,admission
from probe_schema import PLAN
from schemas import REVIEW

def setup():
    return load(ROOT/'prepared/cases.json')[0],load(ROOT/'prepared/initial_evidence.json'),load(ROOT/'prepared/seed_payload_v2.json')

def _brief_unresolved(values):
    # Full original tool response is saved before any compact representation.
    keep=('reason','kind','state','operation','symbol','path','message','binding_status')
    return [{k:v[k]for k in keep if k in v}if isinstance(v,dict)else v for v in values]

def perform_queries(case,backend,payload,controller,folder,round_index,wall_started=None):
    questions=payload['questions'];seeded=False
    if not questions and(not payload['claims']or any(p['status']=='unresolved'for c in payload['claims']for p in c['conditions'])):questions=backend.seed_questions();seeded=True
    records=[]
    for i,q in enumerate(questions):
        remaining_wall=BUDGETS['max_wall_seconds']-(time.monotonic()-wall_started)if wall_started is not None else BUDGETS['max_wall_seconds']
        if remaining_wall<=0:value=dict(evidence=[],unresolved=[dict(reason='active_wall_budget_exhausted')],cost={})
        elif source_tokens(list(controller.evidence.values()))>=BUDGETS['max_source_tokens']:
            value=dict(evidence=[],unresolved=[dict(reason='source_token_budget_exhausted')],cost={})
        else:
            backend.budgets['source_tokens_used']=source_tokens(list(controller.evidence.values()))
            backend.budgets['max_query_seconds']=min(BUDGETS['max_query_seconds'],remaining_wall)
            try:value=backend.query(copy.deepcopy(q))
            except Exception as exc:value=dict(evidence=[],unresolved=[dict(reason='backend_exception',message=f'{type(exc).__name__}: {exc}')],cost=dict(measurement_status='unknown_after_exception'))
        admitted=[]
        for raw in value.get('evidence',[]):
            b=normalize_block(raw)
            if b['id']in controller.evidence:continue
            if source_tokens(list(controller.evidence.values())+admitted+[b])>BUDGETS['max_source_tokens']:
                value.setdefault('unresolved',[]).append(dict(reason='full_body_exceeds_remaining_source_tokens',evidence_id=b['id'],body_not_truncated=True));continue
            admitted.append(b)
        try:controller.add_evidence(admitted)
        except Exception as exc:value.setdefault('unresolved',[]).append(dict(reason='evidence_store_rejection',message=str(exc)));admitted=[]
        path=Path(folder)/f'round_{round_index}_query_{i+1:03}.json';write_new(path,dict(question=q,raw_result=value,newly_admitted_ids=[b['id']for b in admitted]))
        records.append(dict(question=q,evidence=[dict(id=b['id'],kind=b['kind'],snapshot=b['snapshot'],path=b['path'],start_line=b['start_line'],end_line=b['end_line'],binding=compact_binding(b.get('binding',{})))for b in admitted],unresolved=_brief_unresolved(value.get('unresolved',[])),raw_reference=dict(path=str(path.resolve()),sha256=sha(path))))
    result=dict(round=round_index,queries=records,source_tokens=source_tokens(list(controller.evidence.values())),controller_seed_used=seeded,hard_query_count_cap=None)
    write_new(Path(folder)/f'query_round_{round_index}.json',result);return result

def unresolved_questions(payload):
    unresolved={c['id']for c in payload['claims']if any(p['status']=='unresolved'for p in c['conditions'])or any(h['state']=='pending'for h in c['challenges'])}
    return [q for q in payload['questions']if q['claim_id']in unresolved]

def _cost(result):return total_usage(result)
def _sum(*values):return None if any(v is None for v in values)else sum(values)

def _save_stage(path,result,payload,controller,round_index):
    update=controller.update(payload,round_index)if payload is not None else None
    row=dict(status=result['status'],raw_payload=raw_review_payload(result),accepted_payload=payload,update=update,actual_tokens=_cost(result),physical_attempts=result.get('physical_attempt_count',0),controller=controller.export())
    write_new(path,row);return row

def preflight(receipt_name='preflight_v3.json'):
    case,ev,seed=setup();c=ReplayController(ev);initial_update=c.update(seed,0)
    records=[];raw=review_prompt(case,ev,seed,c,records,'verify')
    a=admission(raw,REVIEW,0,0);assert a['admitted']
    planner=planner_prompt(case,ev,seed,records,unresolved_questions(seed));b=admission(planner,PLAN,0,2);assert b['admitted']
    native=ROOT/'audit/native_preflight/queries/query_round_1.json';post_query=None
    if native.exists():
        record=load(native)
        for q in record['queries']:
            full=load(q['raw_reference']['path'])
            c.add_evidence([normalize_block(e)for e in full['raw_result'].get('evidence',[])])
        r1=review_prompt(case,list(c.evidence.values()),seed,c,[record],'verify')
        post_query=admission(r1,REVIEW,0,0);assert post_query['admitted']
        import cli_transport
        composed,_=cli_transport.compose_input(cli_transport.DEFAULT_ORDER,r1['system'],r1['shared'],r1['prompt'],r1['suffix'])
        write_new(ROOT/'audit'/(Path(receipt_name).stem+'.first_review.input.json'),dict(raw=r1,composed_text=composed,composed_sha256=post_query['composed_sha256'],scope='Exact composed preflight stdin using actual native R1 query artifacts; main-run native requests are fresh, and final actual stdin is separately transport-recorded. This is not a model response or a runtime probe.'))
    out=dict(passed=True,initial_controller_update=initial_update,verify_stdin=a,planner_stdin=b,actual_native_R1_verify_stdin=post_query,source_reference_tokens=source_tokens(ev),full_source_not_truncated=True,query_cap_count=None,model_calls=0,runtime_case_probes=0,scope='Actual initial and native-R1 composed stdin + schema + reserve; later actual provider input and per-call remaining-token admission rechecked at execution. No synthetic future model response pretended actual.')
    write_new(ROOT/'audit'/receipt_name,out);return out

def freeze():
    assert not(ROOT/'results').exists()
    assert load(ROOT/'audit/independent_audit.json')['passed']
    assert load(ROOT/'audit/tests_final_v2.json')['passed']and load(ROOT/'audit/preflight_v3.json')['passed']
    files={str(p.relative_to(ROOT)):sha(p)for p in ROOT.rglob('*')if p.is_file()and not any(v in p.parts for v in ('__pycache__','.pytest_cache','.pycache'))and p.suffix not in('.pyc','.log')and p.name not in('freeze.json','execution_authorization.json')}
    external=dict(load(ROOT/'parent_dependency_freeze.json')['files'])
    for case in load(ROOT/'prepared/cases.json'):
        for spec in case['snapshots'].values():
            for rel,h in spec['files'].items():external[str(Path(spec['root'])/rel)]=h
            for v in spec.get('external_files',{}).values():external[v['absolute_source']]=v['sha256']
    for p,h in external.items():assert sha(p)==h,('external changed',p)
    write_new(ROOT/'freeze.json',dict(files=files,external_files=external,model_limits=BUDGETS,probe_limits=PROBE_BUDGETS,repeats=1,arms=ARMS,max_physical_model_calls=6,scope='Historical development mechanism replay only. Freeze verifies new code/data/prompt/schema/rubric + allowed source and external runtime dependencies. No result is a generalization/performance sample.'))
    return dict(files=len(files),external_files=len(external))

def verify_freeze():
    f=load(ROOT/'freeze.json')
    for p,h in f['files'].items():assert sha(ROOT/p)==h,('frozen changed',p)
    for p,h in f['external_files'].items():assert sha(p)==h,('external changed',p)
    return True

def run():
    physical_started=time.monotonic()
    verify_freeze();assert load(ROOT/'audit/execution_authorization.json')['authorized']is True
    assert not(ROOT/'results/started.json').exists(),'Resume needs explicit root review of immutable saved calls; no automatic partial replay.'
    write_new(ROOT/'results/started.json',dict(freeze_sha256=sha(ROOT/'freeze.json'),time=time.time()))
    common_started=time.monotonic()
    case,ev,seed=setup();controller=ReplayController(ev);controller.update(seed,0)
    backend=PythonTools(case,ROOT/'results/static_tools',dict(BUDGETS,source_tokens_used=source_tokens(ev)))
    query_records=[]
    try:
        query_records.append(perform_queries(case,backend,seed,controller,ROOT/'results/static_queries',1,common_started))
        raw=review_prompt(case,list(controller.evidence.values()),seed,controller,query_records,'verify')
        first_result=complete(ROOT/'results/shared','verify_1',raw,0,0,remaining_wall=BUDGETS['max_wall_seconds']-(time.monotonic()-common_started));first_payload=accepted_payload(first_result)
        _save_stage(ROOT/'results/shared/verify_1.summary.json',first_result,first_payload,controller,1)
        if first_payload is None:
            write_new(ROOT/'results/failed.json',dict(reason='common_verify_failed',raw_preserved=True));return
        query_records.append(perform_queries(case,backend,first_payload,controller,ROOT/'results/static_queries',2,common_started))
        raw=review_prompt(case,list(controller.evidence.values()),first_payload,controller,query_records,'verify')
        second_result=complete(ROOT/'results/shared','verify_2',raw,_cost(first_result),1,remaining_wall=BUDGETS['max_wall_seconds']-(time.monotonic()-common_started));verify_payload=accepted_payload(second_result)
        _save_stage(ROOT/'results/shared/verify_2.summary.json',second_result,verify_payload,controller,2)
        if verify_payload is None:
            write_new(ROOT/'results/failed.json',dict(reason='second_common_verify_failed',raw_preserved=True));return
        query_records.append(perform_queries(case,backend,verify_payload,controller,ROOT/'results/static_queries',3,common_started))
    finally:write_new(ROOT/'results/static_tools/close.json',backend.close())
    # The exact common post-query source pool is shared by all three branches.
    common_ev=copy.deepcopy(list(controller.evidence.values()));write_new(ROOT/'results/shared/common_evidence.json',common_ev)
    common_elapsed=time.monotonic()-common_started;planner_started=time.monotonic()
    questions=unresolved_questions(verify_payload);plan_payload=None;plan_result=None;plan_tokens=0
    if questions:
        raw=planner_prompt(case,common_ev,verify_payload,query_records,questions)
        plan_result=complete(ROOT/'results/shared','planner',raw,_sum(_cost(first_result),_cost(second_result)),2,PLAN,remaining_wall=BUDGETS['max_wall_seconds']-common_elapsed-(time.monotonic()-planner_started));plan_payload=accepted_payload(plan_result);plan_tokens=_cost(plan_result)
    write_new(ROOT/'results/shared/plan_summary.json',dict(questions=questions,raw_plan_response=(plan_result or {}).get('raw_response'),accepted_plan=plan_payload,actual_tokens=plan_tokens,reason='Actual unresolved reviewer questions only; no question means no model planner or probe is issued.'))
    planner_elapsed=time.monotonic()-planner_started
    observation=None;probe_evidence=None;runtime_reason='No accepted admissible probe plan.';probe_started=time.monotonic()
    if plan_payload is not None and plan_payload['assessment']=='propose':
        from probe import execute,evidence,validate_probe
        try:
            assert len(plan_payload['probes'])<=PROBE_BUDGETS['max_probes']
            if plan_payload['probes']:
                pp=plan_payload['probes'][0];validate_probe(pp,questions,load(ROOT/'prepared/public_imports.json'))
                remaining=BUDGETS['max_wall_seconds']-common_elapsed-planner_elapsed-(time.monotonic()-probe_started)
                observation=execute(pp,questions,load(ROOT/'prepared/public_imports.json'),ROOT/'results/probe',remaining_wall=remaining)
                if observation['valid']:probe_evidence=evidence(pp,observation,ROOT/'results/probe/receipt.json')
                runtime_reason='Actual analysis observation supplied.'if probe_evidence else'Actual probe failed; raw output preserved and no successful observation evidence is fabricated.'
        except Exception as exc:
            runtime_reason='Probe rejected/unavailable: '+type(exc).__name__+': '+str(exc)
            write_new(ROOT/'results/probe_rejected.json',dict(reason=runtime_reason,plan=plan_payload))
    probe_elapsed=time.monotonic()-probe_started
    results=[];physical_results=[first_result,second_result]+([plan_result]if plan_result is not None else[])
    for arm in ARMS:
        branch_started=time.monotonic()
        branch_controller=ReplayController(common_ev);branch_controller.update(seed,0);branch_controller.update(first_payload,1);branch_controller.update(verify_payload,2)
        shown_plan=plan_payload if arm!='static'else None
        if arm=='observation'and probe_evidence is not None:branch_controller.add_evidence([probe_evidence])
        status=None if arm=='static'else dict(scope='analysis_profile',observations_provided=arm=='observation'and probe_evidence is not None,reason=runtime_reason if arm=='observation'else'Plan-only extra-review control: the same probe plan is supplied; no runtime observation or output is supplied.',deployed_runtime_confirmed=False)
        used=_sum(_cost(first_result),_cost(second_result),plan_tokens if arm!='static'else 0);calls=2+(int((plan_result or{}).get('physical_attempt_count',0)>0)if arm!='static'else 0)
        raw=review_prompt(case,list(branch_controller.evidence.values()),verify_payload,branch_controller,query_records,'final',shown_plan,status)
        extra=0 if arm=='static'else planner_elapsed+(probe_elapsed if arm=='observation'else 0)
        remaining_wall=BUDGETS['max_wall_seconds']-common_elapsed-extra-(time.monotonic()-branch_started)
        result=complete(ROOT/'results'/arm,'final',raw,used,calls,remaining_wall=remaining_wall);payload=accepted_payload(result)
        physical_results.append(result)
        final=_save_stage(ROOT/'results'/arm/'summary.json',result,payload,branch_controller,3)
        raw_payload=final['raw_payload']
        try:binding=branch_controller.bind_locations(raw_payload)if raw_payload else None
        except (TypeError,KeyError,ValueError,AttributeError)as exc:binding=dict(errors=[dict(reason='raw_schema_invalid_for_binding',message=str(exc))])
        logical_tokens=_sum(used,_cost(result));failures=[]
        if result['status']!='ok':failures.append('transport_or_schema_failure')
        if payload is None:failures.append('no_accepted_final_payload')
        if raw_payload is None:failures.append('final_not_produced')
        if final['update']is not None and not final['update']['accepted']:failures.append('invalid_final_claim_update')
        if logical_tokens is None:failures.append('unknown_model_usage')
        elif logical_tokens>BUDGETS['max_model_tokens']:failures.append('model_token_budget_overrun')
        branch_elapsed=time.monotonic()-branch_started
        if common_elapsed+extra+branch_elapsed>BUDGETS['max_wall_seconds']:failures.append('active_wall_budget_exceeded')
        out=dict(arm=arm,raw_final_payload=raw_payload,accepted_final_payload=payload,status='error'if failures else'ok',transport_status=result['status'],failures=failures,final_produced=raw_payload is not None,raw_final_count=len(raw_payload['findings'])if raw_payload else None,known_common_source_sha256=digest(common_ev),probe_plan_sha256=digest(plan_payload)if shown_plan is not None else None,observation_supplied=bool(arm=='observation'and probe_evidence),model_tokens_logical=logical_tokens,model_calls_logical=calls+int(result.get('physical_attempt_count',0)>0),common_review_references=[str(ROOT/'results/shared/verify_1.summary.json'),str(ROOT/'results/shared/verify_2.summary.json')],controller=branch_controller.export(),binding=binding,full_evidence=list(branch_controller.evidence.values()),query_records=query_records)
        out['active_wall_seconds']=dict(common=common_elapsed,planner=planner_elapsed if arm!='static'else 0,probe=probe_elapsed if arm=='observation'else 0,final_branch=branch_elapsed,total=common_elapsed+extra+branch_elapsed,other_branch_wait_included=False)
        write_new(ROOT/'results'/arm/'review.result.json',out);results.append(dict(arm=arm,status=out['status'],final_produced=out['final_produced']))
    attempted=[r for r in physical_results if r.get('physical_attempt_count',0)>0]
    tokens=[_cost(r)for r in attempted]
    write_new(ROOT/'results/completed.json',dict(results=results,model_calls_physical_max=6,actual_physical_model_calls=sum(r.get('physical_attempt_count',0)for r in physical_results),actual_model_tokens_physical=_sum(*tokens),known_partial_model_tokens_physical=sum(t for t in tokens if t is not None),model_usage_unknown=any(t is None for t in tokens),shared_calls_physically_counted_once=True,physical_wall_seconds=time.monotonic()-physical_started,not_generalization_experiment=True))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('preflight','freeze','verify','run'));a=parser.parse_args()
    print(globals()['verify_freeze'if a.action=='verify'else a.action]())
