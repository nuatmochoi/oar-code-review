"""Manual semantic and report-validity annotations; no automatic semantic grades."""
from common import ROOT,load,sha,write_new,digest

def record(owner,rid,*,cause,semantic,reason,finding_index=None,abstained=False,evidence=None):
    assert cause in (0,1) and semantic in (0,1) and cause<=semantic
    assert isinstance(reason,str) and len(reason)>20
    p=ROOT/'results/reviews'/rid/'review.result.json';r=load(p)
    truth=next(t for t in load(ROOT/'private/cases.json') if (t['case_id'],t['direction'])==(r['case_id'],r['direction']))
    unsafe=truth['unsafe'];assert type(unsafe) is bool
    raw=r.get('payload',{});raw=raw if isinstance(raw,dict) else {}
    findings=raw.get('findings',[]);findings=findings if isinstance(findings,list) else []
    n=len(findings);assert n==r['raw_reported_count']
    assert semantic<=min(n,int(unsafe))
    assert r['status']=='ok' or cause==0
    if semantic:
        assert type(finding_index) is int and 0<=finding_index<n
    canon=r['canonical_payload'].get('findings',[])
    assert len(canon)==n
    if cause:assert canon[finding_index]['file'] and canon[finding_index]['start_line']>0 and canon[finding_index]['evidence']
    actual=ROOT/'results/reviews'/rid/'initial.input.json';assert sha(actual)==r['actual_input_sha256']
    row={k:r[k] for k in ('review_id','case_id','language','direction','arm','repeat','stratum')}
    row.update(owner=owner,unsafe=unsafe,reported_count=n,cause_tp=cause,semantic_cause_tp=semantic,
        report_binding_losses=semantic-cause,reason=reason,finding_index=finding_index,evidence=evidence or [],
        output_failure=r['status']!='ok',transport_failure=r['transport_status']!='ok',abstained=bool(abstained),
        safe_false_alarm=not unsafe and n>0,result_sha256=sha(p),actual_input_sha256=sha(actual),truth_sha256=digest(truth))
    dest=ROOT/'grades'/f'{rid}.json'
    if dest.exists():assert load(dest)==row,('conflicting grade requires explicit adjudication',rid)
    else:write_new(dest,row)
    return row
