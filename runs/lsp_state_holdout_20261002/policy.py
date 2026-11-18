"""Case-independent state reference candidates; no labels, witnesses, outcomes, or function-name recipes."""
import ast,hashlib,importlib.util,sys
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT/'runs/lsp_ablation_fresh_20261001'))
# A distinct module name is required because this file itself is named policy.py.
sp=importlib.util.spec_from_file_location('shared_state_policy',ROOT/'runs/lsp_ablation_fresh_20261001/policy.py');shared=importlib.util.module_from_spec(sp);sp.loader.exec_module(shared)
from ace_review.context_adaptive import EvidenceState
CAP=1600

def packet(corpus,versions,case,resolver,client,p):
 initial=shared.restored(corpus,p);roots,edits=shared.b.roots(corpus,case,resolver);state=shared.fm.FragmentState(corpus,versions,edits,case['scope']);state.lines={k:set(v) for k,v in initial.lines.items()};state.paths=dict(initial.paths);protected={k for k in initial.lines if corpus.units[k].version=='head' and k in corpus.seeds};sites=[]
 for file,name,origin,rank in roots:
  if origin!='edited_function':continue
  fn=resolver.defs[file,name];globals_=set(resolver.imports[file]);tree=resolver.trees[file]
  for n in tree.body:
   if isinstance(n,(ast.Assign,ast.AnnAssign)):
    globals_.update(x.id for t in (n.targets if isinstance(n,ast.Assign) else [n.target]) for x in ast.walk(t) if isinstance(x,ast.Name))
  calls={id(x.func) for x in ast.walk(fn) if isinstance(x,ast.Call)};candidates=[]
  # Global map keys track shared state across producer/consumer functions.
  for n in ast.walk(fn):
   if isinstance(n,ast.Subscript) and isinstance(n.slice,ast.Name) and n.slice.id in globals_:candidates.append((0,n.slice))
   if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ['get','pop','setdefault'] and n.args and isinstance(n.args[0],ast.Name) and n.args[0].id in globals_:candidates.append((0,n.args[0]))
   if isinstance(n,ast.Attribute) and id(n) not in calls and isinstance(n.value,ast.Name):
    candidates.append((1 if isinstance(n.ctx,ast.Store) else 2,n))
  for kind,node in candidates:
   near=any(node.lineno<=n<=node.end_lineno for n in edits['head'][file]);sites.append(((kind,not near,rank,file,node.lineno,node.col_offset),file,name,node))
 sites.sort(key=lambda x:x[0]);extras=[];selected=[];seen=set();removable=sorted(set(initial.lines)-protected,key=lambda k:(corpus.units[k].version!='base',corpus.scores(case['scope']).get(k,0),k));evicted=[];additions=[]
 for _,file,name,node in sites:
  symbol=ast.unparse(node);key=(file,symbol)
  if key in seen:continue
  seen.add(key);line,char=shared.ref.position(resolver.sources[file],node);error=None;result=None;hits=[];excluded=0
  try:result=client.references(file,line,char);hits,excluded=shared.locations(corpus,client,result)
  except Exception as e:error=repr(e)
  # Favor references in other functions over the already-visible producer.
  hits.sort(key=lambda x:(corpus.units[x[0]].symbol==name,corpus.units[x[0]].file!=file,corpus.units[x[0]].file,corpus.units[x[0]].start,x[1]));ids=list(dict.fromkeys(k for k,l in hits));extras.append({'method':'references','file':file,'line':line+1,'utf16_character':char,'symbol':symbol,'units':ids,'raw_result':result,'error':error,'excluded_reference_count':excluded,'mapped_reference_count':len(hits)})
  for k,l in hits[:8]:
   u=corpus.units[k]
   if u.symbol==name:continue
   state.focus[k].add(l);selected.append((k,l))
  if len(extras)>=3:break
 for k,line in list(dict.fromkeys(selected)):
  if k in protected:continue
  before={x:set(y) for x,y in state.lines.items()};paths=dict(state.paths);removed=[]
  while True:
   u=corpus.units[k];whole=set(range(u.start,u.end+1));updated={x:set(y) for x,y in state.lines.items()};updated.setdefault(k,set()).update(whole)
   if shared.token_count(state.render({k:whole},{}))<=500 and shared.token_count(state.render(updated,state.paths))<=CAP:state.lines=updated;ok=True
   else:ok=state.add_partial(k,CAP,'native_state_reference') if u.kind=='function' else EvidenceState.add(state,[k],'L1',CAP)
   if ok and line in state.lines.get(k,set()):
    protected.add(k);evicted+=removed;additions.append({'unit_id':k,'reference_line':line,'evicted':removed});break
   state.lines={x:set(y) for x,y in before.items() if x not in removed};state.paths=dict(paths);choices=[x for x in removable if x in state.lines and x not in protected and x!=k]
   if not choices:state.lines=before;state.paths=paths;break
   victim=choices[0];state.lines.pop(victim);removed.append(victim)
 assert state.tokens<=CAP;assert all(state.lines[k]==initial.lines[k] for k in protected&set(initial.lines))
 return state,{'state':state.audit(),'jumps':p['B_audit']['jumps'],'extra_navigation':extras,'protected_ids':sorted(protected&set(initial.lines)),'evicted_ids':evicted,'additions':additions,'selection':'Global subscript/get/pop keys first, then assigned/read non-call receiver attributes; changed proximity and source order. Native references only; no semantic/runtime fallback.'},shared.observation(p['B_audit']['jumps'],extras)
