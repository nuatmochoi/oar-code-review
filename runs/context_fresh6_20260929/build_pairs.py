"""Construct explicitly derived component pairs before any evaluation model calls."""
import ast, difflib, hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from ace_review.paired_benchmark import validate_pair
R=Path(__file__).resolve().parent
meta={s['githubPrUrl'].split('/')[-1]:s for s in json.loads((R/'candidate_metadata.json').read_text())}
LF='src/backend/base/langflow/'
def method(source,name):
 return [n for n in ast.walk(ast.parse(source)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name][-1]
def replace_once(s,a,b):
 assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
config=[
 ('F01','1482','browser_use/browser/browser.py','_setup_builtin_browser','Browser startup must reach the launch boundary with the deployed BrowserConfig. The only entry is deployment.launch; headless builtin Chromium, no existing event loop and no custom browser binary. Network probe reports no listener; browser launch is an inert boundary.', 'A None remote-debugging port from deployment reaches socket.connect_ex without validation after replacing the previous integer constant, raising TypeError before browser launch.'),
 ('F02','3172','browser_use/llm/google/chat.py','ainvoke','Only deployment.complete is used, with plain-text messages and no structured output. Explicit thinking_budget must be preserved in the outgoing provider configuration. API transport and message encoding are boundary fixtures; the provider is not contacted.', 'ainvoke replaces the configured thinking budget with -1, so a deployment requesting a finite budget emits the wrong API configuration.'),
 ('F03','6553','api/apps/api_app.py','document_rm','Only deployment.request_body is submitted to the document deletion endpoint. An existing document ID without a supplied document name must survive ID collection. Authentication succeeds; witness scope ends after ID collection before destructive document/storage operations.', 'The new bulk name lookup returns None for empty results. Membership testing an explicit document ID against this None raises TypeError, preventing deletion from reaching the collected-ID boundary.'),
 ('F04','7151','rag/utils/infinity_conn.py','update','Only deployment.apply_update is called. The table has id and pagerank_fea columns; pagerank_fea default is 1.0. remove is an operation directive and is not a stored column. Scope ends at the database update payload boundary; the external Infinity database is not executed.', 'For a string remove directive, update retains the remove key and submits it as a database column along with the reset value; moving deletion into the dictionary branch breaks this deployed operation.'),
 ('F05','6044',LF+'graph/utils.py','log_vertex_build','Only deployment.record_build is called. Vertex-build storage is enabled; the data payload must preserve all supplied artifact items for this deployment. Scope includes source serialization and the constructed storage record, with persistence replaced by an inert capture boundary.', 'Switching log_vertex_build to serialize applies MAX_ITEMS_LENGTH to list data; the configured finite limit replaces the tail with a truncation marker and loses original items.'),
 ('F06','6652',LF+'components/processing/parser.py','parse_combined_text','Only deployment.render is called with a Data object. Its template may reference keys present in Data.data, as advertised by the component. The witness checks template rendering; component UI/framework construction is out of scope.', 'The Data branch supplies only text to template.format, so the deployed template referring to the existing name field raises KeyError rather than rendering the Data record.')]
manifest=[]
for cid,pr,target,symbol,scope,mechanism in config:
 repo=meta[pr]['githubPrUrl'].split('github.com/')[1].split('/pull/')[0]
 snap=R/'snapshots'/pr
 roots={'browser-use/browser-use':['browser_use'],'infiniflow/ragflow':['api','rag','graphrag','agent'],'langflow-ai/langflow':['src/backend/base/langflow']}[repo]
 sources={};skipped=[]
 for top in roots:
  for p in sorted((snap/top).rglob('*.py')):
   rel=p.relative_to(snap).as_posix()
   if any(x in ('tests','test','__pycache__') for x in p.relative_to(snap).parts):continue
   try:s=p.read_text();ast.parse(s)
   except (UnicodeError,SyntaxError) as e:skipped.append({'file':rel,'reason':str(e)});continue
   sources[rel]=s if s.endswith('\n') else s+'\n'
 assert target in sources
 head=sources[target];derivation=[]
 deploy='deployment.py'; extra_companions=[]
 if cid=='F01':
  base=replace_once(head,"s.connect_ex(('localhost', self.config.chrome_remote_debugging_port))", "s.connect_ex(('localhost', 9222))")
  deployments={w:"from browser_use.browser.browser import Browser, BrowserConfig\n\ndef config():\n    return BrowserConfig(headless=True, browser_class='chromium', chrome_remote_debugging_port="+v+")\n\nasync def launch(playwright):\n    browser = Browser(config=config())\n    return await browser._setup_builtin_browser(playwright)\n" for w,v in [('safe','9222'),('unsafe','None')]}
  derivation=['Base restores only the pre-PR integer port at connect_ex; other head changes retained.','Deployment fixes all non-port choices equally and varies configured port.']
 elif cid=='F02':
  base=replace_once(head,"{'thinking_budget': -1}","{'thinking_budget': self.thinking_budget}")
  deployments={w:"from browser_use.llm.google.chat import ChatGoogle\n\ndef model():\n    return ChatGoogle(model='gemini-2.5-flash', thinking_budget="+v+")\n\nasync def complete(messages):\n    return await model().ainvoke(messages)\n" for w,v in [('safe','-1'),('unsafe','128')]}
  derivation=['Base restores the pre-PR budget expression only.','Deployment requests -1 or 128; model/service otherwise identical.']
 elif cid=='F03':
  base=replace_once(head,'doc_ids = DocumentService.get_doc_ids_by_doc_names(req.get("doc_names", []))','doc_ids = [DocumentService.get_doc_id_by_doc_name(doc_name) for doc_name in req.get("doc_names", [])]')
  companion='api/db/services/document_service.py';extra_companions=[companion]
  deployments={w:"from api.apps.api_app import document_rm\n\ndef request_body():\n    return {'doc_names': [], 'doc_ids': ['doc-1']}\n\ndef handle():\n    return document_rm()\n" for w in ('safe','unsafe')}
  service=sources[companion];n=method(service,'get_doc_ids_by_doc_names');block=''.join(service.splitlines(True)[n.lineno-1:n.end_lineno])
  safe_service=replace_once(service,block,replace_once(block,'if not doc_ids:\n            return\n','if not doc_ids:\n            return []\n'))
  derivation=['Base restores only the old caller name-lookup expression; bulk helper is available in both revisions.','Safe companion fixes the bulk helper empty-result return to []; unsafe retains upstream None.']
 elif cid=='F04':
  base=replace_once(head,'                    del newValue[k]\n            else:', '                del newValue[k]\n            else:')
  deployments={w:"from rag.utils.infinity_conn import InfinityConnection\n\ndef update_payload():\n    return "+v+"\n\ndef apply_update(connection):\n    return connection.update({'id': 'doc-1'}, update_payload(), 'documents', 'kb-1')\n" for w,v in [('safe',"{'pagerank_fea': 1.0}"),('unsafe',"{'remove': 'pagerank_fea'}")]}
  derivation=['Base moves the existing directive deletion out of the dict-only branch, preserving other upstream head code. This repaired base is constructed, not the entire upstream parent.','Deployment varies ordinary field update versus string remove directive.']
 elif cid=='F05':
  old=(R/'screen_v2/langflow-ai__langflow/6044/base'/target).read_text();n=method(old,'serialize_field')
  helper=''.join(old.splitlines(True)[n.lineno-1:n.end_lineno])
  head+='\n\n'+helper
  # Keep the old helper callable in BOTH revisions. Its dependencies are already available via head imports except Document/V1 alias.
  head+='\nfrom langchain_core.documents import Document\nfrom pydantic.v1 import BaseModel as V1BaseModel\n'
  base=replace_once(replace_once(head,'data=serialize(data),','data=serialize_field(data),'),'artifacts=serialize(artifacts) if artifacts else None,','artifacts=serialize_field(artifacts) if artifacts else None,')
  companion=LF+'serialization/constants.py';extra_companions=[companion]
  safe_constants=replace_once(sources[companion],'MAX_ITEMS_LENGTH = 1000','MAX_ITEMS_LENGTH = None')
  deployments={w:"from langflow.graph.utils import log_vertex_build\n\ndef payload():\n    return ['item-' + str(i) for i in range(1002)]\n\nasync def record_build():\n    return await log_vertex_build(flow_id='flow-1', vertex_id='vertex-1', valid=True, params=None, data=payload())\n" for w in ('safe','unsafe')}
  derivation=['Reintroduced pre-PR serialize_field helper identically into both target revisions, with required imports; base switches only the two calls back to this helper.','Safe companion has no list item limit; unsafe keeps upstream limit 1000. Both deployments carry the same 1002 strings.']
 else:
  base=replace_once(head,'formatted_text = template.format(text=data.get_text())','formatted_text = template.format(**data.data)')
  deployments={w:"from langflow.schema import Data\nfrom langflow.components.processing.parser import ParserComponent\n\ndef arguments():\n    return {'input_data': Data(data={'text': 'hello', 'name': 'Ada'}), 'template': "+repr(t)+", 'sep': '\\n', 'stringify': False}\n\ndef render():\n    return ParserComponent(**arguments()).parse_combined_text()\n" for w,t in [('safe','{text}'),('unsafe','{name}')]}
  derivation=['Base is a constructed repaired predecessor exposing all Data keys; component did not exist in the upstream parent.','Deployment varies template key text versus name, both present in the same Data record.']
 sources[target]=head
 pairroot=R/'pairs'/cid;pairroot.mkdir(parents=True,exist_ok=False)
 diff=''.join(difflib.unified_diff(base.splitlines(True),head.splitlines(True),fromfile='a/'+target,tofile='b/'+target))
 (pairroot/'target.diff').write_text(diff)
 for world in ('safe','unsafe'):
  for rev in ('base','head'):
   content=dict(sources);content[target]=base if rev=='base' else head;content[deploy]=deployments[world]
   if cid=='F03' and world=='safe':content[companion]=safe_service
   if cid=='F05' and world=='safe':content[companion]=safe_constants
   for name,text in content.items():
    ast.parse(text)
    p=pairroot/'snapshots'/f'{world}_{rev}'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
 target_node=method(head,symbol)
 companions=extra_companions or [deploy]
 companion=companions[0]
 comp_text=(pairroot/'snapshots/unsafe_head'/companion).read_text()
 oracle=[{'id':'target','role':'changed_consumer','file':target,'start_line':target_node.lineno,'end_line':target_node.end_lineno,'symbols':[symbol]},
         {'id':'companion','role':'world_condition','file':companion,'start_line':1,'end_line':len(comp_text.splitlines()),'symbols':[]}]
 pair={'schema_version':1,'family_id':cid,'repository':repo,'language':'Python','origin':'derived','split':'validation','stratum':'static_decidable',
  'target':{'changed_files':[target],'actionable_span':{'file':target,'start_line':target_node.lineno,'end_line':target_node.end_lineno}},
  'worlds':{w:{'base_snapshot':f'snapshots/{w}_base','head_snapshot':f'snapshots/{w}_head','diff':'target.diff','expected':{'base':'pass','head':'target_failure' if w=='unsafe' else 'pass'}} for w in ('safe','unsafe')},
  'companion_paths':companions,
  'gold':{'mechanism':mechanism,'required_evidence_nodes':['target','companion'],'oracle_nodes':oracle,'oracle_edges':[{'source':'companion','target':'target','type':'semantic_dependency'}],'oracle_paths':[{'nodes':['companion','target'],'edge_types':['semantic_dependency']}], 'oracle_note':'Author-defined semantic dependency, not an automatically resolved call edge or execution proof.'},
  'witness':{'argv':['{python}','-B','{pair_root}/../../witness.py',cid,'{snapshot}'],'timeout_seconds':15,'exit_codes':{'pass':0,'target_failure':10}},
  'scope':scope,'source':{'pr_url':meta[pr]['githubPrUrl'],'upstream_head_commit':meta[pr]['target_commit'],'upstream_base_commit':meta[pr]['source_commit'],'derivation':derivation},'corpus':{'python_files':len(sources)+1,'excluded_parser_files':skipped,'production_roots':roots}}
 (pairroot/'pair.json').write_text(json.dumps(pair,ensure_ascii=False,indent=2))
 audit=validate_pair(pair,pairroot);(pairroot/'static_audit.json').write_text(json.dumps(audit,indent=2))
 assert audit['eligible'],audit['errors']
 manifest.append({'case_id':cid,'repository':repo,'pr':pr,'pair':str(pairroot.relative_to(R)/'pair.json'),'scope':scope,'corpus_files':len(sources)+1,'excluded_parser_files':skipped,'static_gate':audit['eligible']})
 print(json.dumps(manifest[-1]),flush=True)
(R/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
