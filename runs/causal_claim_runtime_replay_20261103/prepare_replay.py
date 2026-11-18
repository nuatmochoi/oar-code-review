"""Source-only preparation. The wrong historical mechanism is never corrected here."""
import ast,copy,json,re
from pathlib import Path
from common import ROOT,load,sha,digest,write_new,normalize_block
from python_tools import _mask
from claim_controller import public_diff_evidence
from prepare_initial import annotate,block_key

PARENT=ROOT.parent/'causal_claim_loop_20261103'

def historical_blocks(text,case):
    out=[]
    for attrs,body in re.findall(r'<file\s+([^>]+)>(.*?)</file>',text,re.S):
        fields=dict(re.findall(r'(\w+)="([^"]*)"',attrs))
        if not all(k in fields for k in ('evidence_id','path','lines')):continue
        match=re.fullmatch(r'(\d+)-(\d+)',fields['lines'])
        if not match:continue
        start,end=map(int,match.groups());parsed=[]
        for line in body.strip('\n').split('\n'):
            m=re.match(r'^(\d+): ?(.*)$',line)
            if m:parsed.append((int(m[1]),m[2]))
        assert [n for n,_ in parsed]==list(range(start,end+1))
        snapshot='BASE'if fields['evidence_id'].endswith('@base')else'HEAD'
        spec=case['snapshots'][snapshot];path=fields['path'];p=Path(spec['root'])/path
        assert sha(p)==spec['files'][path]
        actual=_mask(p.read_bytes().decode()).split('\n')[start-1:end]
        assert actual==[t for _,t in parsed],(snapshot,path,start,end)
        out.append(dict(id=fields['evidence_id'],snapshot=snapshot,path=path,start_line=start,end_line=end,content='\n'.join(actual),kind='source_lines',provenance='historical_input_exact_masked_source',mechanical_checks=dict(raw_sha256=sha(p),source_hash_verified=True,actual_range=[start,end],mask_policy='legacy-compatible-comment-docstring-mask-physical-lines-v1',semantic_truth_guaranteed=False),binding={}))
    assert out
    return out

def public_imports(case):
    result={}
    for snapshot,spec in case['snapshots'].items():
        for rel in case['changed_files']:
            p=Path(spec['root'])/rel;assert sha(p)==spec['files'][rel]
            for n in ast.walk(ast.parse(p.read_bytes())):
                if isinstance(n,ast.ImportFrom)and n.module:
                    for a in n.names:result[(snapshot,n.module,a.name)]=dict(snapshot=snapshot,module=n.module,symbol=a.name,alias=a.asname or a.name,file=rel,line=n.lineno,column=0)
    return sorted(result.values(),key=lambda x:(x['snapshot'],x['file'],x['line'],x['symbol']))

def seed_payload(raw,case,evidence,imports):
    findings=raw['findings'];assert len(findings)==1
    old=findings[0];mechanism=old['mechanism'];anchor=next(b for b in evidence if b['snapshot']=='HEAD'and b['path']==old['path']and b['start_line']<=old['line_start']<=b['end_line'])
    def bound(b,a,z,why):return dict(status='bound',evidence_id=b['id'],snapshot=b['snapshot'],path=b['path'],start_line=a,end_line=z,reason=why)
    unknown=dict(status='unresolved',evidence_id='',snapshot='HEAD',path='',start_line=0,end_line=0,reason='The historical response has no typed physical failure-operation slot. The adapter supplies no fabricated implementation location.')
    converted=dict(claim_id='historical-C1',title=old['title'],explanation=mechanism,trigger=old['trigger'],impact=old['impact'],regression_anchor=bound(anchor,old['line_start'],old['line_end'],'Historical report anchor, mechanically translated to the supplied source; no semantic repair.'),failure_operation=unknown,supporting_evidence=[bound(anchor,old['line_start'],old['line_end'],'Historical cited changed-source range only.')],suggested_fix_target=raw['review']['observations'][0]['recommendation'],uncertainties=[])
    questions=[]
    for item in imports:
        if item['snapshot']!='HEAD' or item['symbol']not in mechanism:continue
        questions.append(dict(id=f'historical-Q{len(questions)+1}',claim_id='historical-C1',kind='implementation_body',query='implementation',snapshot='HEAD',file=item['file'],line=item['line'],column=0,symbol=item['module']+'.'+item['symbol'],reason='The historical explanation depends on this imported API. Inspect its implementation before treating the asserted exception mechanism as verified.'))
    return dict(assessment='issue',findings=[converted],claims=[dict(id='historical-C1',claim=mechanism,trigger=old['trigger'],action='new',change_basis='none',reason='Verbatim historical mechanism replay; this adapter is not a new reviewer judgment and adds no alternative causal hypothesis.',conditions=[dict(id='historical-C1-P1',predicate=mechanism,kind='exception_branch',snapshot='HEAD',status='unresolved',evidence_ids=[],reason='The historical model asserted this mechanism; the new controller has not verified its necessary API predicate. The raw original assertion and confidence are preserved separately.',effect_if_true='This asserted exception could explain the historical reported failure.',effect_if_false='This exact exception mechanism cannot explain the failure; any other route remains to be investigated.')],challenges=[dict(id='historical-CH1',text='Check whether the asserted API exception actually occurs; if refuted, follow the next relevant value consumer without assuming an alternative cause.',state='pending',evidence_ids=[],reason='Generic counterexample and consumer follow-up obligation, not a supplied answer.')],termination='unresolved')],questions=questions,reasoning=raw['review']['summary'],termination_reason='Historical replay starting point, not a fresh model output.')

def main():
    case=copy.deepcopy(next(c for c in load(PARENT/'prepared/cases.json')if c['id']=='PY_N03_a'))
    raw_record=load(ROOT/'seed/historical.initial.result.json');raw=json.loads(raw_record['raw_response'])
    old_input=load(ROOT/'seed/historical.initial.input.json')
    case['initial_blocks']=historical_blocks(old_input['composed_text'],case)
    write_new(ROOT/'prepared/cases.json',[case]);annotate([case])
    annotations=load(ROOT/'prepared/initial_body_annotations_final'/f'{case["id"]}.json');evidence=[]
    for b in case['initial_blocks']:
        b=copy.deepcopy(b);b['mechanical_checks'].update(annotations[block_key(b)]);evidence.append(normalize_block(b))
    evidence+=public_diff_evidence(case['diff'])
    imports=public_imports(case);payload=seed_payload(raw,case,evidence,imports)
    write_new(ROOT/'prepared/seed_payload_v2.json',payload);write_new(ROOT/'prepared/initial_evidence.json',evidence);write_new(ROOT/'prepared/public_imports.json',imports)
    write_new(ROOT/'prepared/replay_provenance.json',dict(historical_response_reference=dict(path=str(ROOT/'seed/historical.initial.result.json'),sha256=sha(ROOT/'seed/historical.initial.result.json')),historical_raw_response_sha256=digest(raw_record['raw_response']),wrong_mechanism_verbatim=raw['findings'][0]['mechanism'],new_payload_sha256=digest(payload),source_blocks=len(case['initial_blocks']),source_policy='Only exact source frames in historical input, checked against current public snapshot manifest. Static queries may retrieve from the same V1 public allowed pool. Historical whole input preserved but is not automatically pasted into new reviewer input.',replay_not_fresh_initial=True,cohort='development_historical_error_mechanism_fixture',extra_correct_consumer_hint=False,controller_normalization='Original erroneous mechanism is verbatim. Its controller condition begins unresolved because manual schema adaptation is not new semantic verification. Historical original asserted confidence remains in raw seed.',model_calls=0))
    return dict(case=case['id'],blocks=len(evidence),seed_questions=len(payload['questions']))

if __name__=='__main__':print(main())
