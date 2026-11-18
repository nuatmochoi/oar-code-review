"""Run inside the exact frozen multilingual image; no model or gold access."""
import copy,json,os,time
from pathlib import Path
from entrypoint import snapshot,command
from multilingual import ContextCollector
from common import BUDGETS,source_tokens,write_new
ROOT=Path('/work/out/agent')
started=time.monotonic()
x=json.loads((ROOT/'input.json').read_text());packet=x['packet'];pr=packet['pr'];case=copy.deepcopy(packet['case']);repo=Path('/work/repo')
base=command(['git','-C',str(repo),'merge-base',pr['base'],pr['head']]).decode().strip()
assert base==case['snapshots']['BASE']['commit']
for side,commit in [('HEAD',pr['head']),('BASE',base)]:case['snapshots'][side]=snapshot(repo,commit,None if side=='HEAD'else ROOT/'base')
prefix=packet['prefix']
for side in ('HEAD','BASE'):assert case['snapshots'][side]['files']==packet['case']['snapshots'][side]['files']
caps={**BUDGETS,'max_source_tokens':x['source_cap'],'lsp_source_tokens':x['source_cap'],'max_native_calls':64,'max_native_seconds':240,'max_file_reads':256,'max_wall_seconds':900}
tool=ContextCollector(case,ROOT/'queries',caps,initial_state=prefix);records=[];deferred=[]
tool.started-=sum(s.get('seconds',0)for s in prefix.get('stages',{}).values())
protected={e['id']for e in prefix['evidence']};before={e['id']for e in prefix['evidence']}
try:
 for index,q in enumerate(x['questions']):
  if tool.stats['native_calls']>=62 or tool.stats['file_reads']>=248 or source_tokens(tool.evidence)>=x['source_cap']-64:
   deferred=x['questions'][index:];break
  request={k:q[k]for k in ('id','snapshot','file','line','column','symbol','query','kind','reason')};request['claim_id']=q['unit_id']
  answer=tool.query(request)
  records.append(dict(question=q,answer=answer))
finally:tool.close()
context=tool.export();assert protected<={e['id']for e in context['evidence']}
new={e['id']for e in context['evidence']}-before
ledger=dict(queries=[dict(question=r['question'],selected_evidence_ids=r['answer'].get('selected_evidence_ids',[]),already_available_evidence_ids=r['answer'].get('already_available_evidence_ids',[]),unresolved=r['answer'].get('unresolved',[]),callee_binding=r['answer'].get('callee_binding'),receipt=r['answer'].get('query_receipt'))for r in records],deferred=deferred,new_evidence_ids=sorted(new),semantic_truth_guaranteed=False,runtime_probe_calls=0)
write_new(ROOT/'context.json',context);write_new(ROOT/'query_ledger.json',ledger);write_new(ROOT/'raw_queries.json',records);write_new(ROOT/'complete.json',dict(complete=True,queries=len(records),new_ids=len(new),source_tokens=source_tokens(context['evidence']),cost=context['cost'],incremental_cost={k:context['cost'].get(k,0)-prefix['cost'].get(k,0)for k in context['cost']},collection_seconds=time.monotonic()-started,prefix_preserved=True))
