"""Independent semantic correspondence; no benchmark expansion or score repair."""
import json
from concurrent.futures import ThreadPoolExecutor
import jsonschema
from common import ROOT,WORKERS,load,write_new
from schemas import obj,arr,S,enum
from candidate_run import cases,units
from judging48 import shown_source,physical_source_map
from openai_transport import complete,parse_payload
import grading48 as g

SYSTEM='''Compare the literal initial and final code-review findings, using only supplied frozen source. This is nonblind AI semantic correspondence, not human review. Assess every initial finding exactly once and each final path exactly once. Match only the SAME causal instance: concrete trigger, failing/wrong-value operation and effect. Similar titles, files or functions do not suffice. A different real defect must not replace the original in retention counts. A narrower final explanation may retain the same established causal core; explain the scope change. A wrong explanation may be related but is not a retained correct defect; final correctness is adjudicated separately. Select final_finding_index=-1 for no correspondence; never invent or repair a report. For any correspondence cite at least one supplied source span that establishes the causal link. Use actual inclusive physical rows without gaps. The controller extracts text; this does not certify your semantic matching. Return all entries, including initially false/unresolved claims, without relying on previous AI labels. No tools or external facts.'''

def one(cn):
    case,n=cn;folder=ROOT/'evaluation/retention'/f'{case["id"]}_r{n}';dest=folder/'audit.json';fp=folder/'failure.json'
    if dest.exists()or fp.exists():return
    rs={a:load(ROOT/'results/reviews'/f'{case["id"]}_{a}_r{n}'/'review.result.json')for a in('INITIAL','PLAIN','LEDGER')};findings={a:g.actual_final_findings(r)[0]for a,r in rs.items()}
    if not findings['INITIAL']:write_new(dest,dict(rows=[],new_model_calls=0));return
    packet=load(ROOT/'private/common_judge_packets'/f'{case["id"]}.json');span=obj(dict(source_id=enum(*(s['id']for s in packet['sources'])),start_line={'type':'integer','minimum':1},end_line={'type':'integer','minimum':1},reason=S))
    branches=[obj(dict(path=enum(a),final_finding_index={'type':'integer','minimum':-1,'maximum':max(-1,len(findings[a])-1)},same_causal_instance={'type':'boolean'},source_assessments=arr(span),reason=S))for a in('PLAIN','LEDGER')]
    sc=obj(dict(initial_findings=arr(obj(dict(initial_finding_index={'type':'integer','minimum':0,'maximum':len(findings['INITIAL'])-1},paths=arr({'anyOf':branches})))),limitations=arr(S)))
    raw=dict(system=SYSTEM,prompt=json.dumps(dict(public_title=case['title'],actual_findings=findings,sources=[shown_source(s)for s in packet['sources']]),ensure_ascii=False))
    receipt=complete(folder,'judge',raw,sc)
    try:
        p=parse_payload(receipt);assert p is not None;jsonschema.validate(p,sc);assert sorted(v['initial_finding_index']for v in p['initial_findings'])==list(range(len(findings['INITIAL'])))
        rows=[]
        for v in p['initial_findings']:
            assert sorted(b['path']for b in v['paths'])==['LEDGER','PLAIN']
            for b in v['paths']:
                i=b['final_finding_index'];assert b['same_causal_instance']==(i>=0)
                spans=[]
                for a in b['source_assessments']:
                    source=next(s for s in packet['sources']if s['id']==a['source_id']);rr=physical_source_map(source);lo,hi=a['start_line'],a['end_line'];assert 1<=lo<=hi and all(k in rr for k in range(lo,hi+1));fragment='\n'.join(rr[k]for k in range(lo,hi+1));assert len(fragment)>2;spans.append(dict(**a,fragment=fragment))
                if i>=0:assert spans
                rows.append(dict(case_id=case['id'],repeat=n,initial_finding_index=v['initial_finding_index'],arm=b['path'],final_finding_index=i,same_causal_instance=b['same_causal_instance'],reason=b['reason'],source_assessments=spans))
        # Enforce one-to-one matching within a path, rather than multiplying a TP.
        for a in('PLAIN','LEDGER'):
            indices=[r['final_finding_index']for r in rows if r['arm']==a and r['final_finding_index']>=0];assert len(indices)==len(set(indices))
        write_new(dest,dict(rows=rows,new_model_calls=receipt['physical_attempt_count'],human_review=False,nonblind_AI=True))
    except Exception as exc:write_new(fp,dict(case_id=case['id'],repeat=n,error=type(exc).__name__+': '+str(exc),no_semantic_repair=True))

def run():
    with ThreadPoolExecutor(max_workers=WORKERS)as p:list(p.map(one,units()))
    rows=[];failures=[]
    for c,n in units():
        folder=ROOT/'evaluation/retention'/f'{c["id"]}_r{n}'
        if(folder/'audit.json').exists():rows+=load(folder/'audit.json')['rows']
        else:failures.append(load(folder/'failure.json'))
    scores={a:dict(initial_confirmed_true=0,retained_confirmed_true=0,lost_or_unresolved=0,initial_primary_true=0,retained_primary_true=0,initial_off_gold_true=0,retained_off_gold_true=0)for a in('PLAIN','LEDGER')}
    for row in rows:
        labels={}
        for a in('INITIAL',row['arm']):
            p=ROOT/'grades/final'/f'{row["case_id"]}_{a}_r{row["repeat"]}.json';labels[a]=load(p)['finding_labels']if p.exists()else[]
        before=next((v for v in labels['INITIAL']if v['finding_index']==row['initial_finding_index']),None);after=next((v for v in labels[row['arm']]if v['finding_index']==row['final_finding_index']),None)
        true=lambda v:bool(v and v['cause_correct']and v['label']in('target_correct','other_confirmed_defect'))
        row['initial_confirmed_true']=true(before);row['retained_confirmed_true']=true(before)and true(after)and row['same_causal_instance']
        if true(before):
            s=scores[row['arm']];s['initial_confirmed_true']+=1;s['retained_confirmed_true']+=row['retained_confirmed_true'];s['lost_or_unresolved']+=not row['retained_confirmed_true']
            primary=bool(before['primary_cause_tp']);off=before['label']=='other_confirmed_defect';s['initial_primary_true']+=primary;s['retained_primary_true']+=primary and row['retained_confirmed_true'];s['initial_off_gold_true']+=off;s['retained_off_gold_true']+=off and row['retained_confirmed_true']
    value=dict(scores=scores,rows=rows,failures=failures,human_review=False,nonblind_AI=True,scope='Matched subset of terminal successful AI audits. Lost_or_unresolved includes judge uncertainty; no fabricated failure label.')
    write_new(ROOT/'post/retention.json',value);return value
