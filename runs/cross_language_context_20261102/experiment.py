"""Freeze and execute independent one-judgment reviews under a shared admission gate."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import random
import time
from pathlib import Path
from common import ROOT, ARMS, REPEATS, MODEL, load, sha, digest, write_new, source_rows, source_tokens, prompt_for, response_schema
import cli_transport
import reliable_transport as transport

def prepare():
    assert not (ROOT/'freeze.json').exists()
    cases = []
    for lang in ('python','go'):
        records = load(ROOT/f'{lang}_context/public_cases.json')
        assert isinstance(records,list) and len(records)==12, (lang,len(records))
        cases += records
    assert len({(c['case_id'],c['direction']) for c in cases})==24
    for c in cases:
        assert set(c['arms'])==set(ARMS)
        assert c['arms']['diff']==[]
        ast = source_rows(c['arms']['ast']); lsp = source_rows(c['arms']['lsp']); search = source_rows(c['arms']['search'])
        assert all(lsp.get(k)==v for k,v in ast.items()), (c['case_id'],'AST loss')
        assert all(search.get(k)==v for k,v in lsp.items()), (c['case_id'],'LSP loss')
        assert all(source_rows(c['arms']['oracle']).get(k)==v for k,v in ast.items()), (c['case_id'],'oracle AST loss')
        for arm in ARMS:
            assert source_tokens(c['arms'][arm])<=5200,(c['case_id'],arm,source_tokens(c['arms'][arm]))
    jobs=[]
    for c in cases:
        for arm in ARMS:
            raw=prompt_for(c,arm)
            for repeat in range(1,REPEATS+1):
                rid=f'{c["case_id"]}_{c["direction"]}_{arm}_r{repeat}'
                jobs.append(dict(review_id=rid,case_id=c['case_id'],direction=c['direction'],language=c['language'],stratum=c['stratum'],arm=arm,repeat=repeat,
                                 raw=raw,source_tokens=source_tokens(c['arms'][arm]),public_case_hash=digest(c),raw_hash=digest(raw)))
    random.Random(2026110201).shuffle(jobs)
    write_new(ROOT/'prepared/cases.json',cases)
    write_new(ROOT/'prepared/jobs.json',jobs)
    write_new(ROOT/'prepared/schema.json',response_schema())
    write_new(ROOT/'prepared/summary.json',dict(cases=12,directions=len(cases),reviews=len(jobs),logical_calls=len(jobs),seed=2026110201,arms=ARMS))
    return dict(reviews=len(jobs),cases=12)

def freeze():
    assert not (ROOT/'freeze.json').exists()
    jobs=load(ROOT/'prepared/jobs.json');assert len(jobs)==360
    assert load(ROOT/'verification/tests.json')['passed']
    assert load(ROOT/'audit/preservation_before.json')['passed']
    assert load(ROOT/'audit/source_audit.json')['passed']
    files={}
    for p in ROOT.rglob('*'):
        if not p.is_file() or any(x in ('__pycache__','.pycache','.pytest_cache','.venv','results','workspaces') or x.endswith('_workspaces') for x in p.relative_to(ROOT).parts):continue
        if p.suffix in ('.pyc','.pyo','.log'):continue
        files[str(p.relative_to(ROOT))]=sha(p)
    # Data/source files under workspaces are covered by the collector's source manifest.
    write_new(ROOT/'freeze.json',dict(files=files,created_at=time.time(),model=MODEL,reasoning_effort='low',workers=4,min_interval=3,reviews=360,logical_calls=360))
    return dict(files=len(files),reviews=360)

def verify_freeze():
    f=load(ROOT/'freeze.json')
    for p,h in f['files'].items():assert sha(ROOT/p)==h,('frozen change',p)
    return f

def run_one(job):
    rid=job['review_id'];dest=ROOT/'results/reviews'/rid/'review.result.json'
    if dest.exists():
        saved=load(dest);assert saved['job_hash']==digest(job);return dict(review_id=rid,preserved=True,status=saved['status'])
    gate=transport.AdmissionGate(ROOT/'results/gate',max_live_calls=4,min_interval=3)
    folder=ROOT/'results/reviews'/rid
    if (folder/'initial.claim.json').exists():
        if not (folder/'initial.result.json').exists():raise transport.ClaimedIncomplete(rid)
    else:
        client=transport.CliCompletionClient(out_dir=ROOT/'results/reviews',review_id=rid,schema=load(ROOT/'prepared/schema.json'),model=MODEL,gate=gate,max_calls=1,timeout=300)
        client.begin_stage('initial')
        try:client.complete(**job['raw'])
        except transport.CallFailed:pass
    record=load(folder/'initial.result.json');actual=load(folder/'initial.input.json')
    assert actual['raw']==job['raw'],('actual input changed',rid)
    try:payload=json.loads(record.get('raw_response') or '{}')
    except ValueError:payload={}
    parsed_payload_type=type(payload).__name__
    if not isinstance(payload,dict):payload={}
    value={k:v for k,v in job.items() if k!='raw'}
    value.update(job_hash=digest(job),freeze_sha256=sha(ROOT/'freeze.json'),status=record['status'],payload=payload,parsed_payload_type=parsed_payload_type,
                 stage_result_sha256=sha(folder/'initial.result.json'),actual_input_sha256=sha(folder/'initial.input.json'),
                 physical_attempt_count=record['physical_attempt_count'],retry_count=record['retry_count'],actual_usage=record['actual_usage'],
                 duration_seconds=record['timing']['all_attempts_duration_seconds'],attempts_without_reported_usage=record['attempts_without_reported_usage'])
    write_new(dest,value)
    return dict(review_id=rid,status=value['status'],findings=len(payload.get('findings',[])))

def run():
    verify_freeze();jobs=load(ROOT/'prepared/jobs.json')
    # The preseeded first evaluation doubles as the real transport sentinel.
    first=run_one(jobs[0]);print(json.dumps(first),flush=True)
    if first['status']!='ok':return dict(stopped='sentinel_failure',result=first)
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(run_one,j):j['review_id'] for j in jobs[1:]}
        for future in as_completed(futures):
            try:r=future.result()
            except Exception as exc:
                print(json.dumps(dict(review_id=futures[future],error=f'{type(exc).__name__}: {exc}')),flush=True)
            else:print(json.dumps(r),flush=True)
    return dict(results=len(list((ROOT/'results/reviews').glob('*/review.result.json'))))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=('prepare','freeze','run','verify'))
    args=p.parse_args();print(json.dumps({'prepare':prepare,'freeze':freeze,'run':run,'verify':verify_freeze}[args.command](),ensure_ascii=False),flush=True)
