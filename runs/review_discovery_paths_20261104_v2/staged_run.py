"""Fresh common initial, then three independently budgeted static policies."""
import copy,json,time,traceback,random
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from common import ROOT,load,sha,digest,write_new,BUDGETS,WORKERS,source_tokens
import reexplore_run as r
from discovery_policy import INITIAL,REPLAN,INTENT_POLICY,DISCOVERY_POLICY,seed_catalog,compact_seeds,audit as discovery_audit,intent_for
from persistent_context import ContextCollector,release_budget_cached_answers
from controller_frontier import acquire as generic_acquire,compact
from question_frontier import acquire as goal_acquire
from reexplore_schema import plan_only,review_only,audit_replan
from claim_scope import audit as scope_audit
from condition_roles import with_condition_roles,audit as role_audit
from claim_scope import with_claim_scopes
from schemas import REVIEW

BRANCHES=('R0','R1','R2')
def units():return load(ROOT/'prepared/units.json')['units']
def expected():return [f'{u["case_id"]}_{a}_r{u["repeat"]}'for u in units()for a in('INITIAL',)+BRANCHES]
def initial_path(case,n):return ROOT/'results/shared'/f'{case["id"]}_r{n}'/'shared.result.json'
def folder_for(case,n,arm):return ROOT/'results/reviews'/f'{case["id"]}_{arm}_r{n}'
def context_wall(ctx):return max(ctx['cost'].get('native_seconds',0),sum(s.get('seconds',0)for s in ctx.get('stages',{}).values()))
def attach(f,case,context):
    es=r.evidence_for(case,context);f.controller.add_evidence(es);f.evidence.update({e['id']:copy.deepcopy(e)for e in es});return es

def initial_one(case,n):
    dest=initial_path(case,n)
    if dest.exists():return load(dest)
    started=time.monotonic();folder=dest.parent;context=r.context_for(case);es=r.evidence_for(case,context);raw=r.initial_prompt(r.view_case(case),es);raw['system']+='\n'+INTENT_POLICY;raw['prompt']+='\nReturn retrieval_intents along with the initial schema. Keep at most 3 substantive provisional claims and at most 4 targeted questions; zero claims is allowed. Prioritize distinct causes over redundant variants.'
    reserve=r.reserve_final(case,es)+48000
    write_new(folder/'initial.delivered_evidence.json',dict(evidence=es,evidence_ids=[e['id']for e in es],evidence_sha256=digest(es),context=context,full_original_diff_sha256=__import__('hashlib').sha256(case['diff'].encode()).hexdigest(),note='Fresh initial for full original diff; no prior initial response reused.'))
    reuse_path=ROOT/'prepared/shared_initials.json'
    reuse=load(reuse_path)['initials'].get(f'{case["id"]}_r{n}')if reuse_path.exists()else None
    if reuse:
        assert all(sha(reuse[k])==reuse[k+'_sha256']for k in('shared','request','provider_result','delivery'))
        assert r.request_body(raw,INITIAL)==load(reuse['request'])
        assert load(reuse['delivery'])['evidence']==es
        provider=load(reuse['provider_result']);payload=r.valid_payload(provider,INITIAL);ref=r.provider_ref(Path(reuse['provider_result']),'exact_reused_full_diff_initial');elapsed=load(reuse['shared'])['active_wall_seconds']
        write_new(folder/'initial.reused_reference.json',reuse)
    else:
        provider=r.complete(folder,'initial_review',raw,INITIAL,remaining_wall=max(.01,900-context_wall(context)),final_reserve=reserve)
        payload=r.valid_payload(provider,INITIAL);ref=r.provider_ref(folder/'initial_review/initial.result.json','fresh_full_diff_initial');elapsed=context_wall(context)+time.monotonic()-started
    row=dict(case_id=case['id'],repeat=n,initial=payload,initial_evidence=es,initial_provider_reference={'path':ref['reference'],'sha256':ref['sha256']},context=context,first_execution=r.blank_execution('initial_only'),stages=[ref],active_wall_seconds=elapsed,initial_seconds=elapsed,exception=None)
    write_new(dest,row)
    result=r.make_result(case,n,'INITIAL',row,context,r.blank_execution('initial_only'),None,None,0);result.update(fallback_to_initial=False,method_completed=False,verification_completed=False,full_diff=True)
    write_new(folder_for(case,n,'INITIAL')/'review.result.json',result);print(json.dumps(dict(initial=case['id'],repeat=n,normal=payload is not None)),flush=True);return row

def ordered_plan(payload,arm):
    plan=plan_only(payload)
    if arm=='R2':
        intents=payload.get('retrieval_intents',[])
        plan['questions'].sort(key=lambda q:(not intent_for(q,intents)['blocking'],intent_for(q,intents)['needed_fact']not in('consumer','value_transfer','input_guard','producer'),q['id']))
    return plan

def acquisition(tool,arm,payload,execution,phase):
    kwargs=dict(phase=phase,claims=payload.get('claims',[]),completed_answers=execution.get('answers',[]))
    if arm=='R2':return goal_acquire(tool,payload['questions'],intents=payload.get('retrieval_intents',[]),**kwargs)
    return generic_acquire(tool,payload['questions'],include_changed=phase=='before_replan',**kwargs)

def branch_one(case,n,arm):
    folder=folder_for(case,n,arm);dest=folder/'review.result.json'
    if dest.exists():return load(dest)
    common=load(initial_path(case,n));started=time.monotonic();initial=common['initial'];context=copy.deepcopy(common['context']);execution=r.blank_execution('no_replan');first=r.blank_execution('no_initial');tool=None;delivery=None;provider=None;payload2=None;stages=copy.deepcopy(common['stages']);f=None;tracker=r.WarningTracker(review_only(initial)if initial else None,common['initial_evidence']);reports=[];error=None;audits={};waudit=None;aux=None;literal=None;roles=None;first_context=copy.deepcopy(context)
    try:
        if initial is not None:
            f=r.Frontier(review_only(initial),common['initial_evidence'],changed_operations=r.changed_operations(r.view_case(case),common['initial_evidence']))
            tool=ContextCollector(case,folder/'native_acquisition',{**BUDGETS,'max_wall_seconds':max(.01,900-common['active_wall_seconds'])},initial_state=context)
            tool.set_phase('before_replan');p=ordered_plan(initial,arm);first=f.execute_plan(p,tool,enable_queries=True);tracker.record_plan(p,first.get('validation'));tracker.record_answers(first)
            reports.append(acquisition(tool,arm,initial,first,'before_replan'));context=tool.export();first_context=copy.deepcopy(context);visible=attach(f,case,context);tracker.add_evidence(visible)
            write_new(folder/'first_query_execution.json',first);write_new(folder/'first_context.json',context)
            seeds=seed_catalog(r.view_case(case),visible)if arm in('R1','R2')else[]
            used,calls,_=r.usage(stages);raw=r.replan_prompt(r.view_case(case),visible,initial,first,f,{'model_tokens':200000-used if used is not None else None,'model_calls':3-calls,'source_tokens':12000-source_tokens(visible)},compact(reports[0]));raw['system']+='\n'+INTENT_POLICY
            raw['prompt']+='\nKeep all prior history compactly; request at most 4 next questions. Propose at most 2 genuinely distinct NEW candidates and at most 8 seed assessments; zero is allowed. Use the common extended REPLAN schema: retrieval_intents and discovery_assessments. '+('Return discovery_assessments=[]; use the existing verification policy.'if arm=='R0'else DISCOVERY_POLICY+'\nPublic diff seed rows [id,snapshot,file,line,column,operation,evidence_id,symbol_hint,checks]:\n'+json.dumps(compact_seeds(seeds),ensure_ascii=False,separators=(',',':')))
            write_new(folder/'planner.delivered_evidence.json',dict(evidence=visible,evidence_ids=[e['id']for e in visible],first_frontier=f.export(),public_seeds=seeds,planning_scope=r.view_case(case)['budget_view']))
            reserve=r.reserve_final(case,visible,review_only(initial),{},f.export(),first)+16000
            second=r.complete(folder,'planner',raw,REPLAN,used,calls,max(0,900-common['active_wall_seconds']-(time.monotonic()-started)),final_reserve=reserve)
            stages.append(r.provider_ref(folder/'planner/initial.result.json','policy_replan'));payload2=r.valid_payload(second,REPLAN)
            if payload2 is not None:
                audits=dict(answer=audit_replan(payload2,first,visible),claim_scope=scope_audit(payload2,visible,initial['claims']),discovery=discovery_audit(payload2,visible,initial,seeds));plan=ordered_plan(payload2,arm)
                births=audits['discovery']['birth_claims'];birth_baseline=initial['claims']+list(births.values());write_new(folder/'candidate_births.json',dict(initial_claims=initial['claims'],birth_claims=births,scope_originals=copy.deepcopy(birth_baseline),semantic_distinctness_guaranteed=False))
                # Retain the same live collector, parsed ASTs and native cache.
                tool.set_phase('before_final');released=release_budget_cached_answers(f);write_new(folder/'released_budget_cache.json',dict(keys=released,meaning='Budget failures may be reissued after the common stage limit changes; original receipts retained.'))
                execution=f.execute_plan(plan,tool,enable_queries=True);tracker.record_plan(plan,execution.get('validation'));tracker.record_answers(execution)
                reports.append(acquisition(tool,arm,payload2,execution,'before_final'));context=tool.export();es=attach(f,case,context);tracker.add_evidence(es)
                used,calls,_=r.usage(stages);remaining=dict(provider_tokens=None if used is None else 200000-used,model_calls=3-calls,source_tokens=12000-source_tokens(es))
                raw3=r.final_prompt(r.view_case(case),es,review_only(initial),plan,execution,f.export(),remaining,tracker.prompt_state(),first,payload2.get('answer_assessments',[]),payload2.get('claim_scopes',[]),{p['phase']:compact(p)for p in reports})
                raw3['prompt']=raw3['prompt'].replace('The budget diff view is partial: never claim complete PR coverage.','The complete original diff is supplied; this does not guarantee complete semantic review coverage.')
                raw3['prompt']+='\nEmit at most 4 distinct supported findings; do not force a finding or silently drop candidate history.'
                if births:raw3['prompt']+='\nController-recorded birth texts for NEW candidates (original_claim must equal the corresponding birth text; semantic truth is unverified):\n'+json.dumps({cid:c['claim']for cid,c in births.items()},ensure_ascii=False,separators=(',',':'))
                allqs=list(dict.fromkeys(q['id']for p0 in(initial,payload2)for q in p0['questions']));schema=with_claim_scopes(with_condition_roles(REVIEW,warning_ids=[w['warning_id']for w in tracker.catalog()],evidence_ids=[e['id']for e in es],question_ids=allqs))
                delivery=dict(evidence=es,evidence_ids=[e['id']for e in es],evidence_sha256=digest(es),source_tokens=source_tokens(es),context=copy.deepcopy(context),controller_before_call=f.controller.export(),frontier_before_call=f.export(),execution=execution,warning_tracker_before_call=tracker.export(),controller_reports=reports,scope_original_claims=birth_baseline)
                write_new(folder/'query_execution.json',execution);write_new(folder/'final.delivered_evidence.json',delivery);write_new(folder/'final.review_schema.json',schema)
                provider=r.complete(folder,'final',raw3,schema,used,calls,max(0,900-common['active_wall_seconds']-(time.monotonic()-started)))
                output=r.valid_payload(provider,r.FINAL)
                if output is not None:
                    literal=f.check_literal_conditions(output);waudit=tracker.audit_final(output,delivered_evidence=es);roles=role_audit(output,es,case['public_contract'],prior_claims=plan['claims']);aux=f.controller.update(output,len(f.versions)+1)
                    write_new(folder/'final.auxiliary_update.json',dict(update=aux,literal_fact_check=literal,warning_audit=waudit,condition_role_audit=roles,claim_scope_audit=scope_audit(output,es,birth_baseline),controller_after_call=f.controller.export()))
            else:audits={'no_replan':True}
    except Exception as exc:error=type(exc).__name__+': '+str(exc);write_new(folder/'runner_failure.json',dict(error=error,traceback=traceback.format_exc(),no_automatic_retry=True))
    finally:
        if tool is not None:
            try:tool.budgets['max_native_calls']=48;tool.budgets['max_file_reads']=256;tool.close()
            except Exception as exc:error=error or(type(exc).__name__+': '+str(exc))
            context=tool.export()
    if waudit is None:tracker.record_no_new_final('No normal final; preserve actual common fresh initial only.')
    write_new(folder/'warning_trace.json',tracker.export());write_new(folder/'plan_audits.json',audits);write_new(folder/'acquisition.closed_context.json',context)
    shared={**common,'stages':stages,'first_execution':first,'context':first_context,'replan':payload2}
    write_new(folder/'branch.shared.json',shared)
    row=r.make_result(case,n,arm,shared,context,execution,delivery,provider,time.monotonic()-started,aux,waudit,literal,roles,error)
    finalclaims=(row.get('payload')or{}).get('claims',[]);initialids={c['id']for c in(initial or{}).get('claims',[])}
    row.update(method_version='full_diff_discovery_question_paths_v1',full_diff=True,new_claim_ids=[c['id']for c in finalclaims if c['id']not in initialids],controller_reports=reports,discovery_audit=audits.get('discovery'),first_execution=first,first_context_reference={'path':str(folder/'first_context.json'),'sha256':sha(folder/'first_context.json')}if(folder/'first_context.json').exists()else None,branch_shared_reference={'path':str(folder/'branch.shared.json'),'sha256':sha(folder/'branch.shared.json')},query_completed=execution.get('backend_query_attempts',0)>0,source_condition='Same complete original public diff, frozen repository/dependency roots and common infrastructure; no runtime/SCIP/gold access.')
    write_new(dest,row);print(json.dumps(dict(review=row['review_id'],normal_final=row['final_new_generated'],reports=row['raw_findings_count'],new_claims=row['new_claim_ids'],exception=error,native=context['cost'].get('native_calls'))),flush=True);return row

def run():
    from freeze_small import verify
    verify();cases={c['id']:c for c in load(ROOT/'prepared/cases.json')};jobs=[(cases[u['case_id']],u['repeat'])for u in units()]
    with ProcessPoolExecutor(max_workers=WORKERS)as pool:
        for future in as_completed([pool.submit(initial_one,*j)for j in jobs]):future.result()
    branches=[(c,n,a)for c,n in jobs for a in BRANCHES];random.Random(20261104).shuffle(branches)
    with ProcessPoolExecutor(max_workers=WORKERS)as pool:
        for future in as_completed([pool.submit(branch_one,*j)for j in branches]):future.result()
    assert all((ROOT/'results/reviews'/rid/'review.result.json').exists()for rid in expected())
    write_new(ROOT/'post/reviewers_complete.json',dict(complete=True,reviews=20,units=5,reused_full_diff_initials=5,new_physical_initial_calls=0,planned_fresh_branch_calls=30))
if __name__=='__main__':run()
