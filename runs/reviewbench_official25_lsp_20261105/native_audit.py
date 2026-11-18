from pathlib import Path
import collections,json
ROOT=Path(__file__).resolve().parent

def audit():
 manifest=json.loads((ROOT/'upstream/corpus/test/test.json').read_text());rows=[]
 for i,e in enumerate(manifest):
  key=e['nwo'].replace('/','_')+'_'+str(e['pr_number'])+'-'+e['head'][:8];folder=ROOT/'work'/f'w{i%3}'/'out'/key/'agent'
  context=json.loads((folder/'lsp.context.json').read_text());before=json.loads((folder/'ast.context.json').read_text());case=json.loads((folder/'case.json').read_text());native=list((folder/'context/native').rglob('rpc-*.json'))
  calls=[]
  for p in native:
   r=json.loads(p.read_text());method=r['method']
   if method.startswith('textDocument/'):
    value=r.get('result');calls.append(dict(method=method,has_locations=bool(value),failed=bool(r.get('error')),path=str(p)))
  before_ids={x['id']for x in before['evidence']};extra=[x for x in context['evidence']if x['id']not in before_ids];source_kinds={'implementation_body','source_lines','body','exception_branch'}
  dec=json.loads((folder/'review.decoded.json').read_text());conv=json.loads((folder/'conversion_audit.json').read_text());result=json.loads((folder/'complete.json').read_text())
  row=dict(index=i,key=key,nwo=e['nwo'],pr=e['pr_number'],primary_language=e['language'],changed_extensions=dict(collections.Counter(Path(p).suffix for p in case['changed_files'])),ast_source_tokens=before['source_tokens'],lsp_source_tokens=context['source_tokens'],incremental_source_tokens=context['source_tokens']-before['source_tokens'],new_LSP_source_units=sum(x['kind']in source_kinds for x in extra),native_calls=context['cost']['native_calls'],native_seconds=context['cost']['native_seconds'],query_count=context['cost']['query_calls'],observed_RPC_queries=len(calls),successful_location_RPC=sum(c['has_locations']and not c['failed']for c in calls),failed_RPC=sum(c['failed']for c in calls),unresolved_reasons=dict(collections.Counter(x['reason']for x in context['unresolved'])),raw_findings=len(dec['findings']),official_findings=result['findings'],unmapped_findings=len(conv['unmapped']),assessment=dec['assessment'],SCIP_calls=context['cost']['scip_index_reads'],runtime_probe_calls=context['runtime_probe_calls'],seconds=result['seconds'],folder=str(folder),mechanical_source_bound_not_semantic_truth=True)
  rows.append(row)
 return dict(rows=rows,PR_count=len(rows),PRs_with_new_LSP_body=sum(r['new_LSP_source_units']>0 for r in rows),PRs_with_location_results=sum(r['successful_location_RPC']>0 for r in rows),native_RPC_queries=sum(r['observed_RPC_queries']for r in rows),failed_RPC=sum(r['failed_RPC']for r in rows),native_calls=sum(r['native_calls']for r in rows),native_seconds=sum(r['native_seconds']for r in rows),raw_findings=sum(r['raw_findings']for r in rows),official_findings=sum(r['official_findings']for r in rows),unmapped_findings=sum(r['unmapped_findings']for r in rows),unresolved_reasons=dict(sum((collections.Counter(r['unresolved_reasons'])for r in rows),collections.Counter())))
if __name__=='__main__':
 result=audit()
 with (ROOT/'audit/native-coverage.json').open('x')as f:json.dump(result,f,indent=2)
 print(json.dumps({k:v for k,v in result.items()if k!='rows'},indent=2))
