"""Extract original target body, execute deterministic contracts; no gold labels."""
import ast,copy,json,sys,types
from pathlib import Path
cid,snapshot=sys.argv[1],Path(sys.argv[2]);NS=types.SimpleNamespace
spans=[]
def load(p,cls,name,env):
 tree=ast.parse((snapshot/p).read_text());nodes=tree.body if cls is None else next(n.body for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
 n=copy.deepcopy(next(n for n in nodes if isinstance(n,ast.FunctionDef) and n.name==name));n.decorator_list=[];spans.append({'file':p,'symbol':name,'start':n.lineno,'end':n.end_lineno});module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),n],type_ignores=[]);ast.fix_missing_locations(module);exec(compile(module,p,'exec'),env);return env[name]
a=load('deployment.py',None,'arguments',{})()
if cid=='H01':
 fn=load('browser_use/llm/google/chat.py','ChatGoogle','_get_client_params',{});actual=fn(NS(**a));expected={k:v for k,v in a.items() if v is not None}
elif cid=='H02':
 response=load('deployment.py',None,'metadata_response',{'SimpleNamespace':NS})(a);fn=load('browser_use/llm/google/chat.py','ChatGoogle','_get_usage',{'ChatInvokeUsage':lambda **kw:kw});actual=fn(None,response)['completion_tokens'];expected=a['candidate_tokens']+a['thought_tokens']
elif cid=='H03':
 fn=load('comfy_extras/nodes_string.py','StringContains','execute',{});actual=fn(None,**a)[0];expected=a['substring'] in a['string'] if a['case_sensitive'] else a['substring'].lower() in a['string'].lower()
elif cid=='H04':
 fn=load('comfy_extras/nodes_string.py','StringSubstring','execute',{});actual=fn(None,**a)[0];expected=a['string'][a['start']:a['end']]
else:raise ValueError(cid)
failed=actual!=expected;print(json.dumps({'case_id':cid,'status':'target_failure' if failed else 'pass','actual':actual,'expected':expected,'executed_spans':spans}));sys.exit(10 if failed else 0)
