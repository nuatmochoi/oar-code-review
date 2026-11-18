"""Shared real initial/replan prefix; 3 calls vs 4-call same-source/query contrast."""
import copy,json,time,traceback,random,threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,ProcessPoolExecutor,as_completed
from common import ROOT,PROJECT,BUDGETS,WORKERS,REPEATS,load,sha,digest,write_new,render_evidence,source_tokens
from openai_transport import complete,parse_payload,total_usage,admission
from location_ids import wire_schema,decode,render_catalog,verify_result
from diff48 import diff_evidence
from loop_policy import initial_schema,replan_schema,plan_only,execute,replay,attach,POLICY
from prompts_lsp48 import changed_operations
from acquisition_frontier import Frontier
from persistent_context import ContextCollector,release_budget_cached_answers

ARMS=('INITIAL','THREE','FOUR_SAME','FOUR_QUERY')
LABELS={'INITIAL':'AST+LSP 본문 후 초기 리뷰1회','THREE':'질문 조회·재계획 후 최종3호출','FOUR_SAME':'동일 본문으로 재계획 추가·4호출','FOUR_QUERY':'추가 재계획의 질문을 실제 조회·4호출'}
NATIVE_GATE=threading.BoundedSemaphore(4)
INITIAL_TASK='Generate an initial review with provisional claims and the linked verification PLAN. Up to4 substantive claims and4 focused questions; zero is valid. All initial report locations select actual integer IDs. Keep each live condition/challenge linked through coverage. Unsupported broad impacts must remain uncertain.'
def cases():return load(ROOT/'prepared/cases.json')
def units():return [(c,n)for c in cases()for n in range(1,REPEATS+1)]
def expected():return [f'{c["id"]}_{a}_r{n}'for c,n in units()for a in ARMS]
def shared_path(c,n):return ROOT/'results/shared'/f'{c["id"]}_r{n}'/'shared.json'
def extra_path(c,n):return ROOT/'results/shared'/f'{c["id"]}_r{n}'/'extra_replan.json'
def view(case,evidence):
    return 'Public title:\n'+case['title']+'\nPublic description:\n'+case['body']+'\nComplete original diff:\n'+case['diff']+'\nActually delivered evidence:\n'+render_evidence(evidence)
def ref(path):return dict(path=str(path),sha256=sha(path))
def stage_ref(folder,label):return ref(folder/label/'initial.result.json')
def usage(refs):
    values=[total_usage(load(v['path']))for v in refs]
    return (sum(values)if all(v is not None for v in values)else None),sum(load(v['path']).get('physical_attempt_count',0)for v in refs)
def prompt(case,es,history,task,cat=None):
    p=view(case,es)+'\nPublic changed-operation candidates (lexical, nonexhaustive; full diff remains visible):\n'+json.dumps(changed_operations(case,es),ensure_ascii=False,separators=(',',':'))+'\nActual prior history and tool/controller records:\n'+json.dumps(history,ensure_ascii=False,separators=(',',':'))+'\n'+task
    if cat is not None:p+='\nController integer location IDs (0=unresolved):\n'+render_catalog(cat)
    return dict(system=POLICY,prompt=p)
def answer_view(record):
    keys=['question','question_binding','execution','cache_hit','selected_evidence_ids','already_available_evidence_ids','all_answer_evidence_ids','mechanically_compatible_evidence_ids','unresolved','cost']
    return dict(plan_validation=record['core'].get('validation'),answers=[{k:copy.deepcopy(a[k])for k in keys if k in a}for a in record['core'].get('answers',[])],independent_source_reads=[dict(question=x['original_question'],original_target_remains_rejected=True,read_authority=x.get('read_authority'),evidence_ids=x['native_answer'].get('selected_evidence_ids',[]),unresolved=x['native_answer'].get('unresolved',[]))for x in record.get('independent_reads',[])])

def result(case,n,arm,folder,provider,evidence,cat,context,refs,initial=None,aux=None,workflow_complete=True):
    wire=parse_payload(provider);payload=decode(wire,cat)if wire is not None else None;normal=payload is not None;provider_ref=stage_ref(folder,'final')
    r=dict(review_id=f'{case["id"]}_{arm}_r{n}',case_id=case['id'],arm=arm,repeat=n,language=case['language'],status='ok'if normal else'error',report_status=provider['status'],payload=payload,raw_wire_payload=wire,raw_final_payload=payload,final_produced=normal,final_new_generated=normal,final_origin='new_final'if normal else'none',final_input_evidence_ids=[e['id']for e in evidence],evidence=evidence,raw_findings_count=len((payload or{}).get('findings',[])),actual_final_provider_reference=provider_ref,known_model_tokens=usage(refs)[0],logical_model_calls=usage(refs)[1],context_cost=context['cost'],source_tokens=source_tokens(context['evidence']),auxiliary_final_update_accepted=aux.get('accepted')if aux else None,method_completed=normal and usage(refs)[1]==(1 if arm=='INITIAL'else 3 if arm=='THREE'else 4),query_completed=arm in('THREE','FOUR_QUERY'),verification_completed=False,fallback_to_initial=False,initial_claim_ids=[c['id']for c in(initial or{}).get('claims',[])],failures=provider['failure_kinds'],model_stages=refs,no_semantic_repair=True)
    r['method_completed']=r['method_completed']and workflow_complete
    r['query_completed']=r['query_completed']and workflow_complete
    r['attempted_input_evidence_ids']=[e['id']for e in evidence]
    r['delivery_status']='normal_model_response'if normal else'not_issued'if not provider['physical_attempt_count']else'unknown_model_observation'
    if not normal:r['final_input_evidence_ids']=[]
    if normal:verify_result(r)
    write_new(folder/'review.result.json',r);return r

def common_prefix(case,n):
    dest=shared_path(case,n)
    if dest.exists():return load(dest)
    start=time.monotonic();folder=dest.parent;context=load(ROOT/'contexts'/case['id']/'context_LSP.json');initial_evidence=diff_evidence(case['diff'])+context['evidence'];schema,cat=initial_schema(initial_evidence,changed_operations(case,initial_evidence))
    raw=prompt(case,initial_evidence,{},INITIAL_TASK,cat)
    delivery=dict(evidence=initial_evidence,evidence_ids=[e['id']for e in initial_evidence],evidence_sha256=digest(initial_evidence),location_catalog=cat)
    if(folder/'final.delivered_evidence.json').exists():assert load(folder/'final.delivered_evidence.json')==delivery
    else:write_new(folder/'final.delivered_evidence.json',delivery)
    p=complete(folder,'final',raw,schema,final_reserve=65000);wire=parse_payload(p);initial=decode(wire,cat)if wire is not None else None;refs=[stage_ref(folder,'final')];first=second=None;replan=None;tool=None;f=None;error=None
    prefilled=load(folder/'initial.prefilled_actual.json')['charged_active_seconds']if(folder/'initial.prefilled_actual.json').exists()else 0
    initial_prep_seconds=sum(v.get('seconds',0)for v in context.get('stages',{}).values())
    # Initial is an actual review reference, never a diff-only baseline.
    initial_folder=ROOT/'results/reviews'/f'{case["id"]}_INITIAL_r{n}';initial_folder.mkdir(parents=True,exist_ok=True)
    # Its provider/delivery live together in shared; verify_result follows that
    # actual provider path, preserving the exact initial response.
    ri=result(case,n,'INITIAL',folder,p,initial_evidence,cat,context,refs,initial)
    write_new(initial_folder/'review.result.json',ri)
    try:
        if initial is not None:
            f=Frontier(initial,initial_evidence,changed_operations=changed_operations(case,initial_evidence));tool=ContextCollector(case,folder/'queries',BUDGETS,initial_state=context)
            first=execute(f,plan_only(initial),tool,enabled=True);es=diff_evidence(case['diff'])+tool.export()['evidence'];attach(f,es)
            used,calls=usage(refs);raw2=prompt(case,es,dict(initial=initial,first=answer_view(first),controller=f.export()),'Produce a finding-free updated PLAN and answer_assessments. Explicitly assess each attempted question with actual evidence, unresolved gaps and next question IDs. Preserve candidate/condition/challenge history; add or revise current conditions if a new implementation/consumer needs investigation. New independent causes may be added without discarding earlier candidates. Request up to4 concrete next questions; zero is allowed.')
            raw2['prompt']+='\nThe first two collection rounds temporarily cap source/native/file/native-time at10000/34/212/120s. A later collection round will release the global12000/48/256/180s limits. A temporary budget failure is not refutation; preserve necessary unanswered facts and do not withdraw a separately established narrow defect because a read was deferred.'
            p2=complete(folder,'replan',raw2,replan_schema(list(f.changed_operations.values())),used,calls,900-prefilled-initial_prep_seconds-(time.monotonic()-start),final_reserve=55000);refs.append(stage_ref(folder,'replan'));replan=parse_payload(p2)
            if replan is not None:
                release_budget_cached_answers(f);second=execute(f,plan_only(replan),tool,enabled=True)
    except Exception as exc:
        error=dict(error=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc(),model_retry=False);write_new(folder/'prefix_exception.json',error)
    finally:
        if tool is not None:
            try:tool.close()
            except Exception as exc:
                error=error or dict(error=type(exc).__name__+': '+str(exc),model_retry=False)
                write_new(folder/'close_exception.json',dict(error=str(exc),model_retry=False))
            context=tool.export()
    prep_seconds=sum(v.get('seconds',0)for v in context.get('stages',{}).values())
    shared=dict(case_id=case['id'],repeat=n,initial=initial,initial_evidence=initial_evidence,initial_catalog=cat,replan=replan,first=first,second=second,context=context,stages=refs,controller=f.export()if f else None,active_seconds=time.monotonic()-start+prep_seconds+prefilled,exception=error,complete=error is None and initial is not None and replan is not None and first is not None and second is not None)
    write_new(dest,shared);print(json.dumps(dict(prefix=case['id'],repeat=n,complete=shared['complete'],queries=sum(x['core'].get('backend_query_attempts',0)+len(x['independent_reads'])for x in[first,second]if x))),flush=True);return shared

def _extra_replan(case,n):
    started=time.monotonic();dest=extra_path(case,n)
    if dest.exists():return load(dest)
    s=load(shared_path(case,n));folder=dest.parent;refs=copy.deepcopy(s['stages']);payload=None
    if s['complete']:
        f=replay(case,s);es=diff_evidence(case['diff'])+s['context']['evidence'];used,calls=usage(refs)
        raw=prompt(case,es,dict(initial=s['initial'],prior_plan=s['replan'],first=answer_view(s['first']),second=answer_view(s['second']),controller=f.export()),'This additional planning call occurs AFTER the latest actual source acquisition. Check whether the newly read implementation closes the question or exposes another necessary producer/consumer/guard/exception fact. Produce PLAN plus answer_assessments and up to4 discriminating follow-up questions. A question discovered now can be executed in the next query condition; do not treat a failed read as refutation. Preserve all candidate history and distinguish semantic certainty from mechanical delivery.')
        raw['prompt']+='\nThe next collection round releases the reserved source/native/file/native-time limits to12000/48/256/180s. Reissue discriminating unresolved reads previously blocked by temporary resource caps when still necessary. This does not revive a refuted premise or make a missing fact true.'
        receipt=complete(folder,'extra_replan',raw,replan_schema(list(f.changed_operations.values())),used,calls,900-s['active_seconds'],final_reserve=45000);refs.append(stage_ref(folder,'extra_replan'));payload=parse_payload(receipt)
    elapsed=time.monotonic()-started
    value=dict(payload=payload,stages=refs,complete=payload is not None,active_seconds=elapsed);write_new(dest,value);return value

def _branch(case,n,arm):
    folder=ROOT/'results/reviews'/f'{case["id"]}_{arm}_r{n}';dest=folder/'review.result.json'
    if dest.exists():return load(dest)
    start=time.monotonic();s=load(shared_path(case,n));extra_seconds=0;context=copy.deepcopy(s['context']);refs=copy.deepcopy(s['stages']);f=None;new_record=None;latest=s.get('replan');error=None
    if s['complete']:
        f=replay(case,s)
        if arm!='THREE':
            extra=load(extra_path(case,n));extra_seconds=extra['active_seconds'];refs=extra['stages'];latest=extra['payload']
            if latest is not None:
                reused=None
                if reused is not None:new_record,context=reused
                tool=None;locked=arm=='FOUR_QUERY'
                if reused is None:
                    if locked:NATIVE_GATE.acquire()
                    try:
                        tool=ContextCollector(case,folder/'additional_queries',BUDGETS,initial_state=context)if arm=='FOUR_QUERY'else None
                        if tool is not None:
                            tool.set_phase('after_replan');release_budget_cached_answers(f)
                        new_record=execute(f,plan_only(latest),tool,enabled=arm=='FOUR_QUERY')
                    finally:
                        try:
                            if tool is not None:tool.close();context=tool.export()
                        finally:
                            if locked:NATIVE_GATE.release()
    es=diff_evidence(case['diff'])+context['evidence'];schema,cat=wire_schema(es)
    history=dict(initial=s['initial'],prior_plan=s.get('replan'),first=answer_view(s['first'])if s['first']else None,second=answer_view(s['second'])if s['second']else None,latest_plan=latest,additional_round=answer_view(new_record)if new_record else None,controller=f.export()if f else None)
    raw=prompt(case,es,history,'Give the terminal review. No further tool round remains. Report only concrete supported narrow defects, retaining explicitly bounded triggers and marking uncertain broader impact separately. Reflect actual support/refutation/unresolved evidence. Preserve reasons for candidate withdrawal or withholding; never call deletion/no-warning a successful correction. Do not replace one supported distinct defect just because another is ranked higher. Return up to6 independent findings; zero is valid.',cat)
    candidates=None
    if(ROOT/'prepared/method.json').exists()and load(ROOT/'prepared/method.json')['final_policy']=='LEDGER':
        from candidate_policy import inventory,augmented,POLICY as REPORT_POLICY
        candidates=inventory(s,load(extra_path(case,n)),history);raw['system']+='\n'+REPORT_POLICY;raw['prompt']+='\nComplete historical candidate inventory (not gold; no filtering):\n'+json.dumps(candidates,ensure_ascii=False,separators=(',',':'));schema=augmented(schema,candidates)
    write_new(folder/'final.delivered_evidence.json',dict(evidence=es,evidence_ids=[e['id']for e in es],evidence_sha256=digest(es),location_catalog=cat,context=context,actual_history=history))
    write_new(folder/'final.review_schema.json',schema);used,calls=usage(refs)
    p=complete(folder,'final',raw,schema,used,calls,900-s['active_seconds']-extra_seconds-(time.monotonic()-start));refs.append(stage_ref(folder,'final'));wire=parse_payload(p);aux=None
    if wire is not None and f is not None:
        canonical=decode(wire,cat);projection=__import__('scope_contract').ledger_projection(canonical);write_new(folder/'controller_projection.json',dict(payload=projection,original_model_payload_sha256=digest(canonical),only_scope_metadata_removed=True,semantic_fields_unchanged=True));aux=f.controller.update(projection,len(f.versions)+1);write_new(folder/'controller_final_update.json',dict(update=aux,controller=f.controller.export(),frontier=f.export(),mechanical_not_semantic_truth=True))
    workflow_complete=s['complete']and(arm=='THREE'or load(extra_path(case,n))['complete'])
    r=result(case,n,arm,folder,p,es,cat,context,refs,s['initial'],aux,workflow_complete);write_new(folder/'query_round_trace.json',dict(additional_round=new_record,source_prefix_preserved={e['id']for e in load(ROOT/'contexts'/case['id']/'context_LSP.json')['evidence']}<={e['id']for e in context['evidence']},original_claim_history=s['initial']['claims']if s['initial']else[],final_claims=(r['payload']or{}).get('claims',[])))
    print(json.dumps(dict(review=r['review_id'],normal=r['final_produced'],logical_calls=r['logical_model_calls'],native=context['cost']['native_calls'],reports=r['raw_findings_count'])),flush=True);return r

def extra_replan(case,n):
    try:return _extra_replan(case,n)
    except Exception as exc:
        dest=extra_path(case,n)
        if dest.exists():raise
        s=load(shared_path(case,n));value=dict(payload=None,stages=s['stages'],complete=False,active_seconds=0,exception=dict(error=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc(),model_retry=False))
        if (dest.parent/'extra_replan/initial.result.json').exists():value['stages']=value['stages']+[stage_ref(dest.parent,'extra_replan')]
        write_new(dest,value);return value

def branch(case,n,arm):
    try:return _branch(case,n,arm)
    except Exception as exc:
        folder=ROOT/'results/reviews'/f'{case["id"]}_{arm}_r{n}';dest=folder/'review.result.json'
        if dest.exists():raise
        s=load(shared_path(case,n));refs=copy.deepcopy(s['stages'])
        if arm!='THREE' and extra_path(case,n).exists():refs=load(extra_path(case,n))['stages']
        if (folder/'final/initial.result.json').exists():refs.append(stage_ref(folder,'final'))
        error=dict(error=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc(),model_retry=False)
        write_new(folder/'branch_exception.json',error)
        # A produced final may not be relabeled as zero findings merely because
        # downstream decoding/binding/controller bookkeeping failed.
        if (folder/'final/initial.result.json').exists() and parse_payload(load(folder/'final/initial.result.json')) is not None:raise
        r=dict(review_id=f'{case["id"]}_{arm}_r{n}',case_id=case['id'],arm=arm,repeat=n,language=case['language'],status='error',report_status='error',payload=None,raw_final_payload=None,final_produced=False,final_new_generated=False,final_origin='none',evidence=s['initial_evidence'],final_input_evidence_ids=[],raw_findings_count=0,known_model_tokens=usage(refs)[0],logical_model_calls=usage(refs)[1],context_cost=s['context']['cost'],source_tokens=source_tokens(s['context']['evidence']),method_completed=False,query_completed=False,verification_completed=False,fallback_to_initial=False,failures=[error],model_stages=refs,no_semantic_repair=True)
        write_new(dest,r);return r

def run():
    from stage_freeze import verify
    verify();assert(PROJECT/'runs/lsp_followup2_seeds_20261104/completion.json').exists(),'Complete and share stage2 before stage3 dispatch'
    assert(PROJECT/'runs/lsp_followup3_replanning_20261104/completion.json').exists(),'Finish and share original200k experiment before supplement dispatch'
    with ThreadPoolExecutor(max_workers=4)as p:list(p.map(lambda cn:common_prefix(*cn),units()))
    with ThreadPoolExecutor(max_workers=WORKERS)as p:list(p.map(lambda cn:extra_replan(*cn),units()))
    jobs=[(c,n,a)for c,n in units()for a in ['THREE','FOUR_SAME','FOUR_QUERY']];random.Random(20261104).shuffle(jobs)
    with ThreadPoolExecutor(max_workers=WORKERS)as p:list(p.map(lambda args:branch(*args),jobs))
    assert all((ROOT/'results/reviews'/rid/'review.result.json').exists()for rid in expected())
    write_new(ROOT/'audit/reviewers_complete.json',dict(complete=True,reviews=len(expected())))

if __name__=='__main__':run()
