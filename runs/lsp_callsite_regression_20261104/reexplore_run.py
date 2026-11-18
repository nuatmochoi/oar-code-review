"""Actual response-conditioned second acquisition, paired with withheld control."""
import copy,json,time,traceback,random
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
import jsonschema
from common import ROOT,PREVIOUS,PREVIOUS_METHOD,REPEATS,WORKERS,BUDGETS,load,sha,digest,write_new,source_tokens
from schemas import REVIEW as BASE_REVIEW
from reexplore_schema import INITIAL,REPLAN,plan_only,review_only,audit_replan
from condition_roles import with_condition_roles,audit as role_audit
from reexplore_prompts import initial_prompt,replan_prompt,final_prompt
from prompts_lsp48 import changed_operations
from acquisition_frontier import Frontier
from frontier import request_key
from diff48 import diff_evidence
from budget_context import public_view
from warning_tracker import WarningTracker
from transport48 import complete,admission,parse_payload,total_usage
from openai_transport import request_body
from bound_context import ContextCollector
from experiment_lsp48 import provider_ref,valid_payload,planning_view

FINAL=with_condition_roles(BASE_REVIEW)

def context_for(case):return load(ROOT/'contexts'/case['id']/'context_LSP.json')
def evidence_for(case,context):return public_view(case,context['evidence'])[1]
def view_case(case):return public_view(case,context_for(case)['evidence'])[0]
def shared_folder(case,n):return ROOT/'results/shared'/f'{case["id"]}_r{n}'
def plan_folder(case,n):return ROOT/'results/plans'/f'{case["id"]}_r{n}'
def shared_path(case,n):return shared_folder(case,n)/'shared.result.json'
def status_context(context,**extra):return {**copy.deepcopy(context),**extra}
def blank_execution(reason):return dict(accepted=False,complete=False,answers=[],new_evidence_ids=[],selected_evidence_ids=[],backend_query_attempts=0,validation={'accepted':False,'complete':False,'errors':[{'code':reason}]},semantic_truth_guaranteed=False)

class RecordedAnswers:
    def __init__(self,execution):self.values={a['question']['id']:a for a in execution.get('answers',[])}
    def query(self,q):
        a=self.values[q['id']];assert request_key(a['question'])==request_key(q)
        # A native response is the replay input, never the already enriched
        # controller output. Otherwise derived unresolved markers accumulate.
        if a.get('query_receipt'):
            raw=load(a['query_receipt']);assert request_key(raw['question'])==request_key(q)
            raw['query_receipt']=a['query_receipt'];return raw
        raw=copy.deepcopy(a)
        for key in ('all_answer_evidence_ids','mechanically_compatible_evidence_ids','question_binding','execution','cache_hit'):raw.pop(key,None)
        raw['unresolved']=[u for u in raw.get('unresolved',[])if u.get('reason')!='actual_complete_implementation_not_available']
        return raw

def replay_first(case,initial,evidence,execution,context):
    f=Frontier(review_only(initial),evidence,changed_operations=changed_operations(view_case(case),evidence))
    rebuilt=f.execute_plan(plan_only(initial),RecordedAnswers(execution),enable_queries=True)
    assert digest(rebuilt)==digest(execution),'Actual first-query controller replay drift'
    f.controller.add_evidence(evidence_for(case,context))
    return f

def reserve_final(case,evidence,initial=None,plan=None,frontier=None,first=None):
    initial=initial or dict(assessment='uncertain',findings=[],claims=[],questions=[],reasoning='',termination_reason='')
    raw=final_prompt(view_case(case),evidence,initial,plan or {},blank_execution('prospective'),frontier or {},{}, {},first or {},[])
    return admission(raw,FINAL)['required_reserve']+10000

def initial_admission(case):
    context=context_for(case);es=evidence_for(case,context);raw=initial_prompt(view_case(case),es)
    empty=Frontier(None,es,changed_operations=changed_operations(view_case(case),es))
    reserve=reserve_final(case,es)+16000
    return raw,reserve,admission(raw,INITIAL,final_reserve=reserve)

def record_call(path,label):return provider_ref(path,label) if path.exists() else None
def usage(stages):
    unknown=any(s.get('provider_tokens')is None for s in stages)
    known=sum(s.get('provider_tokens')or 0 for s in stages)
    return (None if unknown else known),sum(s.get('physical_attempt_count',0)for s in stages),known

def shared_one(case,n):
    dest=shared_path(case,n)
    if dest.exists():return load(dest)
    reused=ROOT/'prepared/shared_reuse_manifest.json'
    ref=load(reused)['shared'].get(f'{case["id"]}_r{n}')if reused.exists()else None
    if ref:
        assert sha(ref['path'])==ref['sha256']
        row=load(ref['path']);raw,_,_=initial_admission(case)
        assert digest(request_body(raw,INITIAL))==digest(load(ref['initial_request'])),'Reused shared initial request changed'
        assert row['initial_evidence']==evidence_for(case,context_for(case))
        # Reconstruct every controller transition, including failed queries,
        # before adopting the unchanged prefix. No new model/native calls.
        f=replay_first(case,row['initial'],row['initial_evidence'],row['first_execution'],row['context'])
        used,calls,_=usage(row['stages'][:1]);visible=evidence_for(case,row['context'])
        raw2=replan_prompt(view_case(case),visible,row['initial'],row['first_execution'],f,{'model_tokens':None if used is None else BUDGETS['max_model_tokens']-used,'model_calls':3-calls,'source_tokens':12000-source_tokens(visible)})
        assert digest(request_body(raw2,REPLAN))==digest(load(ref['replan_request'])),'Reused shared replan request changed'
        folder=shared_folder(case,n);pf=plan_folder(case,n)
        write_new(folder/'prefix.reused_reference.json',ref)
        write_new(folder/'first_query_execution.json',row['first_execution'])
        write_new(folder/'first_context.json',row['context'])
        write_new(pf/'plan.result.json',load(ref['plan_path']))
        write_new(dest,row);return row
    folder=shared_folder(case,n);pf=plan_folder(case,n);context=context_for(case);es=evidence_for(case,context)
    started=time.monotonic();raw,reserve,_=initial_admission(case)
    write_new(folder/'initial.delivered_evidence.json',dict(evidence=es,evidence_ids=[e['id']for e in es],evidence_sha256=digest(es),context=context))
    manifest=load(ROOT/'prepared/prior_initial_manifest.json')if(ROOT/'prepared/prior_initial_manifest.json').exists()else {'initials':{}}
    prior=manifest['initials'].get(f'{case["id"]}_r{n}')
    if prior:
        assert sha(prior['provider_result'])==prior['provider_sha256']and sha(prior['request'])==prior['request_sha256']and sha(prior['delivery'])==prior['delivery_sha256']
        assert digest(load(prior['request']))==digest(request_body(raw,INITIAL)),'Reused initial request changed'
        assert load(prior['delivery'])['evidence']==es,'Reused initial actual delivery changed'
        write_new(folder/'initial.reused_reference.json',prior)
        response=load(prior['provider_result']);first_ref=provider_ref(Path(prior['provider_result']),'reused_actual_initial_review_and_queries')
    else:
        response=complete(folder,'initial_review',raw,INITIAL,final_reserve=reserve)
        first_ref=provider_ref(folder/'initial_review/initial.result.json','initial_review_and_queries')
    reused_seconds=response.get('timing',{}).get('duration_seconds',0)if prior else 0
    initial=valid_payload(response,INITIAL);execution=blank_execution('no_normal_initial_review');f=None;replan=None;answer_audit=None;validation=None
    stages=[first_ref];base_seconds=time.monotonic()-started+reused_seconds; initial_seconds=base_seconds
    if initial is not None:
        f=Frontier(review_only(initial),es,changed_operations=changed_operations(view_case(case),es));backend=None
        try:
            validation=f.validate_plan(plan_only(initial))
            if initial['questions']:
                backend=ContextCollector(case,folder/'first_queries',BUDGETS,initial_state=context)
            execution=f.execute_plan(plan_only(initial),backend,enable_queries=True)
        finally:
            if backend is not None:
                try:backend.close()
                finally:context=backend.export()
        f.controller.add_evidence(evidence_for(case,context))
        write_new(folder/'first_query_execution.json',execution)
        write_new(folder/'first_context.json',context)
        base_seconds=time.monotonic()-started+reused_seconds
        used,calls,_=usage(stages);visible=evidence_for(case,context)
        view=view_case(case);pbase={'evidence':visible};scope=view['budget_view']
        raw2=replan_prompt(view,pbase['evidence'],initial,execution,f,{'model_tokens':None if used is None else BUDGETS['max_model_tokens']-used,'model_calls':3-calls,'source_tokens':12000-source_tokens(visible)})
        reserve2=reserve_final(case,visible,review_only(initial),{},f.export(),execution)
        write_new(pf/'planner.delivered_evidence.json',dict(evidence=pbase['evidence'],evidence_ids=[e['id']for e in pbase['evidence']],first_execution_reference={'path':str(folder/'first_query_execution.json'),'sha256':sha(folder/'first_query_execution.json')},first_frontier=f.export(),planning_scope=scope))
        second=complete(pf,'planner',raw2,REPLAN,used,calls,max(0,900-reused_seconds-(time.monotonic()-started)),final_reserve=reserve2)
        stages.append(provider_ref(pf/'planner/initial.result.json','response_conditioned_replan'))
        replan=valid_payload(second,REPLAN)
        if replan is not None:
            validation=f.validate_plan(plan_only(replan));answer_audit=audit_replan(replan,execution,pbase['evidence'])
    write_new(pf/'plan.result.json',dict(case_id=case['id'],repeat=n,status='ok'if replan is not None and validation and validation.get('accepted')else'invalid_or_unattempted',payload=plan_only(replan)if replan else None,raw_payload=replan,validation=validation,answer_audit=answer_audit,stages=stages[1:]))
    row=dict(case_id=case['id'],repeat=n,initial=initial,initial_evidence=es,initial_provider_reference={'path':first_ref['reference'],'sha256':first_ref['sha256']},context=context,first_execution=execution,first_frontier=f.export()if f else None,replan=replan,replan_validation=validation,answer_audit=answer_audit,stages=stages,active_wall_seconds=time.monotonic()-started+reused_seconds,first_stage_seconds=base_seconds,initial_seconds=initial_seconds,exception=None)
    write_new(dest,row);print(json.dumps({'shared':case['id'],'repeat':n,'initial':initial is not None,'first_queries':len(execution.get('answers',[])),'replan':replan is not None,'next_questions':len(replan['questions'])if replan else 0}),flush=True)
    return row

def safe_shared(case,n):
    try:return shared_one(case,n)
    except Exception as error:
        p=shared_path(case,n)
        if p.exists():return load(p)
        folder=shared_folder(case,n);pf=plan_folder(case,n);refs=[]
        first_path=Path(load(folder/'initial.reused_reference.json')['provider_result'])if(folder/'initial.reused_reference.json').exists()else folder/'initial_review/initial.result.json'
        for file,label in((first_path,'initial_review_and_queries'),(pf/'planner/initial.result.json','response_conditioned_replan')):
            if file.exists():refs.append(provider_ref(file,label))
        first=valid_payload(load(first_path),INITIAL)if first_path.exists()else None
        context=load(folder/'first_context.json')if(folder/'first_context.json').exists()else context_for(case)
        if (folder/'first_queries').exists() and not(folder/'first_context.json').exists():context['cost_unknown']=True
        row=dict(case_id=case['id'],repeat=n,initial=first,initial_evidence=evidence_for(case,context_for(case)),initial_provider_reference={'path':str(first_path),'sha256':sha(first_path)}if first_path.exists()else None,context=context,first_execution=load(folder/'first_query_execution.json')if(folder/'first_query_execution.json').exists()else blank_execution('shared_runner_exception'),replan=None,stages=refs,active_wall_seconds=sum(s.get('timing',{}).get('duration_seconds',0)for s in refs),wall_unknown=True,exception=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
        if not(pf/'plan.result.json').exists():write_new(pf/'plan.result.json',dict(case_id=case['id'],repeat=n,status='runner_exception',payload=None,validation=None,stages=refs[1:]))
        write_new(p,row);return row

def branch_body(case,n,arm,shared):
    folder=ROOT/'results/reviews'/f'{case["id"]}_{arm}_r{n}';started=time.monotonic();context=copy.deepcopy(shared['context'])
    initial=shared['initial'];payload2=shared.get('replan');execution=blank_execution('no_replan');tracker=WarningTracker(review_only(initial)if initial else None,shared['initial_evidence']);f=None
    provider=None;delivery=None;aux=None;waudit=None;literal=None;roles=None;error=None
    if initial is not None:
        f=replay_first(case,initial,shared['initial_evidence'],shared['first_execution'],context)
        tracker.record_plan(plan_only(initial),shared['first_execution'].get('validation'));tracker.record_answers(shared['first_execution']);tracker.add_evidence(evidence_for(case,context))
        backend=None
        if payload2 is not None:
            plan=plan_only(payload2);tracker.record_plan(plan,shared.get('replan_validation'))
            try:
                if arm=='AGENTIC' and plan['questions']:
                    backend=ContextCollector(case,folder/'dynamic_queries',{**BUDGETS,'max_wall_seconds':max(.01,900-shared['active_wall_seconds'])},initial_state=context)
                execution=f.execute_plan(plan,backend,enable_queries=arm=='AGENTIC')
            finally:
                if backend is not None:
                    try:backend.close()
                    finally:context=backend.export()
        else:plan=plan_only(initial)
        tracker.record_answers(execution);es=evidence_for(case,context);tracker.add_evidence(es);f.controller.add_evidence(es)
        used,calls,_=usage(shared['stages']);remaining={'provider_tokens':None if used is None else BUDGETS['max_model_tokens']-used,'model_calls':3-calls,'source_tokens':12000-source_tokens(es)}
        raw=final_prompt(view_case(case),es,review_only(initial),plan,execution,f.export(),remaining,tracker.prompt_state(),shared['first_execution'],payload2.get('answer_assessments',[])if payload2 else [])
        allqs=[q['id']for q in initial['questions']]+[q['id']for q in(payload2 or{}).get('questions',[])]
        schema=with_condition_roles(BASE_REVIEW,warning_ids=[w['warning_id']for w in tracker.catalog()],evidence_ids=[e['id']for e in es],question_ids=list(dict.fromkeys(allqs)))
        delivery=dict(evidence=es,evidence_ids=[e['id']for e in es],evidence_sha256=digest(es),source_tokens=source_tokens(es),context=copy.deepcopy(context),controller_before_call=f.controller.export(),frontier_before_call=f.export(),execution=execution,warning_tracker_before_call=tracker.export())
        write_new(folder/'query_execution.json',execution);write_new(folder/'final.delivered_evidence.json',delivery);write_new(folder/'final.review_schema.json',schema)
        provider=complete(folder,'final',raw,schema,used,calls,max(0,900-shared['active_wall_seconds']-(time.monotonic()-started)))
        output=valid_payload(provider,FINAL)
        if output is not None:
            literal=f.check_literal_conditions(output);waudit=tracker.audit_final(output,delivered_evidence=es)
            roles=role_audit(output,es,case['public_contract'],prior_claims=plan['claims']);aux=f.controller.update(output,len(f.versions)+1)
            write_new(folder/'final.auxiliary_update.json',dict(update=aux,literal_fact_check=literal,warning_audit=waudit,condition_role_audit=roles,controller_after_call=f.controller.export()))
    if waudit is None:tracker.record_no_new_final('No normal new final; only an actual fresh initial review may be retained, never a plan.')
    write_new(folder/'warning_trace.json',tracker.export())
    reused_final_seconds=provider.get('timing',{}).get('duration_seconds',0)if provider and(folder/'final/reused.actual.json').exists()else 0
    return make_result(case,n,arm,shared,context,execution,delivery,provider,time.monotonic()-started+reused_final_seconds,aux,waudit,literal,roles,error)

def make_result(case,n,arm,shared,context,execution,delivery,provider,seconds,aux=None,waudit=None,literal=None,roles=None,error=None):
    rid=f'{case["id"]}_{arm}_r{n}';folder=ROOT/'results/reviews'/rid;stages=copy.deepcopy(shared['stages'])
    finalp=folder/'final/initial.result.json'
    if finalp.exists():stages.append(provider_ref(finalp,'new_final'))
    output=valid_payload(provider,FINAL)if provider else None
    fresh=bool(output is not None and delivery)
    selected=output if fresh else shared['initial'];es=delivery['evidence']if fresh else shared['initial_evidence']if selected is not None else []
    actual_final=provider_ref(finalp,'new_final')if fresh else None
    ref=({'path':actual_final['reference'],'sha256':actual_final['sha256']}if fresh else shared['initial_provider_reference']if selected else None)
    used,calls,known=usage(stages);elapsed=shared['active_wall_seconds']+seconds;cost=context['cost'];failures=[]
    if used is None:failures.append('model_usage_unknown')
    if context.get('cost_unknown'):failures.append('native_cost_unknown')
    if shared.get('wall_unknown'):failures.append('active_wall_unknown')
    for value,cap,name in((known,BUDGETS['max_model_tokens'],'model_token'),(calls,3,'model_calls'),(elapsed,900,'active_wall'),(source_tokens(context['evidence']),12000,'collected_source'),(source_tokens(es),12000,'delivered_source'),(cost.get('native_calls',0),48,'native_calls'),(cost.get('native_seconds',0),180,'native_seconds'),(cost.get('file_reads',0),256,'file_reads')):
        if value>cap:failures.append(name+'_overrun')
    report_status='ok'if selected is not None and not failures else'error'if selected is not None or calls else'unattempted'
    firstkeys={request_key(a['question'])for a in shared['first_execution'].get('answers',[])}
    novel=[a['question']['id']for a in execution.get('answers',[])if request_key(a['question'])not in firstkeys and a.get('execution')=='backend_query']
    firstids={e['id']for e in evidence_for(case,shared['context'])};newids=[e['id']for e in es if e['id']not in firstids]if fresh else []
    return dict(review_id=rid,case_id=case['id'],arm=arm,repeat=n,language=case['language'],model=__import__('common').MODEL,
      report_status=report_status,status=report_status,payload=selected,raw_final_payload=selected,accepted_final_payload=selected,
      final_produced=selected is not None,actual_raw_final_response_present=selected is not None,raw_findings_count=len(selected['findings'])if selected else 0,raw_findings_count_measurable=True,
      evidence=es,final_input_evidence_ids=[e['id']for e in es],source_tokens=source_tokens(es),delivered_source_tokens_measured=source_tokens(es),collected_source_tokens=source_tokens(context['evidence']),
      final_origin='new_final'if fresh else'shared_fresh_initial_final'if selected else'none',final_new_generated=fresh,fallback_to_initial=bool(selected and not fresh),
      actual_final_provider_reference=ref,auxiliary_final_update_accepted=aux.get('accepted')if aux else None,
      initial_reference={'path':str(shared_path(case,n)),'sha256':sha(shared_path(case,n))},stages=stages,logical_model_calls=calls,actual_model_tokens=used,known_model_tokens=known,model_usage_unknown=used is None,
      context_cost=cost,context_cost_unknown=bool(context.get('cost_unknown')),active_wall_seconds=elapsed,failures=failures,exception=error or shared.get('exception'),
      method_version='actual_response_conditioned_reexploration',freeze_sha256=sha(ROOT/'freeze.json'),source_pool_hash=case.get('source_content_hash'),scip_used=False,runtime_probe_calls=0,
      method_completed=bool(fresh and execution.get('accepted')and waudit and waudit.get('accepted')and report_status=='ok'),query_completed=bool(arm=='AGENTIC'and novel),verification_completed=bool(shared.get('replan')),
      execution=execution,warning_trace=waudit,literal_fact_check=literal,condition_role_audit=roles,
      first_query_count=shared['first_execution'].get('backend_query_attempts',0),first_question_response_count=len(shared['first_execution'].get('answers',[])),followup_query_count=len(execution.get('answers',[])),novel_followup_question_ids=novel,followup_new_delivered_evidence_ids=newids,followup_new_delivered_complete_body_ids=[e['id']for e in es if e['id']in newids and e.get('mechanical_checks',{}).get('complete_unit')and e.get('mechanical_checks',{}).get('unit_kind')in('function','class','closure','method','function_literal')],
      response_conditioned_chain_executed=bool(shared['first_execution'].get('backend_query_attempts',0)>0 and shared.get('replan')and novel),semantic_verification_success_guaranteed=False,
      source_condition='Same public inputs and frozen repository/dependency source pool; no SCIP, probe, private gold or internet access.')

def safe_branch(case,n,arm):
    folder=ROOT/'results/reviews'/f'{case["id"]}_{arm}_r{n}';dest=folder/'review.result.json'
    if dest.exists():return load(dest)
    shared=load(shared_path(case,n));start=time.monotonic()
    try:row=branch_body(case,n,arm,shared)
    except Exception as error:
        def optional(p):return load(p)if p.exists()else None
        delivery=optional(folder/'final.delivered_evidence.json');provider=optional(folder/'final/initial.result.json');execution=optional(folder/'query_execution.json')or blank_execution('branch_runner_exception')
        context=copy.deepcopy(delivery['context']if delivery else shared['context'])
        if (folder/'dynamic_queries').exists()and delivery is None:context['cost_unknown']=True
        row=make_result(case,n,arm,shared,context,execution,delivery,provider,time.monotonic()-start,error=type(error).__name__+': '+str(error));row['traceback']=traceback.format_exc()
    write_new(dest,row);print(json.dumps({'review':row['review_id'],'origin':row['final_origin'],'followup_queries':row['followup_query_count'],'new_source':len(row['followup_new_delivered_evidence_ids']),'exception':row['exception']}),flush=True);return row

def copy_old(case,n,arm):
    dest=ROOT/'results/reviews'/f'{case["id"]}_{arm}_r{n}'/'review.result.json'
    if dest.exists():return
    source=PREVIOUS/'results/reviews'/f'{case["id"]}_B2_r{n}'/'review.result.json'if arm=='FIXED'else PREVIOUS_METHOD/'results/reviews'/f'{case["id"]}_{arm.removeprefix("OLD_")}_r{n}'/'review.result.json'
    row=load(source)
    if arm=='OLD_AGENTIC' and row.get('acquisition_reference'):
        acquisition=load(row['acquisition_reference']['path']);row['logical_context_cost_correction']={'reference':row['acquisition_reference'],'reason':'Charge full prior acquisition including backend close, not only pre-final delivery.'};row['context_cost']=acquisition['context_cost']
    row.update(review_id=dest.parent.name,arm=arm,repeat=n,reused_previous_method_output=True,previous_method_reference={'path':str(source),'sha256':sha(source)},new_physical_model_in_this_run=False)
    if arm=='FIXED':row['final_origin']='shared_B2_initial_final';row['final_new_generated']=False
    write_new(dest,row)

def run(cohort="full"):
    from transport48 import credentials_ready
    credentials_ready()
    from freeze_lsp48 import verify_freeze
    verify_freeze();cases=load(ROOT/'prepared/cases.json')
    if cohort=='interim':
        chosen=set(load(ROOT/'prepared/interim_cohort.json')['case_ids']);cases=[c for c in cases if c['id']in chosen]
    jobs=[(c,n)for c in cases for n in range(1,REPEATS+1)];random.Random(20261104).shuffle(jobs)
    for c,n in jobs:
        for a in('FIXED','OLD_AGENTIC'):copy_old(c,n,a)
    with ProcessPoolExecutor(max_workers=WORKERS)as pool:
        for f in as_completed([pool.submit(safe_shared,c,n)for c,n in jobs]):f.result()
    for c,n in jobs:copy_initial(c,n)
    branches=[(c,n,a)for c,n in jobs for a in('EXTRA','AGENTIC')];random.Random(20261105).shuffle(branches)
    with ProcessPoolExecutor(max_workers=WORKERS)as pool:
        for f in as_completed([pool.submit(safe_branch,*x)for x in branches]):f.result()

def copy_initial(case,n):
    shared=load(shared_path(case,n));initial=copy.deepcopy(shared)
    initial.update(stages=shared['stages'][:1],context=context_for(case),active_wall_seconds=shared.get('initial_seconds',shared['stages'][0].get('timing',{}).get('duration_seconds',0))if shared['stages']else 0)
    folder=ROOT/'results/reviews'/f'{case["id"]}_INITIAL_r{n}';dest=folder/'review.result.json'
    if dest.exists():return
    row=make_result(case,n,'INITIAL',initial,initial['context'],blank_execution('initial_review_only'),None,None,0)
    row.update(fallback_to_initial=False,method_completed=False,verification_completed=False)
    write_new(dest,row)

if __name__=='__main__':
    import sys
    run(sys.argv[1]if len(sys.argv)>1 else'full')
