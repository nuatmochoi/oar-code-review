"""Reuse exact actual initial+first acquisition, then apply new scope policy.

The old replan/final are never used in this new condition. Logical time/model
and native costs include the reused prefix; physical new calls are separate.
"""
import copy,json,time
from pathlib import Path
from common import ROOT,BUDGETS,load,sha,digest,write_new,source_tokens
from reexplore_schema import INITIAL,REPLAN,review_only,plan_only,audit_replan
from claim_scope import audit as scope_audit

def shared_from_first(case,n):
    import reexplore_run as r
    started=time.monotonic();folder=r.shared_folder(case,n);pf=r.plan_folder(case,n)
    ref=load(ROOT/'prepared/first_acquisition_manifest.json')['units'][f'{case["id"]}_r{n}']
    assert sha(ref['path'])==ref['sha256'];old=load(ref['path'])
    prior=load(ROOT/'prepared/prior_initial_manifest.json')['initials'][f'{case["id"]}_r{n}']
    raw,_,_=r.initial_admission(case);es=r.evidence_for(case,r.context_for(case))
    assert digest(r.request_body(raw,INITIAL))==digest(load(prior['request']))
    assert old['initial_evidence']==es==load(prior['delivery'])['evidence']
    assert sha(prior['provider_result'])==prior['provider_sha256']
    initial=r.valid_payload(load(prior['provider_result']),INITIAL);assert initial==old['initial']
    first_ref=r.provider_ref(Path(prior['provider_result']),'reused_actual_initial_review_and_queries')
    execution=copy.deepcopy(old['first_execution']);context=copy.deepcopy(old['context'])
    f=r.replay_first(case,initial,es,execution,context)
    write_new(folder/'initial.reused_reference.json',prior)
    write_new(folder/'first_acquisition.reused_reference.json',ref)
    write_new(folder/'initial.delivered_evidence.json',dict(evidence=es,evidence_ids=[e['id']for e in es],evidence_sha256=digest(es),context=r.context_for(case)))
    write_new(folder/'first_query_execution.json',execution);write_new(folder/'first_context.json',context)
    prefix_seconds=old['first_stage_seconds'];stages=[first_ref];used,calls,_=r.usage(stages);visible=r.evidence_for(case,context);view=r.view_case(case)
    raw2=r.replan_prompt(view,visible,initial,execution,f,{'model_tokens':BUDGETS['max_model_tokens']-used if used is not None else None,'model_calls':3-calls,'source_tokens':12000-source_tokens(visible)})
    reserve=r.reserve_final(case,visible,review_only(initial),{},f.export(),execution)
    write_new(pf/'planner.delivered_evidence.json',dict(evidence=visible,evidence_ids=[e['id']for e in visible],first_execution_reference={'path':str(folder/'first_query_execution.json'),'sha256':sha(folder/'first_query_execution.json')},first_frontier=f.export(),planning_scope=view['budget_view']))
    second=r.complete(pf,'planner',raw2,REPLAN,used,calls,max(0,900-prefix_seconds-(time.monotonic()-started)),final_reserve=reserve)
    stages.append(r.provider_ref(pf/'planner/initial.result.json','scope_separation_and_response_conditioned_replan'))
    replan=r.valid_payload(second,REPLAN);validation=f.validate_plan(plan_only(replan))if replan is not None else None
    aa=audit_replan(replan,execution,visible)if replan is not None else None;sa=scope_audit(replan,visible,initial['claims'])if replan is not None else None
    write_new(pf/'plan.result.json',dict(case_id=case['id'],repeat=n,status='ok'if replan is not None else'invalid_or_unattempted',payload=plan_only(replan)if replan else None,raw_payload=replan,validation=validation,answer_audit=aa,claim_scope_audit=sa,stages=stages[1:]))
    row=dict(case_id=case['id'],repeat=n,initial=initial,initial_evidence=es,initial_provider_reference={'path':first_ref['reference'],'sha256':first_ref['sha256']},context=context,first_execution=execution,first_frontier=f.export(),replan=replan,replan_validation=validation,answer_audit=aa,claim_scope_audit=sa,stages=stages,active_wall_seconds=prefix_seconds+(time.monotonic()-started),first_stage_seconds=prefix_seconds,initial_seconds=old['initial_seconds'],exception=None,first_acquisition_reused_reference=ref)
    write_new(r.shared_path(case,n),row);print(json.dumps(dict(shared=case['id'],repeat=n,first_acquisition_reused=True,replan=replan is not None,scope_audit=sa,next_questions=len(replan['questions'])if replan else 0)),flush=True)
    return row
