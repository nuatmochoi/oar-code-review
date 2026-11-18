"""Validate actual repository entry bodies; explicit service/config boundaries only."""
from __future__ import annotations
import ast, asyncio, copy, hashlib, json, logging, os, re, tempfile
from pathlib import Path
from types import SimpleNamespace as NS
R=Path(__file__).resolve().parent; ROOT=R.parents[1]
spans=[]; observations=[]
def record(file,source,node,role):
    spans.append(dict(file=file,start=node.lineno,end=node.end_lineno,role=role,source_sha256=hashlib.sha256(source.encode()).hexdigest(),excerpt='\n'.join(source.splitlines()[node.lineno-1:node.end_lineno])))
def function(file,source,name,env):
    nodes=[n for n in ast.walk(ast.parse(source)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name]
    assert len(nodes)==1,(file,name,len(nodes))
    node=nodes[0];record(file,source,node,'executed function body; decorators excluded')
    route=None
    for deco in node.decorator_list:
        if isinstance(deco,ast.Call) and isinstance(deco.func,ast.Attribute) and deco.func.attr in ('get','route') and deco.args and isinstance(deco.args[0],ast.Constant):route=deco.args[0].value
    node=copy.deepcopy(node);node.decorator_list=[]
    compile_nodes([node],file,env)
    return env[name],route

def compile_nodes(nodes,file,env):
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*copy.deepcopy(nodes)],type_ignores=[])
    ast.fix_missing_locations(module);exec(compile(module,file,'exec'),env)

def case(cid):return json.loads((ROOT/f'runs/context_fresh6_20260929/experiment_v1/cases/{cid}.json').read_text())['worlds']['a']['versions']
class Web:
    @staticmethod
    def Response(status=200,headers=None):return NS(status=status,headers={} if headers is None else dict(headers))
# Actual nested route, native missing-file check, actual middleware. No HTTP server/browser.
for revision,sources in case('F08').items():
    env={'web':Web,'os':os}
    src=sources['middleware/cache_middleware.py'];assigns=[n for n in ast.parse(src).body if isinstance(n,(ast.Assign,ast.AnnAssign))]
    compile_nodes(assigns,'middleware/cache_middleware.py',env)
    middleware,_=function('middleware/cache_middleware.py',src,'cache_control',env)
    with tempfile.TemporaryDirectory(prefix='review-missing-image-') as folder:
        env['folder_paths']=NS(annotated_filepath=lambda filename:(filename,folder),get_directory_by_type=lambda typ:folder)
        handler,route=function('server.py',sources['server.py'],'view_image',env)
        assert route=='/view'
        for query in ({'filename':'missing.png'},{}):
            request=NS(path=route,rel_url=NS(query=query))
            out=asyncio.run(middleware(request,handler))
            assert out.status==404 and out.headers=={},(revision,out)
            observations.append(dict(case='F08_actual_view',revision=revision,request_path=route,query=query,status=out.status,headers=out.headers,image_extension_guard_matches=False))
# Real backend factory's selected statements, not the entire environment/config initialization.
for revision,sources in case('F04').items():
    captured=[];calls=[]
    table=NS(show_columns=lambda:NS(rows=lambda:[('id','varchar','',None),('pagerank_fea','float',1.0,None)]),update=lambda filt,values:captured.append(dict(values)))
    conn=NS(get_database=lambda name:NS(get_table=lambda name:table))
    logger=logging.getLogger('provenance')
    method_env=dict(re=re,json=json,logger=logger,equivalent_condition_to_str=lambda *args:'declared-filter')
    function('rag/utils/infinity_conn.py',sources['rag/utils/infinity_conn.py'],'field_keyword',method_env)
    actual_update,_=function('rag/utils/infinity_conn.py',sources['rag/utils/infinity_conn.py'],'update',method_env)
    class InfinityConnection:
        def __init__(self):self.dbName='local';self.connPool=NS(get_conn=lambda:conn,release_conn=lambda c:None)
        def update(self,condition,newValue,indexName,knowledgebaseId):
            calls.append(dict(receiver='InfinityConnection',condition=copy.deepcopy(condition),payload=copy.deepcopy(newValue)))
            return actual_update(self,condition,newValue,indexName,knowledgebaseId)
    class ESConnection:
        def update(self,condition,newValue,indexName,knowledgebaseId):
            calls.append(dict(receiver='ESConnection',condition=copy.deepcopy(condition),payload=copy.deepcopy(newValue)))
            return True # Dispatch recorder only: no claim about ES update behavior.
    factory_src=sources['api/settings.py'];init=next(n for n in ast.parse(factory_src).body if isinstance(n,ast.FunctionDef) and n.name=='init_settings')
    start=next(i for i,n in enumerate(init.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='DOC_ENGINE' for t in n.targets))
    fragment=init.body[start:start+3]
    assert isinstance(fragment[2],ast.If)
    for node in fragment:record('api/settings.py',factory_src,node,'executed backend-selection fragment of init_settings')
    fldsrc=sources['rag/settings.py'];fld=next(n for n in ast.parse(fldsrc).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PAGERANK_FLD' for t in n.targets))
    fldenv={};compile_nodes([fld],'rag/settings.py',fldenv);record('rag/settings.py',fldsrc,fld,'executed pagerank field constant')
    for engine in (None,'infinity'):
        for target in (0,2,1) if engine=='infinity' else (0,):
            captured.clear();calls.clear()
            factory_env=dict(os=NS(environ={} if engine is None else {'DOC_ENGINE':engine}),rag=NS(utils=NS(es_conn=NS(ESConnection=ESConnection),infinity_conn=NS(InfinityConnection=InfinityConnection))))
            compile_nodes(fragment,'api/settings.py',factory_env)
            kb=NS(id='kb-1',tenant_id='tenant-1',name='fixture',pagerank=1,to_dict=lambda:{'id':'kb-1'})
            service=NS(accessible4deletion=lambda *a:True,query=lambda **kw:[kb],get_by_id=lambda *a:(True,kb),update_by_id=lambda *a:True)
            req=dict(kb_id='kb-1',name='fixture',description='fixture',parser_id='naive',pagerank=target)
            env=dict(request=NS(json=req),current_user=NS(id='user-1'),KnowledgebaseService=service,settings=NS(docStoreConn=factory_env['docStoreConn']),os=NS(environ={} if engine is None else {'DOC_ENGINE':engine}),StatusEnum=NS(VALID=NS(value='1')),PAGERANK_FLD=fldenv['PAGERANK_FLD'],search=NS(index_name=lambda tenant:'documents'),get_json_result=lambda **kw:kw,get_data_error_result=lambda **kw:kw,server_error_response=lambda exc:(_ for _ in ()).throw(exc))
            endpoint,route=function('api/apps/kb_app.py',sources['api/apps/kb_app.py'],'update',env)
            result=endpoint();assert 'data' in result
            if target==1:assert not calls and not captured
            else:assert len(calls)==1
            invalid=sorted(set(captured[0])-{'id','pagerank_fea'}) if captured else []
            expected=['remove'] if engine=='infinity' and target==0 and revision=='head' else []
            assert invalid==expected,(revision,engine,target,invalid)
            observations.append(dict(case='F04_actual_kb_update',revision=revision,route_suffix=route,declared_DOC_ENGINE=engine or '(unset: source default)',selected_class=type(factory_env['docStoreConn']).__name__,existing_pagerank=1,request_pagerank=target,calls=copy.deepcopy(calls),database_submitted_payloads=copy.deepcopy(captured),unknown_schema_columns=invalid,backend_boundary='Infinity method body executed against table schema recorder; ES only dispatch recorded; HTTP decorators and ORM/auth boundaries stubbed'))
# Source context is immutable per version; de-duplicate repeated extracted spans.
unique={json.dumps(s,sort_keys=True):s for s in spans}
result=dict(status='all_expected_observations_verified',execution_count=len(observations),observations=observations,source_evidence=list(unique.values()),limitations=['Injected base/head case revisions, not upstream PR reproduction.','Explicit declared fixtures, not actual deployment settings/traffic.','AST function bodies executed without framework decorators or server registration.','No real database, HTTP server, authentication, browser cache or filesystem-existing image exercised.'])
(R/'observations.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':result['status'],'executions':len(observations),'source_spans':len(unique)},ensure_ascii=False))
