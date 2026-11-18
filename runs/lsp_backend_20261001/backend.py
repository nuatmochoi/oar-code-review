"""Same traversal, syntax candidate sites and source budget; actual LSP vs AST bindings."""
from __future__ import annotations
import ast,re,sys,importlib.util
from pathlib import Path
from collections import deque
from urllib.parse import urlparse,unquote
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import EvidenceState
sp=importlib.util.spec_from_file_location('binding_reference',ROOT/'runs/binding_context_20261001/binding.py');old=importlib.util.module_from_spec(sp);sp.loader.exec_module(old)
CAP=1600;PREFIX=800;MAX_QUERIES=12;DEPTH=3

def position(src,node):
 line=src.splitlines()[node.lineno-1]
 byte=node.end_col_offset-len(node.attr.encode()) if isinstance(node,ast.Attribute) else node.col_offset
 char=len(line.encode()[:byte].decode().encode('utf-16-le'))//2
 return node.lineno-1,char

def shared_sites(resolver,file,name):
 n=resolver.defs[file,name]
 if isinstance(n,ast.ClassDef):return []
 output=[]
 for stmt in n.body:
  if isinstance(stmt,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):continue
  for call in sorted((x for x in ast.walk(stmt) if isinstance(x,ast.Call)),key=lambda x:(x.lineno,x.col_offset)):
   if isinstance(call.func,(ast.Name,ast.Attribute)):
    output.append((call.func,'call',old.dotted(call.func) or ast.unparse(call.func)))
   for arg in list(call.args)+[kw.value for kw in call.keywords]:
    if isinstance(arg,(ast.Name,ast.Attribute)):
     output.append((arg,'callback_candidate',ast.unparse(arg)))
 return output

class ASTBackend:
 def __init__(self,resolver):self.r=resolver
 def resolve_sites(self,file,name,incoming):
  inferred=self.r.edges(file,name,incoming);used=set()
  for node,kind,called in shared_sites(self.r,file,name):
   match=None
   for i,e in enumerate(inferred):
    if i not in used and e['line']==node.lineno and e['called']==called and e['kind']==('call' if kind=='call' else 'callback_argument'):
     match=e;used.add(i);break
   target=match['target'] if match else None
   yield dict(node=node,kind=kind,called=called,targets=[target] if target else [],incoming={target:match['incoming']} if target else {},raw_result=None,resolution='unique_static_candidate' if target else 'unresolved')

class LSPBackend:
 def __init__(self,resolver,client):
  self.r=resolver;self.client=client;self.declarations={}
  for target,node in resolver.defs.items():
   file,name=target;line=self.r.sources[file].splitlines()[node.lineno-1]
   pattern=r'\b(?:def|class)\s+('+re.escape(node.name)+r')\b';m=re.search(pattern,line)
   if m:
    ch=len(line[:m.start(1)].encode('utf-16-le'))//2
    self.declarations[file,node.lineno-1,ch]=target
 def map_result(self,result):
  if isinstance(result,dict):result=[result]
  targets=[];excluded=[]
  for loc in result or []:
   uri=loc.get('targetUri',loc.get('uri'));rng=loc.get('targetSelectionRange',loc.get('range'))
   if not uri or not rng:excluded.append('invalid_location');continue
   path=Path(unquote(urlparse(uri).path))
   try:file=str(path.relative_to(self.client.root))
   except ValueError:excluded.append('outside_source_corpus:'+str(path));continue
   start=rng['start'];target=self.declarations.get((file,start['line'],start['character']))
   if target and target not in targets:targets.append(target)
   else:excluded.append('not_indexed_callable_declaration:'+file+':'+str(start))
  return targets,excluded
 def resolve_sites(self,file,name,incoming):
  for node,kind,called in shared_sites(self.r,file,name):
   line,char=position(self.r.sources[file],node);error=None;result=None
   try:result=self.client.definition(file,line,char);targets,excluded=self.map_result(result)
   except Exception as e:targets=[];excluded=[];error=repr(e)
   yield dict(node=node,kind=kind,called=called,targets=targets,incoming={},raw_result=result,excluded=excluded,error=error,resolution='server_error' if error else 'multiple_static_candidates' if len(targets)>1 else 'unique_static_candidate' if targets else 'unresolved')

def packet(corpus,case,resolver,backend):
 state=old.old.lexical(corpus,case,PREFIX);prefix=state.audit();queue=deque();seen=set();audit=[]
 for module,name in re.findall(r'\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)',case['scope']):
  file=resolver.modules.get(module)
  if file and (file,name) in resolver.defs:queue.append((file,name,{},0,'scope_entry'))
 for key in sorted(corpus.seeds|set(state.lines)):
  u=corpus.units[key]
  if u.version=='head' and (u.file,u.symbol) in resolver.defs:queue.append((u.file,u.symbol,{},0,'changed_or_visible'))
 attempts=0
 while queue and attempts<MAX_QUERIES:
  file,name,incoming,depth,origin=queue.popleft();key=(file,name,repr(sorted(incoming.items())))
  if key in seen:continue
  seen.add(key)
  for u in resolver.unit(file,name):state.add([u],'L1',CAP)
  if depth>=DEPTH:continue
  for edge in backend.resolve_sites(file,name,incoming):
   attempts+=1;node=edge.pop('node');line,char=position(resolver.sources[file],node);targets=edge['targets'];bindings=edge.pop('incoming')
   rec=dict(source_file=file,source_symbol=name,line=line+1,utf16_character=char,depth=depth,origin=origin,**edge,target_coverage=[])
   for target in targets:
    q,symbol=target;units=resolver.unit(q,symbol);added=[]
    for uid in units:
     if state.add([uid],'L1',CAP):added.append(uid)
    rec['target_coverage'].append(dict(file=q,symbol=symbol,unit_ids=units,added=added,visible=bool(units) and all(uid in state.lines for uid in units)))
    queue.append((q,symbol,bindings.get(target,{}),depth+1,edge['kind']))
   audit.append(rec)
   if attempts>=MAX_QUERIES:break
 old.old.lexical(corpus,case,CAP,state)
 return state,dict(prefix=prefix,jumps=audit,state=state.audit(),limits={'candidate_queries':MAX_QUERIES,'depth':DEPTH,'source_tokens':CAP},backend=type(backend).__name__,note='Same syntactic call/callback candidates and traversal rules. LSP targets exclusively returned by native textDocument/definition; no AST resolution fallback. Only in-corpus indexed callable declarations admitted. Targets are static candidates, not runtime dispatch proof. AST can propagate explicit call-site values; LSP uses native static inference without injecting AST target hints.')
