"""Protect actual cited source before optional evaluator context, all arms alike."""
import copy,json
from collections import Counter
from common import digest
import judging48 as j
from warning_tracker import physical_rows
import grading48 as g

JUDGE_EXTRA_CAP=100000
class RequiredEvidenceOverflow(ValueError):pass

def required_sources(result):
    es=g.delivered_evidence(result);findings,_=g.actual_final_findings(result);out=[];requirements=[];seen=set()
    for i,f in enumerate(findings):
        for loc in j.finding_locations(f):
            if not j.valid_location_shape(loc):continue
            e=es.get(loc['evidence_id']);rows,problems=physical_rows(e)if e else({},['source_id_not_delivered'])
            expected=list(range(loc['start_line'],loc['end_line']+1));valid=bool(e and e['snapshot']==loc['snapshot']and e['path']==loc['path']and all(n in rows for n in expected))
            requirements.append(dict(review_id=result['review_id'],finding_index=i,location=copy.deepcopy(loc),reviewer_location_bound=valid,reviewer_binding_errors=problems))
            if not valid:continue
            # Preserve the complete actual delivered source unit including its
            # guards/initializers; diff units preserve only real physical rows.
            value={k:e.get(k)for k in('snapshot','path','kind','binding','mechanical_checks')}
            value.update(start_line=min(rows),end_line=max(rows),physical_rows={str(n):rows[n]for n in sorted(rows)},content='\n'.join(rows[n]for n in sorted(rows)),review_evidence_ids=[e['id']],scope_note='Mandatory actual cited source unit, protected before optional context. Original binding assumptions retained; no runtime or causal truth guarantee.')
            value['id']='REQ-'+digest(value)[:16]
            if value['id']not in seen:seen.add(value['id']);out.append(j.normalized_source(value))
    return out,requirements

def common_packet(case,truth,fixed_packet,results,*,expected_repeats,extra_token_cap=JUDGE_EXTRA_CAP):
    # Existing builder receives a deterministic protected prefix. Its original
    # semantic matching remains unchanged. No source is sourced from gold to
    # the reviewer; these are evaluator-only post-terminal packets.
    protected=copy.deepcopy(fixed_packet);sources=[j.normalized_source(v)for v in protected['sources']];covered={}
    for s in sources:
        for n,text in j.physical_source_map(s).items():covered[s['snapshot'],s['path'],n]=text
    mandatory=[];requirements=[];cost=0;events=[]
    from common import ARMS
    for r in sorted(results,key=lambda x:(x['repeat'],ARMS.index(x['arm']))):
        candidates,req=required_sources(r);requirements.extend(req)
        for s in candidates:
            rows=j.physical_source_map(s);keys=[(s['snapshot'],s['path'],n)for n in rows]
            for key in keys:
                if key in covered:assert covered[key]==rows[key[2]],'Immutable reviewer/evaluator source mismatch'
            if all(k in covered for k in keys):continue
            n=j.count_tokens(json.dumps(j.shown_source(s),ensure_ascii=False))
            if cost+n>extra_token_cap:raise RequiredEvidenceOverflow('Cited source union exceeds predeclared evaluator cap; do not issue incomplete judge or relabel model.')
            cost+=n;sources.append(s);mandatory.append(s['id']);events.append(dict(review_id=r['review_id'],source_id=s['id'],state='mandatory_cited_source_admitted',reference_tokens=n))
            for line,text in rows.items():covered[s['snapshot'],s['path'],line]=text
    protected['sources']=sources
    packet=j.build_common_packet(case,truth,protected,results,extra_token_cap=extra_token_cap-cost,expected_repeats=expected_repeats)
    packet.update(original_fixed_gold_packet_sha256=digest(fixed_packet),mandatory_cited_source_ids=mandatory,mandatory_source_display_reference_tokens=cost,optional_source_display_reference_tokens=packet['extra_source_display_reference_tokens'],extra_source_display_reference_tokens=packet['extra_source_display_reference_tokens']+cost,extra_token_cap=extra_token_cap,selection_events=events+packet['selection_events'])
    shown={}
    for s in packet['sources']:
        for n,text in j.physical_source_map(s).items():shown[s['snapshot'],s['path'],n]=text
    for req in requirements:
        loc=req['location'];req['judge_rows_present']=all((loc['snapshot'],loc['path'],n)in shown for n in range(loc['start_line'],loc['end_line']+1))
        if req['reviewer_location_bound']:assert req['judge_rows_present'],'Mandatory bound citation omitted from judge'
    packet['required_citation_delivery']=requirements;packet['required_citation_delivery_complete']=all(x['judge_rows_present']for x in requirements if x['reviewer_location_bound'])
    return packet
