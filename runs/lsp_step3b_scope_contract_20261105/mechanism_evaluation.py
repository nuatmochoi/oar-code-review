"""Separate, prospective AI adjudication of provisional-claim corrections.

Never part of the reviewer input. Source spans are mechanically extracted;
semantic refutation, causal continuity and use of refutation remain AI labels.
"""
import json,copy
from concurrent.futures import ThreadPoolExecutor
import jsonschema
from common import ROOT,PROJECT,WORKERS,load,sha,digest,write_new
from schemas import obj,arr,S,enum
from causal_loop import cases,units,shared_path,ARMS
from openai_transport import complete,parse_payload
from judging48 import shown_source,physical_source_map
import grading48 as g

BRANCHES=('THREE','FOUR_SAME','FOUR_QUERY')
MASK={a:f'PATH{i+1}'for i,a in enumerate(BRANCHES)}
SYSTEM='''You are a separate nonblind AI mechanism auditor, not a human evaluator and not the reviewer. Assess EVERY supplied initial provisional claim exactly once, and EVERY final path for that claim exactly once. Supplied benchmark labels are not proof. Determine whether the literal INITIAL causal assumption is source-supported, source-refuted, or unresolved. Uncertainty, missing context, lack of a warning, and an unconfirmed path are not refutation. A claim may contain a correct narrow defect but an unverified broad impact; assess the causal core and explicitly note the scope.
For each path distinguish retained/revised/withdrawn/omitted, and assess the literal resulting causal explanation separately. Distinguish a correction of the same failure investigation from discovery of a different defect. Select an actual final finding index or -1 if unreported; never count deletion, withholding, or withdrawal as a successful correction. A corrected explanation must describe the real trigger, failing/wrong-value operation and bounded effect. final_supported without a reported actual finding is not correction success. Judge whether the model explicitly uses refuting source to change its explanation, rather than just repeating self-reflection.
Use only displayed evaluator source IDs and inclusive physical start/end rows, with no gaps. The controller extracts exact text. Each supported/refuted status requires at least one corresponding source assessment; all source assessment reasons must explain the particular proposition. These common facts DO NOT prove that any reviewer saw them. The controller separately checks actual delivery before the final response, independently of your semantic judgments. Static edges/definitions are not implementation execution; environment assumptions remain assumptions. Return only the schema. No tools or external knowledge. Zero initial claims is valid.'''

def schema(initial,results,packet):
    span=obj(dict(source_id=enum(*(s['id']for s in packet['sources'])),start_line={'type':'integer','minimum':1},end_line={'type':'integer','minimum':1},judgment=enum('supports','refutes','unresolved'),reason=S))
    status=enum('supported','refuted','unresolved')
    paths=[]
    for a in BRANCHES:
        ids=[c['id']for c in(results[a].get('payload')or{}).get('claims',[])]
        paths.append(obj(dict(path=enum(MASK[a]),final_claim_id=enum('',*ids),transition=enum('retained','revised','withdrawn','omitted'),final_status=status,same_failure_investigation={'type':'boolean'},explicitly_uses_refutation={'type':'boolean'},finding_index={'type':'integer','minimum':-1,'maximum':max(-1,results[a]['raw_findings_count']-1)},source_assessments=arr(span),reason=S)))
    # Use a union of path schemas so finding bounds remain path-specific.
    item=obj(dict(initial_claim_id=enum(*(c['id']for c in initial['claims'])),initial_status=status,initial_assessments=arr(span),paths=arr({'anyOf':paths}),reason=S))
    return obj(dict(claims=arr(item),limitations=arr(S)))

def source_span(a,packet):
    source=next(s for s in packet['sources']if s['id']==a['source_id']);rows=physical_source_map(source);lo,hi=a['start_line'],a['end_line']
    assert 1<=lo<=hi and all(n in rows for n in range(lo,hi+1)),'Gapped/missing source span'
    fragment='\n'.join(rows[n]for n in range(lo,hi+1));assert len(fragment)>2
    return dict(**a,fragment=fragment,fragment_sha256=digest(fragment),snapshot=source['snapshot'],path=source['path'],rows={str(n):rows[n]for n in range(lo,hi+1)})

def available(span,evidence):
    """Exact path/side/text availability only; not causal or runtime proof."""
    covered={}
    for e in evidence:
        if e.get('snapshot')!=span['snapshot']or e.get('path')!=span['path']:continue
        rows=g.physical_rows(e,e['snapshot'])
        if not rows:continue
        if e.get('kind')in('diff','diff_rows','diff_source','diff_hunk'):values=g.diff_physical_rows(e['content'],e['snapshot'],e['path'])
        else:values=dict(enumerate(e['content'].splitlines(),e['start_line']))
        for n in rows:
            if str(n)in span['rows']and values.get(n)==span['rows'][str(n)]:covered[n]=e['id']
    return dict(all_rows_available=all(int(n)in covered for n in span['rows']),evidence_ids=sorted(set(covered.values())),scope='Literal path/snapshot/text availability, not deployed binding, causal truth, or evidence of model use.')

def validate(payload,initial,results,packet):
    ids=[c['id']for c in initial['claims']];assert len(set(ids))==len(ids)
    assert sorted(x['initial_claim_id']for x in payload['claims'])==sorted(ids),'Every initial claim exactly once'
    rows=[]
    for c in payload['claims']:
        first=[source_span(a,packet)for a in c['initial_assessments']]
        if c['initial_status']!='unresolved':assert any(a['judgment']==('supports'if c['initial_status']=='supported'else'refutes')for a in first)
        assert sorted(p['path']for p in c['paths'])==sorted(MASK.values())
        for p in c['paths']:
            a=next(a for a in BRANCHES if MASK[a]==p['path']);r=results[a];claims=(r.get('payload')or{}).get('claims',[]);final=next((x for x in claims if x['id']==p['final_claim_id']),None)
            assert (p['transition']=='omitted')==(final is None)
            if p['transition']=='withdrawn':assert final['action']=='withdraw'
            assessments=[source_span(x,packet)for x in p['source_assessments']]
            if p['final_status']!='unresolved':assert any(x['judgment']==('supports'if p['final_status']=='supported'else'refutes')for x in assessments)
            findings,_=g.actual_final_findings(r);idx=p['finding_index'];assert idx==-1 or 0<=idx<len(findings)
            if idx>=0:assert final is not None and findings[idx]['claim_id']==final['id'],'Finding must literally belong to selected final claim'
            row=dict(initial_claim_id=c['initial_claim_id'],arm=a,initial_status=c['initial_status'],final_status=p['final_status'],transition=p['transition'],same_failure_investigation=p['same_failure_investigation'],explicitly_uses_refutation=p['explicitly_uses_refutation'],finding_index=idx,reason=p['reason'],source_assessments=assessments,initial_assessments=first)
            rows.append(row)
    return rows

def one(cn):
    case,n=cn;rid=f'{case["id"]}_r{n}';out=ROOT/'evaluation/mechanism'/rid;dest=out/'audit.json';failed=out/'failure.json'
    if dest.exists()or failed.exists():return
    s=load(shared_path(case,n));initial=s['initial'];results={a:load(ROOT/'results/reviews'/f'{case["id"]}_{a}_r{n}'/'review.result.json')for a in BRANCHES}
    if initial is None or not initial['claims']:
        write_new(dest,dict(case_id=case['id'],repeat=n,rows=[],no_initial_claims=initial is not None,initial_failed=initial is None,new_model_calls=0));return
    packet=load(ROOT/'private/common_judge_packets'/f'{case["id"]}.json');truth=next(t for t in load(ROOT/'private/truth.json')['cases']if t['id']==case['id'])
    from causal_loop import answer_view,extra_path
    paths={}
    for a in BRANCHES:
        tp=ROOT/'results/reviews'/results[a]['review_id']/'query_round_trace.json';trace=load(tp)if tp.exists()else{}
        additional=trace.get('additional_round')
        paths[MASK[a]]=dict(final_payload=results[a].get('raw_final_payload'),additional_round=answer_view(additional)if additional else None)
    history=dict(first=answer_view(s['first'])if s['first']else None,replan2=s['replan'],second=answer_view(s['second'])if s['second']else None,extra_replan3=load(extra_path(case,n))['payload']if extra_path(case,n).exists()else None)
    raw=dict(system=SYSTEM,prompt=json.dumps(dict(public_title=case['title'],initial_claims=initial['claims'],shared_history=history,paths=paths,targets=truth['targets'],evaluator_sources=[shown_source(x)for x in packet['sources']],source_limitations=packet.get('unresolved',[])),ensure_ascii=False));sc=schema(initial,results,packet)
    receipt=complete(out,'judge',raw,sc)
    try:
        payload=parse_payload(receipt);assert payload is not None;jsonschema.validate(payload,sc);rows=validate(payload,initial,results,packet)
        for row in rows:
            r=results[row['arm']];row['case_id']=case['id'];row['repeat']=n
            refutations=[x for x in row['initial_assessments']if x['judgment']=='refutes']
            later=[dict(span=x,before_initial=available(x,s['initial_evidence']),before_final=available(x,list(g.delivered_evidence(r).values())))for x in refutations]
            row['refutation_delivery']=later
            gp=ROOT/'grades/final'/f'{r["review_id"]}.json'
            # Actual final grades are consulted only in aggregation; record raw
            # index and source exposure here, not a replacement main judgment.
            row['main_grade_path']=str(gp);row['refutation_delivered_before_final']=any(x['before_final']['all_rows_available']for x in later)
            row['new_refutation_body_after_initial']=any(x['before_final']['all_rows_available']and not x['before_initial']['all_rows_available']for x in later)
        write_new(dest,dict(case_id=case['id'],repeat=n,rows=rows,new_model_calls=receipt['physical_attempt_count'],nonblind_AI=True,human_review=False,mechanical_check_scope='Source availability only, not semantic correctness or actual mental use.'))
    except Exception as exc:write_new(failed,dict(case_id=case['id'],repeat=n,error=type(exc).__name__+': '+str(exc),no_semantic_repair=True))

def run():
    with ThreadPoolExecutor(max_workers=WORKERS)as p:list(p.map(one,units()))
    rows=[];failures=[]
    for c,n in units():
        folder=ROOT/'evaluation/mechanism'/f'{c["id"]}_r{n}'
        if(folder/'audit.json').exists():rows+=load(folder/'audit.json')['rows']
        else:failures.append(load(folder/'failure.json'))
    from post.aggregate_lsp import make_row
    # Main F1 rows preserve adjudication failures and labels independently.
    truth={t['id']:t for t in load(ROOT/'private/truth.json')['cases']};subset=load(ROOT/'private/source_supported_subset.json')['supported_primary_target_ids'];case_map={c['id']:c for c in cases()}
    scores={}
    for row in rows:
        rid=f'{row["case_id"]}_{row["arm"]}_r{row["repeat"]}';rp=ROOT/'results/reviews'/rid/'review.result.json';gp=ROOT/'grades/final'/f'{rid}.json'
        if gp.exists():
            rr=make_row(truth[row['case_id']],row['arm'],row['repeat'],load(rp),load(gp),'complete',subset[row['case_id']],case_map[row['case_id']]);label=next((v for v in rr['finding_labels']if v['finding_index']==row['finding_index']),None)
        else:label=None
        row['final_report_cause_confirmed']=bool(label and label['cause_correct']and label['label']in('target_correct','other_confirmed_defect'))
        row['strict_correction']=bool(row['initial_status']=='refuted'and row['final_status']=='supported'and row['transition']=='revised'and row['same_failure_investigation']and row['explicitly_uses_refutation']and row['refutation_delivered_before_final']and row['final_report_cause_confirmed'])
    for a in BRANCHES:
        rs=[r for r in rows if r['arm']==a];wrong=sum(r['initial_status']=='refuted'for r in rs);corrected=sum(r['strict_correction']for r in rs)
        scores[a]=dict(audited_initial_claims=len(rs),initial_refuted=wrong,strict_corrected=corrected,strict_correction_rate=corrected/wrong if wrong else None,initial_supported=sum(r['initial_status']=='supported'for r in rs),supported_to_refuted=sum(r['initial_status']=='supported'and r['final_status']=='refuted'for r in rs),new_refutation_delivered=sum(r['new_refutation_body_after_initial']for r in rs),explicit_refutation_used=sum(r['explicitly_uses_refutation']for r in rs),reported_confirmed=sum(r['final_report_cause_confirmed']for r in rs))
    value=dict(scores=scores,rows=rows,failures=failures,human_review=False,nonblind_AI=True)
    write_new(ROOT/'post/mechanism.json',value);return value
