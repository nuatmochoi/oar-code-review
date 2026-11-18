from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent
p=ROOT/'adapter/guarded_selection.py';s=p.read_text();needle='    else:\n        for call in data[\'calls\']:'
branch='''    elif hasattr(data['tree'], 'root_node'):
        def walk(node):
            yield node
            for child in node.named_children:
                yield from walk(child)
        for node in walk(data['tree'].root_node):
            a=node.start_point.row+1
            b=node.end_point.row+(1 if node.end_point.column else 0)
            if not any(a <= n <= b for n in changed):
                continue
            condition=node.child_by_field_name('condition') or node.child_by_field_name('test')
            if condition is not None:
                ca=condition.start_point.row+1;cb=condition.end_point.row+1
                guards.append((ca,cb));spans.append((a,cb))
            elif node.type.endswith('_statement') or node.type in ('lexical_declaration','variable_declarator','assignment_expression','return_expression','let_declaration','property_declaration','field_declaration'):
                spans.append((a,b))
        for call in data['calls']:
            a=call.get('start_line',call['line']);b=call.get('end_line',call['line'])
            if any(a <= n <= b for n in changed):
                spans.append((a,b));calls.append((a,b))
    else:
        for call in data['calls']:'''
assert needle in s;s=s.replace(needle,branch);p.write_text(s)
p=ROOT/'adapter/entrypoint.py';s=p.read_text().replace('import hashlib,io,json,os,subprocess,sys,tarfile,time,traceback,urllib.request','import base64,copy,hashlib,io,json,os,subprocess,sys,tarfile,time,traceback,urllib.request,zlib')
start=s.index(' tool=ContextCollector(case,ROOT/\'context\'');end=s.index(' raw,schema,evidence,catalog=',start)
new=''' prefix=json.loads(zlib.decompress(base64.b64decode(os.environ['REVIEW_PREFIX_ZLIB'])))
 assert prefix['case_id']==case['id']
 # Verify every physically supplied source row against the new fixed snapshot.
 from warning_tracker import physical_rows
 from context48 import digest as source_digest
 actual_pool=source_digest({side:{k:v for k,v in spec.items()if k in ('commit','files','external_files','allowed_external_roots','native_profiles')}for side,spec in case['snapshots'].items()})
 assert actual_pool==prefix['source_pool_sha256'],'Public source pool changed'
 checked={};bytes_checked=0
 for e in prefix['evidence']:
  physical,errors=physical_rows(e)
  assert not errors
  if not physical:continue
  key=e['snapshot'],e['path']
  if key not in checked:
   spec=case['snapshots'][e['snapshot']]
   if e['path'] in spec['files']:
    path=Path(spec['root'])/e['path'];expected=spec['files'][e['path']]
   else:
    ext=prefix['external_source_bindings'][e['snapshot']][e['path']];path=Path(ext['absolute_source']);expected=ext['sha256']
   data=path.read_bytes();assert hashlib.sha256(data).hexdigest()==expected;checked[key]=data.decode().splitlines();bytes_checked+=len(data)
  rows=checked[key]
  assert all(1<=n<=len(rows)and rows[n-1]==text for n,text in physical.items()),'Protected source rows changed'
 write_new(ROOT/'protected.prefix.json',prefix)
 write_new(ROOT/'prefix.validation.json',dict(source_pool_identical=True,all_physical_rows_identical=True,files_checked=len(checked),bytes_checked=bytes_checked,prior_review_or_gold_loaded=False))
 caps={**BUDGETS,'max_source_tokens':12000,'lsp_source_tokens':12000,'max_native_calls':48,'max_file_reads':256}
 tool=ContextCollector(case,ROOT/'context',caps,initial_state=prefix)
 protected={e['id']for e in prefix['evidence']};ast_context=copy.deepcopy(prefix)
 ast_context['evidence']=[e for e in prefix['evidence']if e['id']in set(prefix['stages']['AST']['evidence_ids'])]
 ast_context['source_tokens']=prefix['stages']['AST']['source_tokens']
 from guarded_selection import inventory,queue
 records=[];deferred=[];inv=[];errors=[];collection_start=time.monotonic()
 prefix_wall=sum(stage.get('seconds',0.)for stage in prefix['stages'].values())
 tool.started-=prefix_wall
 try:
  inv,errors=inventory(tool);requests=queue(inv,'GUARDED')
  for index,row in enumerate(requests):
   cost=tool.stats
   if cost['native_calls']>=46 or cost['file_reads']>=248 or source_tokens(tool.evidence)>=11936 or time.monotonic()-tool.started>=880:
    deferred=requests[index:];break
   answer=tool.query(row['question']);records.append(dict(**row,answer=answer))
 finally:tool.close()
 context=tool.export();assert protected <= {e['id']for e in context['evidence']}
 context.update(cap=12000,protected_initial_ids=sorted(protected),protected_initial_ids_retained=True,seed_inventory=inv,seed_inventory_errors=errors,request_records=records,deferred_requests=deferred,priority_is_not_a_filter=True,additional_collection_seconds=time.monotonic()-collection_start,reused_prefix_cost=prefix['cost'],physical_incremental_cost={k:context['cost'].get(k,0)-prefix['cost'].get(k,0)for k in context['cost']},method='protected_LSP6k_plus_priority2_breadth1_12k')
 write_new(ROOT/'ast.context.json',ast_context);write_new(ROOT/'lsp.context.json',context)
'''
s=s[:start]+new+s[end:];p.write_text(s)
p=ROOT/'adapter/Dockerfile.final';p.write_text('FROM research-reviewbench-lsp:20261105-final\nCOPY vendor /opt/agent/vendor\nCOPY *.py /opt/agent/\n')
p=ROOT/'budget_proxy.py';s=p.read_text().replace('CAP=70.','CAP=50.').replace("8789","8791");p.write_text(s)
p=ROOT/'test_budget.py';s=p.read_text();p.write_text(s)
for name in ['freeze_run.py','run_official.py']:
 p=ROOT/name;s=p.read_text().replace('research-reviewbench-lsp:20261105-final','research-reviewbench-guarded:20261105').replace('hard_total_upper_usd=70','hard_total_upper_usd=50');p.write_text(s)
p=ROOT/'freeze_run.py';s=p.read_text().replace("paths+=list((ROOT/'adapter')", "paths+=list((ROOT/'prefixes').glob('*.json'))\n paths+=list((ROOT/'adapter')",1);p.write_text(s)
p=ROOT/'run_official.py';s=p.read_text().replace('import concurrent.futures,json,os,subprocess,time,signal','import base64,concurrent.futures,json,os,subprocess,time,signal,zlib').replace('8789','8791')
s=s.replace("  command=[", "  env['REVIEW_PREFIX_ZLIB']=base64.b64encode(zlib.compress((ROOT/'prefixes'/f'{index:02d}.json').read_bytes())).decode()\n  command=[")
s=s.replace("'-e','REVIEW_BROKER_TOKEN']", "'-e','REVIEW_BROKER_TOKEN','-e','REVIEW_PREFIX_ZLIB']");p.write_text(s)
p=ROOT/'run_judge.py';s=p.read_text().replace("OPENAI_API_KEY='budget-proxy-dummy-no-account-key'", "OPENAI_API_KEY=(ROOT/'broker.token').read_text().strip()");p.write_text(s)
p=ROOT/'judge-config/models.json';s=p.read_text().replace('8789','8791');p.write_text(s)
