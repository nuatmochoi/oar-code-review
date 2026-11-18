"""Explicit partial source evidence. Syntax dependencies are candidates, never runtime bindings."""
import ast,re
from collections import defaultdict
from ace_review.context_adaptive import EvidenceState,token_count

class FragmentState(EvidenceState):
 def __init__(self,corpus,versions,edits,scope):
  super().__init__(corpus);self.edits=edits;self.scope=scope;self.trees={v:{p:ast.parse(s) for p,s in src.items()} for v,src in versions.items()};self.reasons=[];self.focus=defaultdict(set);self.fn_index=defaultdict(list);self.file_nodes={};self.group_cache={}
  for v,files in self.trees.items():
   for file,tree in files.items():
    nodes=list(ast.walk(tree));self.file_nodes[v,file]=nodes
    for n in nodes:
     if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):self.fn_index[v,file,n.end_lineno].append(n)
 def groups(self,k):
  u=self.corpus.units[k];cachekey=(k,tuple(sorted(self.focus[k])));cached=self.group_cache.get(cachekey)
  if cached is not None:return cached
  nodes=[n for n in self.fn_index[u.version,u.file,u.end] if n.lineno>=u.start]
  if not nodes:return [set(range(u.start,u.end+1))]
  fn=min(nodes,key=lambda n:n.lineno);hot={n for n in self.edits[u.version][u.file] if u.start<=n<=u.end}|self.focus[k]
  if not hot:
   calls=[n for n in ast.walk(fn) if isinstance(n,ast.Call)];hot={n.lineno for n in calls[:3]}
  groups=[set(range(u.start,min(u.end,u.header_end)+1))];wanted=set()
  for line in sorted(hot):
   groups.append(set(range(max(u.start,line-1),min(u.end,line+2)+1)))
   owners=[n for n in ast.walk(fn) if isinstance(n,ast.stmt) and n.lineno<=line<=n.end_lineno]
   for n in sorted(owners,key=lambda n:(n.end_lineno-n.lineno,n.lineno)):
    if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
    if hasattr(n,'body') and isinstance(n.body,list) and n.body:
     groups.append(set(range(n.lineno,n.body[0].lineno)));test=getattr(n,'test',getattr(n,'iter',None));wanted.update(x.id for x in ast.walk(test) if isinstance(x,ast.Name)) if test else None
    else:
     groups.append(set(range(n.lineno,min(n.end_lineno,n.lineno+6)+1)));wanted.update(x.id for x in ast.walk(n) if isinstance(x,ast.Name) and isinstance(x.ctx,ast.Load))
  # Explicit assignments/pop/default extraction for names used near the selected branch.
  assignments=[n for n in ast.walk(fn) if isinstance(n,(ast.Assign,ast.AnnAssign,ast.AugAssign))]
  for _ in range(2):
   for n in assignments:
    targets=n.targets if isinstance(n,ast.Assign) else [n.target]
    defined={x.id for t in targets for x in ast.walk(t) if isinstance(x,ast.Name)}
    if wanted&defined:
     groups.append(set(range(n.lineno,min(n.end_lineno,n.lineno+6)+1)));wanted.update(x.id for x in ast.walk(n.value) if isinstance(x,ast.Name))
  for n in ast.walk(fn):
   if isinstance(n,ast.Return) and (n.lineno in hot or not hot):groups.append(set(range(n.lineno,min(n.end_lineno,n.lineno+3)+1)))
  result=[g&set(range(u.start,u.end+1)) for g in groups if g];self.group_cache[cachekey]=result;return result
 def add_partial(self,k,cap,reason):
  before={x:set(y) for x,y in self.lines.items()};unit_limit=300
  for group in self.groups(k):
   updated={x:set(y) for x,y in self.lines.items()};updated.setdefault(k,set()).update(group)
   one=self.render({k:updated[k]},{});text=self.render(updated,self.paths)
   if token_count(one)<=unit_limit and token_count(text)<=cap:self.lines=updated
  changed=before!=self.lines
  if changed:self.reasons.append({'unit_id':k,'reason':reason,'selected_lines':sorted(self.lines[k]),'omitted_code_unknown':self.lines[k]!=set(range(self.corpus.units[k].start,self.corpus.units[k].end+1))})
  return changed
 def add(self,nodes,level,cap):
  changed=False
  for k in dict.fromkeys(nodes):
   u=self.corpus.units[k];whole=token_count(self.render({k:set(range(u.start,u.end+1))},{}))
   if level=='L1' and whole>300:changed=self.add_partial(k,cap,'large_source_unit') or changed
   else:
    before={x:set(y) for x,y in self.lines.items()};added=super().add([k],level,cap);changed=added or changed
    if level=='L1' and not added and before==self.lines and self.lines.get(k,set())!=set(range(u.start,u.end+1)):changed=self.add_partial(k,cap,'budget_blocked_source_unit') or changed
  return changed
 def audit(self):return super().audit()|{'fragment_selections':self.reasons}

def initial(corpus,versions,case,edits):
 state=FragmentState(corpus,versions,edits,case['scope']);tails=set()
 for u in corpus.units.values():
  if u.kind=='function' and any(u.start<=n<=u.end for n in edits[u.version][u.file]):tails.add(u.symbol.split('.')[-1])
 # Candidate caller lines: static name matching, no inferred target or argument value.
 callers=[]
 for k,u in corpus.units.items():
  if u.kind!='function':continue
  for n in state.file_nodes[u.version,u.file]:
   if isinstance(n,ast.Call) and u.start<=n.lineno<=u.end:
    tail=n.func.attr if isinstance(n.func,ast.Attribute) else n.func.id if isinstance(n.func,ast.Name) else ''
    if tail in tails:state.focus[k].add(n.lineno)
  if state.focus[k]:callers.append(k)
 changed=[k for k,u in corpus.units.items() if u.kind=='function' and any(u.start<=n<=u.end for n in edits[u.version][u.file])]
 for k in sorted(changed,key=lambda k:(corpus.units[k].version!='head',corpus.units[k].file,corpus.units[k].start)):state.add_partial(k,600,'changed_branch_signature_dependencies')
 for k in sorted(callers,key=lambda k:(corpus.units[k].version!='head',corpus.units[k].file,corpus.units[k].start))[:8]:state.add_partial(k,800,'syntactic_caller_arguments_candidate')
 return state
