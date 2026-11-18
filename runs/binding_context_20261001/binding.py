"""Conservative source-only factory/argument/callback closure, no oracle inputs."""
import ast,re,importlib.util,sys
from pathlib import Path
from collections import deque
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from ace_review.context_adaptive import EvidenceState
OLD=Path(__file__).resolve().parents[1]/'icml_priority_20261001/policy.py'
spec=importlib.util.spec_from_file_location('definition_v1',OLD);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
def dotted(x):
 if isinstance(x,ast.Name):return x.id
 if isinstance(x,ast.Attribute):
  a=dotted(x.value);return a+'.'+x.attr if a else ''
 return ''
class Bindings(old.Definitions):
 def value(self,p,x,env,depth=0):
  if depth>3:return None
  if isinstance(x,ast.Name) and x.id in env:return env[x.id]
  if isinstance(x,ast.Attribute):
   recv=self.value(p,x.value,env,depth+1)
   if recv and recv[0] in ('instance','class'):
    q,name=recv[1];target=(q,name+'.'+x.attr)
    if target in self.defs:return ('function',target)
  if isinstance(x,(ast.Name,ast.Attribute)):
   targets=self.resolve(p,dotted(x))
   if len(targets)==1:
    t=targets[0];return ('class' if isinstance(self.defs[t],ast.ClassDef) else 'function',t)
  if isinstance(x,ast.Call):
   fn=self.value(p,x.func,env,depth+1)
   if fn and fn[0]=='class':return ('instance',fn[1])
   if fn and fn[0]=='function':
    q,name=fn[1];node=self.defs[q,name]
    # Only unconditional, linear factories; branch/loop/try/unknown returns rejected.
    body=[s for s in node.body if not(isinstance(s,ast.Expr) and isinstance(s.value,ast.Constant) and isinstance(s.value.value,str))]
    if not body or not isinstance(body[-1],ast.Return) or any(not isinstance(s,(ast.Assign,ast.AnnAssign)) for s in body[:-1]):return None
    local=self.arguments(p,x,q,name,env,depth+1)
    for stmt in body[:-1]:
     targets=stmt.targets if isinstance(stmt,ast.Assign) else [stmt.target]
     for target in targets:
      if isinstance(target,ast.Name):local[target.id]=self.value(q,stmt.value,local,depth+1)
    result=self.value(q,body[-1].value,local,depth+1)
    return result if result and result[0]=='instance' else None
  return None
 def arguments(self,p,call,q,name,env,depth=0):
  n=self.defs[q,name];params=[a.arg for a in n.args.posonlyargs+n.args.args] if hasattr(n,'args') else []
  if params and params[0] in ('self','cls') and isinstance(call.func,ast.Attribute):params=params[1:]
  result={name:self.value(p,arg,env,depth+1) for name,arg in zip(params,call.args)}
  for kw in call.keywords:
   if kw.arg in params:result[kw.arg]=self.value(p,kw.value,env,depth+1)
  return result
 def edges(self,p,name,incoming=None):
  n=self.defs[p,name]
  if isinstance(n,ast.ClassDef):return []
  env={a.arg:None for a in n.args.posonlyargs+n.args.args+n.args.kwonlyargs};env.update(incoming or {});out=[]
  for stmt in n.body:
   if isinstance(stmt,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):continue
   for x in ast.walk(stmt):
    if isinstance(x,ast.Name) and isinstance(x.ctx,ast.Store):env.setdefault(x.id,None)
  if '.' in name:
   cls=name.split('.')[0];env['self']=('instance',(p,cls));env['cls']=('class',(p,cls))
  for stmt in n.body:
   if isinstance(stmt,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):continue
   # Branch-local writes make incoming identities uncertain; never use a stale type.
   if not isinstance(stmt,(ast.Assign,ast.AnnAssign,ast.Expr,ast.Return)):
    for x in ast.walk(stmt):
     if isinstance(x,ast.Name) and isinstance(x.ctx,ast.Store):env[x.id]=None
   calls=sorted([x for x in ast.walk(stmt) if isinstance(x,ast.Call)],key=lambda x:(x.lineno,x.col_offset))
   for call in calls:
    fn=self.value(p,call.func,env);target=fn[1] if fn else None
    bound=self.arguments(p,call,*target,env) if fn and fn[0]=='function' else {}
    out.append({'called':dotted(call.func) or ast.unparse(call.func),'target':target,'incoming':bound,'line':call.lineno,'kind':'call','resolution':fn[0] if fn else 'unresolved'})
    # Function-valued arguments are source references even without parentheses.
    for arg in list(call.args)+[k.value for k in call.keywords]:
     value=self.value(p,arg,env)
     if value and value[0]=='function':out.append({'called':ast.unparse(arg),'target':value[1],'incoming':{},'line':call.lineno,'kind':'callback_argument','resolution':'function'})
   if isinstance(stmt,(ast.Assign,ast.AnnAssign)):
    value=self.value(p,stmt.value,env);targets=stmt.targets if isinstance(stmt,ast.Assign) else [stmt.target]
    for target in targets:
     if isinstance(target,ast.Name):env[target.id]=value
     else:
      # Unknown mutation may invalidate a receiver identity; no field inference.
      root=dotted(target).split('.')[0]
      if root in env:env[root]=None
  return out

def packet(corpus,case):
 state=old.lexical(corpus,case,800);prefix=state.audit();r=Bindings(corpus,case['world_spec']['versions']['head']);queue=deque();seen=set();audit=[]
 for module,name in re.findall(r'\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)',case['scope']):
  p=r.modules.get(module)
  if p and (p,name) in r.defs:queue.append((p,name,{},0,'scope_entry'))
 for k in sorted(corpus.seeds|set(state.lines)):
  u=corpus.units[k]
  if u.version=='head' and (u.file,u.symbol) in r.defs:queue.append((u.file,u.symbol,{},0,'changed_or_visible'))
 attempts=0
 while queue and attempts<12:
  p,name,env,depth,origin=queue.popleft();key=(p,name,repr(sorted(env.items())))
  if key in seen:continue
  seen.add(key)
  for k in r.unit(p,name):state.add([k],'L1',1600)
  if depth>=3:continue
  for edge in r.edges(p,name,env):
   attempts+=1;target=edge['target'];entry={'source_file':p,'source_symbol':name,'depth':depth,'origin':origin,**edge,'added':[],'target_visible':False}
   if target:
    q,symbol=target
    for k in r.unit(q,symbol):
     if state.add([k],'L1',1600):entry['added'].append(k)
    keys=r.unit(q,symbol);entry['target_visible']=bool(keys) and all(k in state.lines for k in keys)
    queue.append((q,symbol,edge['incoming'],depth+1,edge['kind']))
   audit.append(entry)
   if attempts>=12:break
 old.lexical(corpus,case,1600,state)
 return state,{'prefix':prefix,'jumps':audit,'state':state.audit(),'limits':{'candidate_edges':12,'depth':3,'source_tokens':1600},'note':'Static source candidates; known linear factories, local aliases, call-site parameter binding, explicit function/bound-method callback references. Unknown external parameters and branch assignments unresolved. No runtime guarantee.'}
