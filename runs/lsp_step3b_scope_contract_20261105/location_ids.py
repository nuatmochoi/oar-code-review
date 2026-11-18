"""Lossless location selection from physically delivered rows, not semantic repair."""
import copy,json
from schemas import REVIEW,obj,enum,S
from warning_tracker import physical_rows
from common import digest

def catalog(evidence):
    out={};by_evidence={};seq=0
    for e in sorted(evidence,key=lambda v:v['snapshot']!='HEAD'):
        rows,errors=physical_rows(e)
        if errors or not rows:continue
        ids={};segments=[];start=previous=None
        for n in sorted(rows):
            seq+=1;lid=str(seq)
            out[lid]=dict(status='bound',evidence_id=e['id'],snapshot=e['snapshot'],path=e['path'],start_line=n,end_line=n,reason='Controller-bound actual delivered physical row; semantic relevance unverified.')
            ids[str(n)]=seq
            if previous is not None and n!=previous+1:segments.append((start,previous));start=None
            if start is None:start=n
            previous=n
        if start is not None:segments.append((start,previous))
        blocks=[]
        for a,b in segments:
            if a==b:continue
            seq+=1;lid=str(seq)
            out[lid]=dict(status='bound',evidence_id=e['id'],snapshot=e['snapshot'],path=e['path'],start_line=a,end_line=b,reason='Controller-bound actual contiguous delivered segment; semantic relevance unverified.')
            blocks.append([a,b,seq])
        by_evidence[e['id']]=dict(rows=ids,contiguous_segments=blocks)
    return dict(locations=out,by_evidence=by_evidence,unresolved_id=0,head_location_count=sum(v['snapshot']=='HEAD'for v in out.values()),semantic_truth_guaranteed=False)

def wire_schema(evidence):
    c=catalog(evidence);s=copy.deepcopy(REVIEW)
    # Dense integer IDs give exactly the valid choices without a thousands-item
    # enum that would exceed the API schema's 1000-enum-value limit.
    s['$defs']={'location_selection':obj(dict(location_id=dict(type='integer',minimum=0,maximum=len(c['locations'])),reason=S))}
    s['$defs']['head_location_selection']=obj(dict(location_id=dict(type='integer',minimum=0,maximum=c['head_location_count']),reason=S))
    f=s['properties']['findings']['items']['properties']
    for key in ['regression_anchor','failure_operation']:f[key]={'$ref':'#/$defs/head_location_selection'}
    f['supporting_evidence']['items']={'$ref':'#/$defs/location_selection'}
    return s,c

def decode(payload,c):
    out=copy.deepcopy(payload)
    def one(loc):
        lid=loc['location_id']
        if lid==0:return dict(status='unresolved',evidence_id='',snapshot='HEAD',path='',start_line=0,end_line=0,reason=loc['reason'])
        value=copy.deepcopy(c['locations'][str(lid)]);value['reason']=loc['reason'];return value
    for f in out['findings']:
        for k in ['regression_anchor','failure_operation']:f[k]=one(f[k])
        f['supporting_evidence']=[one(x)for x in f['supporting_evidence']]
    return out

def render_catalog(c):
    # Row maps retain exact evidence/snapshot identity without repeating source.
    return json.dumps(c['by_evidence'],ensure_ascii=False,separators=(',',':'))

def verify_result(r):
    from pathlib import Path
    from common import load,sha
    from openai_transport import parse_payload
    ref=r['actual_final_provider_reference'];p=Path(ref['path'])
    assert sha(p)==ref['sha256']
    wire=parse_payload(load(p));delivery=load(p.parents[1]/'final.delivered_evidence.json')
    assert wire==r['raw_wire_payload']
    assert digest(delivery['evidence'])==delivery['evidence_sha256']
    assert delivery['evidence']==r['evidence']
    assert catalog(delivery['evidence'])==delivery['location_catalog']
    assert decode(wire,delivery['location_catalog'])==r['raw_final_payload']
    return True
