"""Execute inspected source functions, with explicit non-target service boundaries.

This program does not read gold labels or world names. Exit 10 denotes a specific
observed contract failure; incidental exceptions remain exit 1 (invalid witness).
"""
from __future__ import annotations
import ast, asyncio, copy, json, logging, re, socket, sys, types
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID
from collections.abc import AsyncIterator, Generator, Iterator

CID, SNAP=sys.argv[1],Path(sys.argv[2])
NS=types.SimpleNamespace
spans=[]
def load(file,name,env):
 source=(SNAP/file).read_text();tree=ast.parse(source)
 nodes=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name]
 if not nodes:raise ValueError((file,name))
 node=copy.deepcopy(nodes[-1]);node.decorator_list=[]
 spans.append({'file':file,'symbol':name,'start':node.lineno,'end':node.end_lineno})
 module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])
 ast.fix_missing_locations(module);exec(compile(module,file,'exec'),env)
 return env[name]
def result(failed,**facts):
 print(json.dumps({'status':'target_failure' if failed else 'pass','case_id':CID,'facts':facts,'executed_source_spans':spans}))
 sys.exit(10 if failed else 0)
logger=logging.getLogger('witness');logger.addHandler(logging.NullHandler())

if CID=='F01':
 env={'socket':socket,'IN_DOCKER':False}
 exec((SNAP/'browser_use/browser/chrome.py').read_text(),env)
 cfg=load('deployment.py','config',{'BrowserConfig':NS})()
 cfg.browser_binary_path=None;cfg.disable_security=False;cfg.deterministic_rendering=False;cfg.extra_browser_args=[];cfg.proxy=None
 launched=[]
 async def launch(**kw):launched.append(kw);return 'launched'
 subject=NS(config=cfg)
 func=load('browser_use/browser/browser.py','_setup_builtin_browser',env)
 try:
  ret=asyncio.run(func(subject,NS(chromium=NS(launch=launch))))
 except TypeError as e:
  # Validate actual argument-parser cause, not a generic exception.
  if cfg.chrome_remote_debugging_port is None and 'integer' in str(e):result(True,error_type=type(e).__name__,error=str(e),launch_calls=len(launched),configured_port=None)
  raise
 assert ret=='launched' and len(launched)==1
 result(False,launch_calls=len(launched),configured_port=cfg.chrome_remote_debugging_port)

elif CID=='F02':
 cfg=load('deployment.py','model',{'ChatGoogle':NS})()
 requests=[]
 async def generate_content(**kw):requests.append(kw);return NS(text='ok',usage_metadata=None)
 class ProviderError(Exception):
  def __init__(self,**kw):super().__init__(kw)
 env={'GoogleMessageSerializer':NS(serialize_messages=lambda *a,**k:([],None)), 'ChatInvokeCompletion':lambda **kw:kw,'ModelProviderError':ProviderError,'time':__import__('time'),'json':json,'asyncio':asyncio}
 fn=load('browser_use/llm/google/chat.py','ainvoke',env)
 cfg.include_system_in_user=False;cfg.config=None;cfg.temperature=None;cfg.top_p=None;cfg.seed=None;cfg.max_output_tokens=32;cfg.logger=logger
 cfg.get_client=lambda:NS(aio=NS(models=NS(generate_content=generate_content)))
 cfg._get_usage=lambda response:None
 out=asyncio.run(fn(cfg,[]));assert out['completion']=='ok' and len(requests)==1
 requested=cfg.thinking_budget;sent=requests[0]['config']['thinking_config']['thinking_budget']
 result(sent!=requested,requested_budget=requested,sent_budget=sent,api_calls_captured=len(requests))

elif CID=='F03':
 class Field:
  def in_(self,values):return tuple(values)
 class EmptyQuery:
  def where(self,*a):return self
  def dicts(self):return []
 model=NS(id=Field(),name=Field(),select=lambda *a:EmptyQuery())
 service=NS(model=model)
 lookup=load('api/db/services/document_service.py','get_doc_ids_by_doc_names',{})
 service.get_doc_ids_by_doc_names=lambda names:lookup(service,names)
 service.get_doc_id_by_doc_name=lambda name:(_ for _ in ()).throw(AssertionError('empty names must not invoke scalar lookup'))
 class Collected(Exception):pass
 def capture(ids):raise Collected(list(ids))
 service.get_by_ids=capture
 body=load('deployment.py','request_body',{})()
 env={'request':NS(json=body,headers={'Authorization':'Bearer local'}), 'APIToken':NS(query=lambda **kw:[NS(tenant_id='tenant')]),'DocumentService':service,
  'FileService':NS(get_root_folder=lambda tenant:{'id':'root'},init_knowledgebase_docs=lambda *a:None),
  'server_error_response':lambda e:{'error_type':type(e).__name__,'message':str(e)},'get_json_result':lambda **kw:kw}
 fn=load('api/apps/api_app.py','document_rm',env)
 try:out=fn()
 except Collected as e:
  assert e.args[0]==['doc-1'];result(False,collected_ids=e.args[0],empty_lookup_result=service.get_doc_ids_by_doc_names([]),boundary='before destructive deletion')
 assert out['error_type']=='TypeError' and 'NoneType' in out['message'],out
 result(True,error=out,empty_lookup_result=service.get_doc_ids_by_doc_names([]),explicit_ids=body['doc_ids'])

elif CID=='F04':
 payload=load('deployment.py','update_payload',{})()
 captured=[]
 table=NS(show_columns=lambda:NS(rows=lambda:[('id','varchar','',None),('pagerank_fea','float',1.0,None)]),update=lambda filt,values:captured.append(dict(values)))
 conn=NS(get_database=lambda name:NS(get_table=lambda name:table))
 subject=NS(dbName='local',connPool=NS(get_conn=lambda:conn,release_conn=lambda c:None))
 env={'re':re,'json':json,'logger':logger,'equivalent_condition_to_str':lambda *a:'id = local'}
 load('rag/utils/infinity_conn.py','field_keyword',env)
 fn=load('rag/utils/infinity_conn.py','update',env)
 assert fn(subject,{'id':'doc-1'},copy.deepcopy(payload),'documents','kb-1') is True
 assert len(captured)==1
 invalid=sorted(set(captured[0])-{'id','pagerank_fea'})
 assert not invalid or invalid==['remove']
 result(bool(invalid),input_payload=payload,submitted_payload=captured[0],invalid_columns=invalid,boundary='schema-checked database update payload; no Infinity engine')

elif CID=='F05':
 class Document:pass
 class BaseModel:pass
 class BaseModelV1:pass
 class Frame:pass
 class Series:pass
 env=dict(Document=Document,BaseModel=BaseModel,BaseModelV1=BaseModelV1,V1BaseModel=BaseModelV1,pd=NS(DataFrame=Frame,Series=Series),AsyncIterator=AsyncIterator,Generator=Generator,Iterator=Iterator,datetime=datetime,timezone=timezone,Decimal=Decimal,UUID=UUID,logger=logger)
 prefix='src/backend/base/langflow/'
 exec((SNAP/(prefix+'serialization/constants.py')).read_text(),env)
 source=(SNAP/(prefix+'serialization/serialization.py')).read_text()
 for n in ast.parse(source).body:
  if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):load(prefix+'serialization/serialization.py',n.name,env)
 load(prefix+'graph/utils.py','serialize_field',env)
 captured=[]
 def record(**kw):captured.append(kw);return NS(**kw)
 class Session:
  async def __aenter__(self):return None
  async def __aexit__(self,*a):pass
 async def insert(*a):return NS(build_id='local')
 env.update(get_settings_service=lambda:NS(settings=NS(vertex_builds_storage_enabled=True)),VertexBuildBase=record,session_getter=lambda *a:Session(),get_db_service=lambda:None,crud_log_vertex_build=insert)
 fn=load(prefix+'graph/utils.py','log_vertex_build',env)
 payload=load('deployment.py','payload',{})()
 asyncio.run(fn(flow_id='flow-1',vertex_id='vertex-1',valid=True,params=None,data=payload))
 assert len(captured)==1
 actual=captured[0]['data'];assert isinstance(actual,list)
 result(actual!=payload,original_items=len(payload),stored_items=len(actual),original_tail=payload[-2:],stored_tail=actual[-2:],configured_limit=env['MAX_ITEMS_LENGTH'])

elif CID=='F06':
 prefix='src/backend/base/langflow/'
 class Data:
  def __init__(self,data):self.data=data;self.text_key='text';self.default_value=''
 Data.get_text=load(prefix+'schema/data.py','get_text',{})
 class Frame:pass
 cfg=load('deployment.py','arguments',{'Data':Data})()
 subject=NS(**cfg)
 env={'Data':Data,'DataFrame':Frame,'Message':lambda **kw:NS(**kw)}
 clean=load(prefix+'components/processing/parser.py','_clean_args',env)
 subject._clean_args=lambda:clean(subject)
 fn=load(prefix+'components/processing/parser.py','parse_combined_text',env)
 expected=subject.template.format(**subject.input_data.data)
 try:out=fn(subject)
 except KeyError as e:
  assert e.args[0]=='name' and 'name' in subject.input_data.data
  result(True,error_type='KeyError',missing_format_key=e.args[0],available_data_keys=list(subject.input_data.data),template=subject.template,expected=expected)
 assert out.text==expected
 result(False,rendered=out.text,template=subject.template,available_data_keys=list(subject.input_data.data))
elif CID=='F07':
 cfg=load('deployment.py','arguments',{})()
 fn=load('comfy_extras/nodes_string.py','execute',{'re':re})
 expected='\n'.join(m.group(1) or '' for m in re.finditer(cfg['regex_pattern'],cfg['string']))
 try:actual=fn(None,**cfg)
 except TypeError as e:
  assert 'NoneType' in str(e) and any(m.group(1) is None for m in re.finditer(cfg['regex_pattern'],cfg['string']))
  result(True,error_type='TypeError',error=str(e),expected=expected,input=cfg['string'])
 assert actual==(expected,)
 result(False,rendered=actual[0],input=cfg['string'])
elif CID=='F08':
 env={}
 module=ast.parse((SNAP/'middleware/cache_middleware.py').read_text())
 for node in module.body:
  if isinstance(node,(ast.Assign,ast.AnnAssign)):
   exec(compile(ast.Module(body=[node],type_ignores=[]),'middleware/cache_middleware.py','exec'),env)
 fn=load('middleware/cache_middleware.py','cache_control',env)
 producer=load('deployment.py','missing_image',{'web':NS(Response=lambda **kw:NS(**kw))})
 response=asyncio.run(fn(NS(path='/generated/pending.png'),producer))
 header=response.headers.get('Cache-Control','')
 failed='max-age=3600' in header
 assert failed or header=='no-cache'
 result(failed,status=response.status,cache_control=header,contract='missing generated image response requires revalidation')
else:raise ValueError(CID)
