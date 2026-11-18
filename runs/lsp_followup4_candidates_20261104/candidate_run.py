import copy,json,time,random,os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from common import ROOT,PROJECT,ARMS,REPEATS,WORKERS,MIN_START_INTERVAL,BUDGETS,load,sha,digest,write_new
from openai_transport import complete,parse_payload,total_usage,request_body,admission
from location_ids import decode,verify_result
from candidate_policy import inventory,augmented,audit,POLICY,TASK
from upstream_prompt import prompt

UP=PROJECT/'runs/lsp_followup3_budget400k_20261104'
LABELS={'INITIAL':'같은 실제 초기 리뷰','PLAIN':'기존 최종 판단','LEDGER':'전체 후보별 판단·확인한 좁은 결함 보존'}
def cases():return load(ROOT/'prepared/cases.json')
def units():return[(c,n)for c in cases()for n in range(1,REPEATS+1)]
def expected():return[f'{c["id"]}_{a}_r{n}'for c,n in units()for a in ARMS]
def paths(c,n):
    shared=UP/'results/shared'/f'{c["id"]}_r{n}';final=UP/'results/reviews'/f'{c["id"]}_FOUR_QUERY_r{n}'
    return shared,final
def inputs(c,n,a):
    shared,final=paths(c,n);delivery=load(final/'final.delivered_evidence.json');s=load(shared/'shared.json');extra=load(shared/'extra_replan.json');es=delivery['evidence'];cat=delivery['location_catalog'];sc=load(final/'final.review_schema.json');raw=prompt(c,es,delivery['actual_history'],TASK,cat);items=inventory(s,extra,delivery['actual_history'])
    if(final/'final/request.json').exists():assert request_body(raw,sc)==load(final/'final/request.json'),'Exact upstream final input not reproduced'
    if a=='LEDGER':
        raw['system']+='\n'+POLICY;raw['prompt']+='\nComplete historical candidate inventory (not gold; no filtering):\n'+json.dumps(items,ensure_ascii=False,separators=(',',':'));sc=augmented(sc,items)
    admission_record=load(final/'final.admission.json');refs=load(final/'review.result.json')['model_stages'];assert Path(refs[-1]['path']).parent.name=='final';refs=refs[:-1]
    return dict(raw=raw,schema=sc,delivery=delivery,candidates=items,upstream_stages=refs,used_tokens=admission_record['used_tokens'],used_calls=admission_record['used_calls'],remaining_wall=admission_record['CLI_timeout_seconds']+WORKERS*MIN_START_INTERVAL,upstream_result=load(final/'review.result.json'))

def prepare():
    assert(UP/'completion.json').exists(),'Stage3 must complete and be shared first';assert not(ROOT/'freeze.json').exists()
    refs={};checks=[]
    for p in[UP/'freeze.json',UP/'completion.json',UP/'post/aggregate.json',UP/'post/rows.json']:refs[str(p)]=sha(p)
    for c,n in units():
        shared,final=paths(c,n)
        for p in list(shared.glob('*.json'))+list(final.glob('*.json'))+list((final/'final').glob('*.json')):refs[str(p)]=sha(p)
        for a in('PLAIN','LEDGER'):
            v=inputs(c,n,a);check=admission(v['raw'],v['schema'],v['used_tokens'],v['used_calls'],v['remaining_wall']);checks.append(dict(case_id=c['id'],repeat=n,arm=a,admission=check,request_sha256=digest(request_body(v['raw'],v['schema'])),candidate_ids=list(v['candidates'])))
    write_new(ROOT/'prepared/admissions.json',checks)
    assert load(ROOT/'audit/tests.json')['passed']
    assert load(ROOT/'audit/workflow_smoke.json')['passed']
    files={}
    for base,dirs,names in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in('logs','results','evaluation','grades','post','__pycache__','.pytest_cache')]
        for name in names:
            p=Path(base)/name
            if p.is_file():files[str(p.relative_to(ROOT))]=sha(p)
    for p in(ROOT/'post').glob('*.py'):files[str(p.relative_to(ROOT))]=sha(p)
    write_new(ROOT/'freeze.json',dict(files=files,external_references=refs,stage=4,new_reviewer_calls=72,logical_reviews=108,initial_reviewer_response_reused=36,all_upstream_sources_fixed=True,no_new_queries=True,budgets=BUDGETS,recorded_at=time.time()))
def verify():
    f=load(ROOT/'freeze.json')
    for p,h in f['files'].items():assert sha(ROOT/p)==h
    for p,h in f['external_references'].items():assert sha(p)==h
def one(job):
    c,n,a=job;rid=f'{c["id"]}_{a}_r{n}';folder=ROOT/'results/reviews'/rid;dest=folder/'review.result.json'
    if dest.exists():return
    if a=='INITIAL':
        old=UP/'results/reviews'/f'{c["id"]}_INITIAL_r{n}'/'review.result.json';r=load(old);r.update(review_id=rid,arm=a,final_origin='same_actual_initial_reference');write_new(dest,r);return
    v=inputs(c,n,a);delivery=v['delivery'];write_new(folder/'final.delivered_evidence.json',delivery);write_new(folder/'final.review_schema.json',v['schema'])
    receipt=complete(folder,'final',v['raw'],v['schema'],v['used_tokens'],v['used_calls'],v['remaining_wall']);wire=parse_payload(receipt);payload=decode(wire,delivery['location_catalog'])if wire is not None else None;normal=payload is not None
    ref=dict(path=str(folder/'final/initial.result.json'),sha256=sha(folder/'final/initial.result.json'));r=copy.deepcopy(v['upstream_result']);r.update(review_id=rid,arm=a,status='ok'if normal else'error',report_status=receipt['status'],payload=payload,raw_wire_payload=wire,raw_final_payload=payload,final_produced=normal,final_new_generated=normal,final_origin='new_final'if normal else'none',raw_findings_count=len((payload or{}).get('findings',[])),actual_final_provider_reference=ref,known_model_tokens=(v['used_tokens']+total_usage(receipt))if v['used_tokens']is not None and total_usage(receipt)is not None else None,logical_model_calls=v['used_calls']+receipt['physical_attempt_count'],model_stages=v['upstream_stages']+[ref],method_completed=normal and v['used_calls']==3,failures=receipt['failure_kinds'],candidate_inventory=list(v['candidates']))
    if normal:verify_result(r)
    if a=='LEDGER'and normal:write_new(folder/'candidate_decisions.audit.json',audit(payload,v['candidates'],delivery['evidence']))
    write_new(dest,r);print(json.dumps(dict(review=rid,normal=normal,reports=r['raw_findings_count'])),flush=True)
def run():
    verify();jobs=[(c,n,a)for c,n in units()for a in ARMS];random.Random(20261104).shuffle(jobs)
    with ThreadPoolExecutor(max_workers=WORKERS)as p:list(p.map(one,jobs))
    assert all((ROOT/'results/reviews'/rid/'review.result.json').exists()for rid in expected())
    write_new(ROOT/'audit/reviewers_complete.json',dict(complete=True,reviews=len(expected()),new_reviewer_planned=72))
if __name__=='__main__':
    import sys
    if len(sys.argv)>1 and sys.argv[1]=='prepare':prepare()
    else:run()
