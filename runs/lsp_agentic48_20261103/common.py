"""Prospective shared limits and source accounting; never reads evaluator gold."""
from pathlib import Path
import hashlib,json
import oar_core
ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[1]
MODEL='gpt-5.6-sol'
REPEATS=2
ARMS=('FIXED','EXTRA','AGENTIC')
PREVIOUS=PROJECT/'runs/reviewbench48_ablation_20261103'
ARM_LABELS={'FIXED':'AST+LSP 고정 수집·리뷰 1회','EXTRA':'같은 LSP 본문·추가 검토','AGENTIC':'현재 필수조건 기반 LSP agentic 조회'}
BUDGETS=dict(max_source_tokens=12000,max_native_calls=48,max_native_seconds=180,
 max_file_reads=256,max_query_seconds=20,max_wall_seconds=900,
 max_model_tokens=120000,max_model_calls=3,max_index_seconds=180,
 max_index_bytes=256000000,max_scip_candidates=24,
 ast_source_tokens=3000,lsp_source_tokens=6000,scip_source_tokens=9000)
BUDGETS.update(max_index_seconds=0,max_index_bytes=0,max_scip_candidates=0,scip_source_tokens=0)
WORKERS=4
MIN_START_INTERVAL=3
def load(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def write_new(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x',encoding='utf-8')as f:
  if isinstance(v,str):f.write(v)
  else:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')
def source_lines(text):
 rows=text.split('\n')if text else[]
 if rows and rows[-1]=='':rows.pop()
 return [x.removesuffix('\r')for x in rows]
def source_tokens(blocks):
 rows={}
 for b in blocks:
  if b.get('kind','source_lines')not in('source_lines','implementation_body','exception_branch','body'):continue
  for n,t in enumerate(source_lines(b['content']),b['start_line']):
   key=b['snapshot'],b.get('path',b.get('file')),n
   if key in rows:assert rows[key]==t,('Source representation conflict',key)
   rows[key]=t
 chunks=[];cur=[];last=None
 for(s,p,n),t in sorted(rows.items()):
  if last is not None and(s,p,n)!=(last[0],last[1],last[2]+1):chunks.append('\n'.join(cur));cur=[]
  cur.append(t);last=s,p,n
 if cur:chunks.append('\n'.join(cur))
 return sum(oar_core.count_reference_tokens(x)for x in chunks)
def normalize_block(b):
 v={k:b[k]for k in('snapshot','path','start_line','end_line','content')}
 v.update(kind=b.get('kind','source_lines'),provenance=b.get('provenance','read_lines'),mechanical_checks=b.get('mechanical_checks',{'semantic_truth_guaranteed':False}),binding=b.get('binding',{}))
 v['id']='E-'+digest(v)[:16];return v
def compact_binding(binding):
 keys=('status','binding_status','provider','version','origin','resolution_mode','runtime_dispatch_confirmed','runtime_dispatch','environment_assumption','requested_callee','role')
 return {k:binding[k]for k in keys if k in binding}
def render_evidence(evidence):
 """Never repeats full PR diff per file/side or huge backend metadata in stdin.

 DIFF content occurs once in the public input. Its handle table binds references
 to that exact diff's visible physical rows. Full row maps remain in artifacts.
 """
 parts=[]
 for b in evidence:
  meta={k:b[k]for k in('id','snapshot','path','start_line','end_line','kind')}
  if b['kind']=='diff_hunk':
   meta['visible_rows_only_not_every_line_in_enclosing_span']=True
   parts.append('<diff-citation '+json.dumps(meta,ensure_ascii=False,separators=(',',':'))+'/>');continue
  ck=b.get('mechanical_checks',{});meta['complete_unit']=ck.get('complete_unit',False)
  binding=compact_binding(b.get('binding',{}))
  if binding:meta['binding']=binding
  content=b.get('content','')
  if b['kind']in('source_lines','implementation_body','exception_branch','body'):
   content='\n'.join(f'{n}: {t}'for n,t in enumerate(source_lines(content),b['start_line']))
  parts.append('<evidence '+json.dumps(meta,ensure_ascii=False,separators=(',',':'))+'>\n'+content+'\n</evidence>')
 return '\n\n'.join(parts)
