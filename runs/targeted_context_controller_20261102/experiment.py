"""Six-arm paired experiment; fresh judgments and immutable source/registry inputs."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import copy
import json
import random
import time
from pathlib import Path
from common import ROOT,ARMS,CONTROLLED,REPEATS,MODEL,load,sha,digest,write_new,source_rows,source_tokens,prompt_for,response_schema
from citation_controller import make_registry,bind_payload
import cli_transport
import reliable_transport as transport

PARENT=ROOT.parent/'cross_language_context_20261102'
REVIEWS=432

def ref(path):return dict(path=str(path),sha256=sha(path))

def prepare():
    assert not (ROOT/'freeze.json').exists()
    cases=[];truth=[]
    for lang in ('python','go'):
        olds=load(PARENT/f'{lang}_context/public_cases.json')
        target_path=ROOT/f'{lang}_targeted/public_cases.json'
        targets={(c['case_id'],c['direction']):c for c in load(target_path)}
        assert len(olds)==len(targets)==12
        for old in olds:
            target=targets[old['case_id'],old['direction']]
            for k in ('case_id','direction','language','public_contract','diff','workspace','changed_files'):
                assert target[k]==old[k],('changed case',k,old['case_id'])
            blocks=target['arms']['targeted']
            assert all(source_rows(blocks).get(k)==v for k,v in source_rows(old['arms']['lsp']).items()),('protected LSP source lost',old['case_id'])
            c={k:copy.deepcopy(v) for k,v in old.items() if k not in ('arms','costs')}
            c['arms']={
                'diff':[],'ast':old['arms']['ast'],'current':old['arms']['search'],
                'current_controller':old['arms']['search'],'targeted':blocks,'targeted_controller':blocks}
            c['costs']={
                'diff':old['costs']['diff'],'ast':old['costs']['ast'],
                'current':old['costs']['search'],'current_controller':old['costs']['search'],
                'targeted':target['costs']['targeted'],'targeted_controller':target['costs']['targeted']}
            c['source_references']=dict(parent=ref(PARENT/f'{lang}_context/public_cases.json'),targeted=ref(target_path))
            c['source_cost_provenance']={a:('historical_measurement_replayed_source' if a in ('diff','ast','current','current_controller') else 'fresh_targeted_collection_with_historical_lsp_seed') for a in ARMS}
            for arm in ARMS:assert source_tokens(c['arms'][arm])<=5200,(c['case_id'],arm)
            assert source_rows(c['arms']['current'])==source_rows(c['arms']['current_controller'])
            assert source_rows(c['arms']['targeted'])==source_rows(c['arms']['targeted_controller'])
            # Actual old free-citation input is exactly preserved; only answers are fresh.
            assert prompt_for(c,'current')==__import__('citation_legacy_reference').old_prompt(old,'search')
            cases.append(c)
        truth+=load(PARENT/f'{lang}_context/private_cases.json')
    assert len(cases)==24 and len({(c['case_id'],c['direction']) for c in cases})==24
    jobs=[]
    for c in cases:
        for arm in ARMS:
            schema=response_schema(c,arm);raw=prompt_for(c,arm)
            if arm in CONTROLLED:
                key=f'{c["case_id"]}_{c["direction"]}_{arm}'
                registry=make_registry(c,arm);registry_path=ROOT/'prepared/registries'/f'{key}.json'
                write_new(registry_path,registry)
                schema_path=ROOT/'prepared/schemas'/f'{key}.json';write_new(schema_path,schema)
            else:
                registry_path=None;schema_path=ROOT/'prepared/schemas/legacy.json'
                if schema_path.exists():assert load(schema_path)==schema
                else:write_new(schema_path,schema)
            for repeat in range(1,REPEATS+1):
                rid=f'{c["case_id"]}_{c["direction"]}_{arm}_r{repeat}'
                jobs.append(dict(review_id=rid,case_id=c['case_id'],direction=c['direction'],language=c['language'],stratum=c['stratum'],arm=arm,repeat=repeat,
                    raw=raw,source_tokens=source_tokens(c['arms'][arm]),public_case_hash=digest(c),raw_hash=digest(raw),
                    schema_reference=ref(schema_path),registry_reference=ref(registry_path) if registry_path else None))
    random.Random(2026110211).shuffle(jobs)
    index=next(i for i,j in enumerate(jobs) if j['arm'] in CONTROLLED)
    jobs[0],jobs[index]=jobs[index],jobs[0]
    assert len(jobs)==REVIEWS
    write_new(ROOT/'prepared/cases.json',cases)
    write_new(ROOT/'private/cases.json',truth)
    write_new(ROOT/'prepared/jobs.json',jobs)
    write_new(ROOT/'prepared/summary.json',dict(cases=12,directions=24,reviews=REVIEWS,logical_calls=REVIEWS,arms=ARMS,seed=2026110211,
        sentinel='first controlled-citation job in seeded shuffle; no separate model call',historical_model_outputs_reused=0))
    return dict(reviews=REVIEWS,cases=12,controlled_sentinel=jobs[0]['review_id'])

def freeze():
    assert not (ROOT/'freeze.json').exists()
    jobs=load(ROOT/'prepared/jobs.json');assert len(jobs)==REVIEWS
    assert load(ROOT/'verification/tests.json')['passed']
    assert load(ROOT/'audit/preservation_before.json')['passed']
    assert load(ROOT/'audit/source_audit.json')['passed']
    parent=load(PARENT/'completed_result_seal.json')
    for path,h in parent['files'].items():assert sha(PARENT/path)==h,('parent changed',path)
    files={}
    for p in ROOT.rglob('*'):
        rel=p.relative_to(ROOT)
        if not p.is_file() or any(x in ('__pycache__','.pycache','.pytest_cache','.venv','results','workspaces') or x.endswith('_workspaces') for x in rel.parts):continue
        if p.suffix in ('.pyc','.pyo','.log'):continue
        files[str(rel)]=sha(p)
    write_new(ROOT/'freeze.json',dict(files=files,created_at=time.time(),model=MODEL,reasoning_effort='low',workers=4,min_interval=3,
        reviews=REVIEWS,logical_calls=REVIEWS,source_cap=5200,parent_seal=ref(PARENT/'completed_result_seal.json'),
        primary='targeted_controller-current',semantic_cause_secondary=True))
    return dict(files=len(files),reviews=REVIEWS)

def verify_freeze():
    f=load(ROOT/'freeze.json')
    for path,h in f['files'].items():assert sha(ROOT/path)==h,('frozen change',path)
    assert sha(PARENT/'completed_result_seal.json')==f['parent_seal']['sha256']
    return dict(passed=True,files=len(f['files']),reviews=f['reviews'])

def run_one(job):
    rid=job['review_id'];folder=ROOT/'results/reviews'/rid;dest=folder/'review.result.json'
    if dest.exists():
        r=load(dest);assert r['job_hash']==digest(job);return dict(review_id=rid,status=r['status'],preserved=True)
    gate=transport.AdmissionGate(ROOT/'results/gate',max_live_calls=4,min_interval=3)
    sp=Path(job['schema_reference']['path']);assert sha(sp)==job['schema_reference']['sha256'];schema=load(sp)
    if (folder/'initial.claim.json').exists():
        if not(folder/'initial.result.json').exists():raise transport.ClaimedIncomplete(rid)
    else:
        client=transport.CliCompletionClient(out_dir=ROOT/'results/reviews',review_id=rid,schema=schema,model=MODEL,gate=gate,max_calls=1,timeout=300)
        client.begin_stage('initial')
        try:client.complete(**job['raw'])
        except transport.CallFailed:pass
    r=load(folder/'initial.result.json');actual=load(folder/'initial.input.json');assert actual['raw']==job['raw']
    try:raw_payload=json.loads(r.get('raw_response') or '{}')
    except ValueError:raw_payload={}
    raw_type=type(raw_payload).__name__
    model_payload=raw_payload if isinstance(raw_payload,dict) else {}
    if job['arm'] in CONTROLLED:
        rp=Path(job['registry_reference']['path']);assert sha(rp)==job['registry_reference']['sha256']
        canonical,binding=bind_payload(model_payload,load(rp))
    else:
        canonical=copy.deepcopy(model_payload)
        f=model_payload.get('findings',[]);f=f if isinstance(f,list) else []
        binding=dict(raw_findings=len(f),canonical_findings=len(f),binding_errors=[],semantic_assessment_performed=False,
                     filtered_findings=0,repaired_selections=0,mode='legacy_free_citation')
    final_findings=canonical.get('findings',[])
    if not isinstance(final_findings,list):final_findings=[];canonical['findings']=[]
    assert len(final_findings)==binding['raw_findings'],'controller must preserve raw finding count'
    write_new(folder/'binding_audit.json',binding)
    value={k:v for k,v in job.items() if k!='raw'}
    value.update(job_hash=digest(job),freeze_sha256=sha(ROOT/'freeze.json'),
        status='ok' if r['status']=='ok' and not binding['binding_errors'] else 'error',transport_status=r['status'],
        payload=model_payload,canonical_payload=canonical,parsed_payload_type=raw_type,raw_reported_count=binding['raw_findings'],
        stage_result_sha256=sha(folder/'initial.result.json'),actual_input_sha256=sha(folder/'initial.input.json'),
        binding_reference=ref(folder/'binding_audit.json'),physical_attempt_count=r['physical_attempt_count'],retry_count=r['retry_count'],
        actual_usage=r['actual_usage'],duration_seconds=r['timing']['all_attempts_duration_seconds'],
        attempts_without_reported_usage=r['attempts_without_reported_usage'])
    write_new(dest,value)
    return dict(review_id=rid,status=value['status'],findings=len(final_findings))

def run():
    verify_freeze();jobs=load(ROOT/'prepared/jobs.json');first=run_one(jobs[0]);print(json.dumps(first),flush=True)
    if first['status']!='ok':return dict(stopped='sentinel_failure',result=first)
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(run_one,j):j['review_id'] for j in jobs[1:]}
        for future in as_completed(futures):
            try:r=future.result()
            except Exception as exc:print(json.dumps(dict(review_id=futures[future],error=f'{type(exc).__name__}: {exc}')),flush=True)
            else:print(json.dumps(r),flush=True)
    return dict(results=len(list((ROOT/'results/reviews').glob('*/review.result.json'))))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=('prepare','freeze','run','verify'));a=p.parse_args()
    print(json.dumps({'prepare':prepare,'freeze':freeze,'run':run,'verify':verify_freeze}[a.command](),ensure_ascii=False),flush=True)
