"""Source-only utilities and prospective limits for claim-loop experiment."""
from pathlib import Path
import hashlib,json
import oar_core
ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[1]
MODEL='gpt-5.6-sol'
REPEATS=2
ARMS=('A','B','C','D')
BUDGETS=dict(max_source_tokens=12000,max_native_calls=48,max_native_seconds=180,
    max_file_reads=256,max_query_seconds=20,max_wall_seconds=900,
    max_model_tokens=120000,max_model_calls=3,max_index_seconds=120,
    max_staged_source_bytes=256000000,max_index_bytes=256000000,max_scip_candidates=1024)
def load(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:
        if isinstance(value,str):f.write(value)
        else:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
def source_lines(text):
    rows=text.split('\n') if text else []
    if rows and rows[-1]=='':rows.pop()
    return [r.removesuffix('\r') for r in rows]
def source_tokens(blocks):
    rows={}
    for b in blocks:
        if b.get('kind','source_lines') not in ('source_lines','implementation_body','exception_branch','body'):continue
        for n,t in enumerate(source_lines(b['content']),b['start_line']):
            key=(b['snapshot'],b.get('path',b.get('file')),n)
            if key in rows:assert rows[key]==t,('conflicting source',key)
            rows[key]=t
    chunks=[];cur=[];last=None
    for (s,p,n),t in sorted(rows.items()):
        if last is not None and (s,p,n)!=(last[0],last[1],last[2]+1):chunks.append('\n'.join(cur));cur=[]
        cur.append(t);last=(s,p,n)
    if cur:chunks.append('\n'.join(cur))
    return sum(oar_core.count_reference_tokens(x) for x in chunks)
def normalize_block(b):
    v={k:b[k] for k in ('snapshot','path','start_line','end_line','content')}
    v['kind']=b.get('kind','source_lines')
    v['provenance']=b.get('provenance','preserved_source_collection')
    v['mechanical_checks']=b.get('mechanical_checks',{'range_supplied':True,'semantic_truth_guaranteed':False})
    v['binding']=b.get('binding',{})
    v['id']='E-'+digest(v)[:16]
    return v
def render_evidence(evidence):
    out=[]
    for b in evidence:
        meta={k:b[k] for k in ('id','snapshot','path','start_line','end_line','kind')}
        checks=b.get('mechanical_checks',{})
        meta['mechanical_checks']={k:checks[k] for k in ('complete_unit','raw_sha256','authoritative_file_sha256','actual_range','range_exact','source_hash_verified','mask_policy','semantic_truth_guaranteed') if k in checks}
        p=b.get('provenance')
        meta['provenance']={k:p[k] for k in ('provider','file_sha256','source_representation') if k in p} if isinstance(p,dict) else p
        meta['binding']=b.get('binding',{})
        if b.get('kind')=='diff_hunk':
            # The public unified diff already appears once in raw_prompt.
            # A citation ID binds only its own path/snapshot and physically
            # displayed rows; min/max does not fill unsupplied hunk gaps.
            rows=sorted(int(n) for n in b['physical_rows']);ranges=[]
            for n in rows:
                if ranges and n==ranges[-1][1]+1:ranges[-1][1]=n
                else:ranges.append([n,n])
            meta.update(content_reference='Proposed diff',supplied_physical_ranges=ranges,
                citation_scope='only listed physical rows for this snapshot/path; no other file or gap is supplied by this ID')
            out.append('<diff_citation '+json.dumps(meta,ensure_ascii=False)+'>')
            continue
        content=b['content']
        if b.get('kind') in ('source_lines','implementation_body','exception_branch','body'):
            content='\n'.join(f'{n}: {t}' for n,t in enumerate(source_lines(content),b['start_line']))
        out.append('<evidence '+json.dumps(meta,ensure_ascii=False)+'>\n'+content+'\n</evidence>')
    return '\n\n'.join(out)
