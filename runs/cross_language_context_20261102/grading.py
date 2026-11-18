"""Record human/AI manual reading decisions; never infer correctness from keywords."""
from __future__ import annotations
from pathlib import Path
from common import ROOT,load,sha,write_new,digest,normalized_blocks

def private_cases():
    rows=[]
    for lang in ('python','go'):
        x=load(ROOT/f'{lang}_context/private_cases.json')
        rows += x if isinstance(x,list) else x['cases']
    return {(r['case_id'],r['direction']):r for r in rows}

def record(owner,rid,*,cause,ground,reason,finding_index=None,evidence=None,abstained=False):
    assert cause in (0,1) and ground in (0,1) and ground<=cause
    assert isinstance(reason,str) and len(reason)>20
    result_path=ROOT/'results/reviews'/rid/'review.result.json';r=load(result_path)
    truth=private_cases()[r['case_id'],r['direction']]
    unsafe=truth['unsafe'];assert type(unsafe) is bool
    payload=r['payload'] if isinstance(r['payload'],dict) else {}
    findings=payload.get('findings',[])
    if not isinstance(findings,list):findings=[]
    n=len(findings)
    assert cause<=min(n,int(unsafe))
    assert r['status']=='ok' or cause==0
    if cause:
        assert type(finding_index) is int and 0<=finding_index<n
        finding=findings[finding_index]
        assert finding['file'] and finding['start_line']>0 and finding['evidence']
    actual=ROOT/'results/reviews'/rid/'initial.input.json'
    assert sha(actual)==r['actual_input_sha256']
    row={k:r[k] for k in ('review_id','case_id','language','direction','arm','repeat','stratum')}
    row.update(owner=owner,unsafe=unsafe,reported_count=n,cause_tp=cause,grounded_tp=ground,
               reason=reason,finding_index=finding_index,evidence=evidence or [],
               output_failure=r['status']!='ok',abstained=bool(abstained),safe_false_alarm=not unsafe and n>0,
               result_sha256=sha(result_path),actual_input_sha256=sha(actual),truth_sha256=digest(truth))
    dest=ROOT/'grades'/f'{rid}.json'
    if dest.exists():
        assert load(dest)==row,('conflicting grade requires explicit adjudication',rid)
    else:write_new(dest,row)
    return row
