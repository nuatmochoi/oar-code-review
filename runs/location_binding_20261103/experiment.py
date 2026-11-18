"""Frozen source-only location-format experiment:24directions×8arms×3repeats."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
import copy
import json
from pathlib import Path
import random
import time
from common import ROOT,ARMS,CONTROLLED,MODEL,REPEATS,load,sha,digest,write_new,source_rows,source_tokens,prompt_for,response_schema
from location_controller import make_registry,bind_payload
from operation_catalog import python_file,go_files,build_catalog
import cli_transport
import reliable_transport as transport

PARENT=ROOT.parent/'targeted_context_controller_20261102'
REVIEWS=576
POLICY_FILES=('experiment.py','common.py','location_visibility.py','operation_catalog.py','location_controller.py','line_controller.py',
    'location_instructions.py','diff_coordinates.py','go_operations/main.go','go_operations/extractor')

def ref(path):return dict(path=str(path),sha256=sha(path))

def policy():
    assert load(ROOT/'verification/tests.json')['passed']
    assert load(ROOT/'audit/preservation_before.json')['passed']
    assert not list((ROOT/'results/reviews').glob('*/initial.claim.json'))
    files={p:sha(ROOT/p) for p in POLICY_FILES}
    write_new(ROOT/'policy_freeze.json',dict(created_at=time.time(),files=files,
        eligibility='full literal AST token span already supplied in HEAD, inside protected AST/diff row coordinates; max12lines',
        anchor='single actual HEAD start-token line, unchanged context operations allowed',labels_or_previous_responses_consumed=False))
    return dict(files=len(files),passed=True)

def verify_policy():
    for p,h in load(ROOT/'policy_freeze.json')['files'].items():assert sha(ROOT/p)==h,('policy changed',p)

def prepare():
    verify_policy();assert not (ROOT/'freeze.json').exists()
    parent_seal=load(PARENT/'completed_result_seal.json')
    assert parent_seal['status']=='complete'
    olds=load(PARENT/'prepared/cases.json');assert len(olds)==24
    fields=('case_id','direction','language','stratum','public_contract','diff','workspace','changed_files')
    cases=[]
    for old in olds:
        c={k:copy.deepcopy(old[k]) for k in fields}
        c['protected_ast']=copy.deepcopy(old['arms']['ast'])
        c['arms']={a:copy.deepcopy(old['arms'][a.rsplit('_',1)[0]]) for a in ARMS}
        c['source_references']=dict(parent_public_cases=ref(PARENT/'prepared/cases.json'),parent_seal=ref(PARENT/'completed_result_seal.json'))
        c['source_costs']={s:copy.deepcopy(old['costs'][s]) for s in ('diff','targeted')}
        cases.append(c)
    write_new(ROOT/'prepared/public_seed_cases.json',cases)
    py_paths=sorted({str((Path(c['workspace'])/p).resolve()) for c in cases if c['language']=='Python' for p in c['changed_files']})
    go_paths=sorted({str((Path(c['workspace'])/p).resolve()) for c in cases if c['language']=='Go' for p in c['changed_files']})
    began=time.monotonic()
    parsed=[python_file(p) for p in py_paths]+go_files(go_paths)
    records={r['path']:r for r in parsed};assert len(records)==len(py_paths)+len(go_paths)
    assert not any(r['errors'] for r in parsed)
    write_new(ROOT/'prepared/parsed_files.json',dict(files=parsed,python_files=len(py_paths),go_files=len(go_paths),
        preparation_seconds=time.monotonic()-began,full_HEAD_files_parsed=len(parsed),native_LSP_calls=0,source_retrieval_reused=True,
        note='Full-file AST backend traces are never rendered. Only verified actually supplied token slices enter model catalogs.'))
    import legacy_common_reference as legacy
    for c,old in zip(cases,olds):
        c['operation_catalogs']={s:build_catalog(c,s+'_operation',records) for s in ('diff','targeted')}
        for source in ('diff','targeted'):
            assert c['operation_catalogs'][source]['operations'],('no visible operations',c['case_id'],c['direction'],source)
            for form in ('free','numbered','line','operation'):
                arm=source+'_'+form
                assert source_rows(c['arms'][arm])==source_rows(old['arms'][source])
                assert source_tokens(c['arms'][arm])<=5200
            assert prompt_for(c,source+'_free')==legacy.prompt_for(old,source),'baseline raw must exactly replay prior source format'
            assert response_schema(c,source+'_free')==legacy.response_schema(old,source)
    write_new(ROOT/'prepared/cases.json',cases)
    # Collector has finished all source-only catalogs before private truth is read.
    write_new(ROOT/'private/cases.json',load(PARENT/'private/cases.json'))
    jobs=[]
    for c in cases:
        for arm in ARMS:
            source,form=arm.rsplit('_',1);schema=response_schema(c,arm);raw=prompt_for(c,arm)
            if arm in CONTROLLED:
                name=f'{c["case_id"]}_{c["direction"]}_{arm}'
                rp=ROOT/'prepared/registries'/f'{name}.json';write_new(rp,make_registry(c,arm))
                sp=ROOT/'prepared/schemas'/f'{name}.json';write_new(sp,schema)
            else:
                rp=None;sp=ROOT/'prepared/schemas/legacy.json'
                if sp.exists():assert load(sp)==schema
                else:write_new(sp,schema)
            for repeat in range(1,REPEATS+1):
                rid=f'{c["case_id"]}_{c["direction"]}_{arm}_r{repeat}'
                jobs.append(dict(review_id=rid,case_id=c['case_id'],direction=c['direction'],language=c['language'],stratum=c['stratum'],
                    arm=arm,source_context=source,format=form,repeat=repeat,raw=raw,raw_hash=digest(raw),
                    source_tokens=source_tokens(c['arms'][arm]),public_case_hash=digest(c),
                    schema_reference=ref(sp),registry_reference=ref(rp) if rp else None))
    random.Random(2026110301).shuffle(jobs)
    i=next(i for i,j in enumerate(jobs) if j['format']=='operation');jobs[0],jobs[i]=jobs[i],jobs[0]
    assert len(jobs)==REVIEWS
    write_new(ROOT/'prepared/jobs.json',jobs)
    write_new(ROOT/'prepared/summary.json',dict(cases=12,directions=24,reviews=REVIEWS,arms=ARMS,source_contexts=2,formats=4,
        repeats=3,job_seed=2026110301,bootstrap_seed=2026110302,sentinel=jobs[0]['review_id'],source_retrieval_native_calls=0,
        historical_model_responses_reused=0,policy_freeze=ref(ROOT/'policy_freeze.json')))
    verify_policy()
    return dict(cases=12,directions=24,reviews=REVIEWS,registries=96,parsed_HEAD_files=len(parsed))

def freeze():
    verify_policy();assert not (ROOT/'freeze.json').exists()
    assert len(load(ROOT/'prepared/jobs.json'))==REVIEWS
    assert load(ROOT/'audit/source_audit.json')['passed']
    assert load(ROOT/'verification/tests.json')['passed']
    assert load(ROOT/'audit/preservation_before.json')['passed']
    files={}
    for p in ROOT.rglob('*'):
        rel=p.relative_to(ROOT)
        if not p.is_file() or any(x in ('__pycache__','.pycache','.pytest_cache','.venv','results','workspaces') or x.endswith('_workspaces') for x in rel.parts):continue
        if p.suffix in ('.pyc','.pyo','.log'):continue
        files[str(rel)]=sha(p)
    write_new(ROOT/'freeze.json',dict(created_at=time.time(),files=files,reviews=REVIEWS,model=MODEL,reasoning_effort='low',workers=4,min_interval=3,
        source_cap=5200,parent_seal=ref(PARENT/'completed_result_seal.json'),policy_freeze=ref(ROOT/'policy_freeze.json'),
        primary='operation-line within source context; semantic-conditioned selected-location error rate plus causeF1/recall/FP'))
    return dict(files=len(files),reviews=REVIEWS)

def verify_freeze():
    f=load(ROOT/'freeze.json')
    for p,h in f['files'].items():assert sha(ROOT/p)==h,('frozen change',p)
    assert sha(PARENT/'completed_result_seal.json')==f['parent_seal']['sha256']
    return dict(passed=True,files=len(f['files']),reviews=f['reviews'])

def run_one(job):
    rid=job['review_id'];folder=ROOT/'results/reviews'/rid;dest=folder/'review.result.json'
    if dest.exists():
        r=load(dest);assert r['job_hash']==digest(job);return dict(review_id=rid,status=r['status'],preserved=True)
    gate=transport.AdmissionGate(ROOT/'results/gate',max_live_calls=4,min_interval=3)
    sp=Path(job['schema_reference']['path']);assert sha(sp)==job['schema_reference']['sha256'];schema=load(sp)
    if (folder/'initial.claim.json').exists():
        if not (folder/'initial.result.json').exists():raise transport.ClaimedIncomplete(rid)
    else:
        client=transport.CliCompletionClient(out_dir=ROOT/'results/reviews',review_id=rid,schema=schema,model=MODEL,gate=gate,max_calls=1,timeout=300)
        client.begin_stage('initial')
        try:client.complete(**job['raw'])
        except transport.CallFailed:pass
    r=load(folder/'initial.result.json');actual=load(folder/'initial.input.json');assert actual['raw']==job['raw']
    try:raw_payload=json.loads(r.get('raw_response') or '{}')
    except ValueError:raw_payload={}
    payload=raw_payload if isinstance(raw_payload,dict) else {}
    if job['arm'] in CONTROLLED:
        rp=Path(job['registry_reference']['path']);assert sha(rp)==job['registry_reference']['sha256']
        canonical,binding=bind_payload(payload,load(rp))
    else:
        canonical=copy.deepcopy(payload);findings=payload.get('findings',[]);findings=findings if isinstance(findings,list) else []
        binding=dict(raw_findings=len(findings),canonical_findings=len(findings),binding_errors=[],semantic_assessment_performed=False,
                     filtered_findings=0,repaired_selections=0,mode='legacy_free_citation')
    findings=canonical.get('findings',[])
    if not isinstance(findings,list):findings=[];canonical['findings']=[]
    assert len(findings)==binding['raw_findings']
    write_new(folder/'binding_audit.json',binding)
    value={k:v for k,v in job.items() if k!='raw'}
    value.update(job_hash=digest(job),freeze_sha256=sha(ROOT/'freeze.json'),
        status='ok' if r['status']=='ok' and not binding['binding_errors'] else 'error',transport_status=r['status'],
        payload=payload,canonical_payload=canonical,parsed_payload_type=type(raw_payload).__name__,raw_reported_count=binding['raw_findings'],
        stage_result_sha256=sha(folder/'initial.result.json'),actual_input_sha256=sha(folder/'initial.input.json'),binding_reference=ref(folder/'binding_audit.json'),
        physical_attempt_count=r['physical_attempt_count'],retry_count=r['retry_count'],actual_usage=r['actual_usage'],
        duration_seconds=r['timing']['all_attempts_duration_seconds'],attempts_without_reported_usage=r['attempts_without_reported_usage'])
    write_new(dest,value)
    return dict(review_id=rid,status=value['status'],findings=len(findings))

def run():
    verify_freeze();jobs=load(ROOT/'prepared/jobs.json')
    first=run_one(jobs[0]);print(json.dumps(first),flush=True)
    if first['status']!='ok' or (ROOT/'results/gate/STOP').exists():return dict(stopped='sentinel_failure',result=first)
    done=1
    with ProcessPoolExecutor(max_workers=4) as pool:
        it=iter(jobs[1:]);live={}
        for _ in range(4):
            j=next(it,None)
            if j:live[pool.submit(run_one,j)]=j['review_id']
        while live:
            ready,_=wait(live,return_when=FIRST_COMPLETED)
            for f in ready:
                rid=live.pop(f)
                try:r=f.result()
                except Exception as exc:r=dict(review_id=rid,exception=f'{type(exc).__name__}: {exc}')
                print(json.dumps(r),flush=True);done+=1
                if not (ROOT/'results/gate/STOP').exists() and 'exception' not in r:
                    j=next(it,None)
                    if j:live[pool.submit(run_one,j)]=j['review_id']
    return dict(processed=done,results=len(list((ROOT/'results/reviews').glob('*/review.result.json'))),stopped=(ROOT/'results/gate/STOP').exists())

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=('policy','prepare','freeze','verify','run'));a=p.parse_args()
    print(json.dumps({'policy':policy,'prepare':prepare,'freeze':freeze,'verify':verify_freeze,'run':run}[a.command](),ensure_ascii=False),flush=True)
