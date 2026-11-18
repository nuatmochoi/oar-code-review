"""Literature-inspired recovery/reservation without hard symbol filtering."""
import copy,json,time,os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from common import ROOT,PROJECT,ARMS,WORKERS,REPEATS,BUDGETS,load,sha,digest,write_new
from openai_transport import complete,parse_payload,admission,request_body
from diff48 import diff_evidence
from loop_policy import initial_schema
from prompts_lsp48 import changed_operations
from location_ids import verify_result
import causal_loop as c
from candidate_policy import inventory,audit as decision_audit

UP=PROJECT/'runs/lsp_followup4_candidates_20261104'
FIRST=PROJECT/'runs/lsp_followups_20261104'
LABELS={'DIFF':'공개 설명+전체diff·리뷰1회','AST':'diff+AST 주변 본문6k·리뷰1회','LSP':'diff+AST·LSP 정의/참조 본문6k·리뷰1회','CURRENT_INITIAL':'원인 질문 생성·기존 초기 리뷰1회','CURRENT':'원인 질문→조회·재계획4호출+후보별 판단','PROTECTED_INITIAL':'조회 모드 분리 schema의 초기 리뷰1회','PROTECTED':'조회 모드 분리+중립 읽기+후속 예산 예약4호출'}
def cases():return load(ROOT/'prepared/cases.json')
def units():return[(case,n)for case in cases()for n in range(1,REPEATS+1)]
def expected():return[f'{case["id"]}_{a}_r{n}'for case,n in units()for a in ARMS]

def copy_reference(case,n,arm,path):
    r=load(path);r=copy.deepcopy(r);generated_here=Path(r['actual_final_provider_reference']['path']).resolve().is_relative_to(ROOT.resolve());r.update(review_id=f'{case["id"]}_{arm}_r{n}',arm=arm,final_origin='same_run_initial_reference'if generated_here else'prior_actual_response_reference',generation_in_this_run=generated_here,final_new_generated=generated_here,copied_reference=dict(path=str(path),sha256=sha(path)))
    verify_result(r);write_new(ROOT/'results/reviews'/r['review_id']/'review.result.json',r);return r

def prepare():
    assert(UP/'completion.json').exists(),'Complete and share experiment4 first';assert not(ROOT/'freeze.json').exists()
    from preservation import seal
    seal()
    assert sha(ROOT/'prepared/cases.json')==sha(FIRST/'prepared/cases.json')==sha(UP/'prepared/cases.json'),'Exactly same12 public cases'
    assert sha(ROOT/'private/truth.json')==sha(FIRST/'private/truth.json')==sha(UP/'private/truth.json'),'Fixed truth cannot change by method'
    scores=load(UP/'post/aggregate.json')['scores'];plain,ledger=scores['PLAIN'],scores['LEDGER']
    actual=lambda s:s['all_functional_cause']['tp']+s['other_confirmed_defects']
    selected='LEDGER'if ledger['cause']['f1']>plain['cause']['f1']and actual(ledger)>=actual(plain)and ledger['actual_confirmed_false_findings']<=plain['actual_confirmed_false_findings']else'PLAIN'
    refs={};source_map={};admissions=[]
    for base in(FIRST,UP):
        for name in('freeze.json','completion.json','post/aggregate.json','post/rows.json'):refs[str(base/name)]=sha(base/name)
    for case,n in units():
        for a,base,prior in [('DIFF',FIRST,'D'),('AST',FIRST,'A6'),('LSP',FIRST,'L6'),('CURRENT_INITIAL',UP,'INITIAL'),('CURRENT',UP,selected)]:
            p=base/'results/reviews'/f'{case["id"]}_{prior}_r{n}'/'review.result.json';copy_reference(case,n,a,p);refs[str(p)]=sha(p);r=load(p);provider=Path(r['actual_final_provider_reference']['path']);delivery=provider.parents[1]/'final.delivered_evidence.json'
            for q in(provider,delivery,provider.parent/'request.json'):refs[str(q)]=sha(q)
            source_map[f'{case["id"]}_{a}_r{n}']=str(p)
        context=load(ROOT/'contexts'/case['id']/'context_LSP.json');es=diff_evidence(case['diff'])+context['evidence'];schema,cat=initial_schema(es,changed_operations(case,es));raw=c.prompt(case,es,{},c.INITIAL_TASK,cat);a=admission(raw,schema,final_reserve=65000);assert a['admitted'];admissions.append(dict(case_id=case['id'],admission=a,request_sha256=digest(request_body(raw,schema))))
    write_new(ROOT/'prepared/method.json',dict(final_policy=selected,selection_rule='Experiment4 cause F1 strictly improves, confirmed true reports do not decrease, confirmed false reports do not increase; both integration methods use the same selected policy.',source_map=source_map,external_references=refs,development_adaptation_not_holdout=True,new_initial_review_required_due_to_schema_change=True))
    write_new(ROOT/'audit/initial_admissions.json',admissions)

def freeze():
    assert not(ROOT/'freeze.json').exists();assert not list((ROOT/'results').glob('**/initial.result.json'));assert load(ROOT/'audit/tests.json')['passed'];assert load(ROOT/'audit/workflow_smoke.json')['passed'];assert load(ROOT/'audit/native_smoke.json')['passed']
    from preservation import verify as preserve
    preserve();assert load(ROOT/'RESUMED_BY_USER.json')['status']=='resumed'
    assert load(ROOT/'audit/native_recovery_smoke.json')['passed']
    m=load(ROOT/'prepared/method.json')
    for p,h in m['external_references'].items():assert sha(p)==h
    files={}
    for base,dirs,names in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in('cache','logs','results','evaluation','grades','post','__pycache__','.pytest_cache')]
        for name in names:
            p=Path(base)/name
            if p.is_file():files[str(p.relative_to(ROOT))]=sha(p)
    for p in(ROOT/'post').glob('*.py'):files[str(p.relative_to(ROOT))]=sha(p)
    for p in(ROOT/'results/reviews').glob('*/review.result.json'):files[str(p.relative_to(ROOT))]=sha(p)
    write_new(ROOT/'freeze.json',dict(files=files,external_references=m['external_references'],budgets=BUDGETS,arms=ARMS,planned_reviews=len(expected()),new_reviewer_calls_planned=144,reference_responses=180,actual_new_model_calls_before_freeze=0,changed_components=['question routing schema','manifest-authorized neutral read recovery','priority 2/broad 1 preserving every question','first2-round resource reservation and cache release'],final_policy_common=m['final_policy'],recorded_at=time.time()))
def verify():
    f=load(ROOT/'freeze.json')
    for p,h in f['files'].items():assert sha(ROOT/p)==h
    for p,h in f['external_references'].items():assert sha(p)==h

def initial_stage(cn):
    case,n=cn;folder=c.shared_path(case,n).parent;dest=folder/'initial.prefilled_actual.json'
    if dest.exists():return load(dest)
    context=load(ROOT/'contexts'/case['id']/'context_LSP.json');es=diff_evidence(case['diff'])+context['evidence'];schema,cat=initial_schema(es,changed_operations(case,es));raw=c.prompt(case,es,{},c.INITIAL_TASK,cat)
    manifest=dict(evidence=es,evidence_ids=[e['id']for e in es],evidence_sha256=digest(es),location_catalog=cat)
    if(folder/'final.delivered_evidence.json').exists():assert load(folder/'final.delivered_evidence.json')==manifest
    else:write_new(folder/'final.delivered_evidence.json',manifest)
    receipt=complete(folder,'final',raw,schema,final_reserve=65000)
    value=dict(normal=parse_payload(receipt)is not None,provider_sha256=sha(folder/'final/initial.result.json'),charged_active_seconds=receipt.get('timing',{}).get('duration_seconds',0),new_actual_call=bool(receipt['physical_attempt_count']))
    write_new(dest,value);return value

def finish_unit(cn):
    case,n=cn;r=c.branch(case,n,'FOUR_QUERY');r=copy.deepcopy(r);r.update(review_id=f'{case["id"]}_PROTECTED_r{n}',arm='PROTECTED');write_new(ROOT/'results/reviews'/r['review_id']/'review.result.json',r)
    if r['final_produced']and load(ROOT/'prepared/method.json')['final_policy']=='LEDGER':
        s=load(c.shared_path(case,n));extra=load(c.extra_path(case,n));provider=Path(r['actual_final_provider_reference']['path']);delivery=load(provider.parents[1]/'final.delivered_evidence.json');items=inventory(s,extra,delivery['actual_history']);write_new(ROOT/'results/reviews'/r['review_id']/'candidate_decisions.audit.json',decision_audit(r['payload'],items,r['evidence']))
    return r

def run():
    verify();items=units();pilot=initial_stage(items[0])
    if not pilot['normal']:
        write_new(ROOT/'audit/STOP',dict(reason='Initial transport/schema pilot failed; preserved without retry or further dispatch.'))
        raise RuntimeError('Initial pilot failed; no further model dispatch')
    with ThreadPoolExecutor(max_workers=WORKERS)as p:list(p.map(initial_stage,items[1:]))
    with ThreadPoolExecutor(max_workers=4)as p:list(p.map(lambda cn:c.common_prefix(*cn),items))
    for case,n in items:
        p=ROOT/'results/reviews'/f'{case["id"]}_INITIAL_r{n}'/'review.result.json';copy_reference(case,n,'PROTECTED_INITIAL',p)
    with ThreadPoolExecutor(max_workers=WORKERS)as p:list(p.map(lambda cn:c.extra_replan(*cn),items))
    with ThreadPoolExecutor(max_workers=WORKERS)as p:list(p.map(finish_unit,items))
    assert all((ROOT/'results/reviews'/rid/'review.result.json').exists()for rid in expected())
    write_new(ROOT/'audit/reviewers_complete.json',dict(complete=True,planned_reviews=len(expected()),new_reviewer_calls=144))

if __name__=='__main__':
    import sys
    if len(sys.argv)>1 and sys.argv[1]=='prepare':prepare()
    elif len(sys.argv)>1 and sys.argv[1]=='freeze':freeze()
    else:run()
