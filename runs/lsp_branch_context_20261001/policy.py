"""B: changed/entry first. C: B + receiver/property/global/import provenance. No gold/witness inputs."""
import ast,heapq,re,sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote,urlparse
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'runs/lsp_backend_20261001'))
import backend as ref
from fragments import initial
from ace_review.context_adaptive import digest
CAP=1600;PREFIX=800;MAX_QUERIES=12

def edited_lines(diff):
 out={'base':defaultdict(set),'head':defaultdict(set)};file=None;a=b=0
 for line in diff.splitlines():
  if line.startswith('+++ b/'):file=line[6:]
  elif line.startswith('@@'):
   m=re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)',line)
   if m:a,b=map(int,m.groups())
  elif file and line.startswith('+') and not line.startswith('+++'):out['head'][file].add(b);b+=1
  elif file and line.startswith('-') and not line.startswith('---'):out['base'][file].add(a);a+=1
  elif line.startswith(' '):a+=1;b+=1
 return out

def roots(corpus,case,resolver):
 edits=edited_lines(case['diff']);changed=set()
 for (file,name),node in resolver.defs.items():
  if isinstance(node,ast.ClassDef):continue
  if any(node.lineno<=n<=node.end_lineno for n in edits['head'][file]):changed.add((file,name))
 for u in corpus.units.values():
  if u.version=='base' and any(u.start<=n<=u.end for n in edits['base'][u.file]) and (u.file,u.symbol) in resolver.defs:changed.add((u.file,u.symbol))
 # Scope identifier matches are candidates, not asserted runtime entry bindings.
 calls=re.findall(r'\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(',case['scope']);entries=[]
 changed_files=set(edits['head'])|set(edits['base'])
 for called in calls:
  tail=called.split('.')[-1];receiver=called.split('.')[-2] if '.' in called else None
  candidates=[t for t,n in resolver.defs.items() if n.name==tail and not isinstance(n,ast.ClassDef)]
  candidates.sort(key=lambda t:(not(receiver and receiver in t[1].split('.')[:-1]),t[0] not in changed_files,t[0],resolver.defs[t].lineno,t[1]))
  entries.extend(candidates[:3])
 entries=list(dict.fromkeys(entries));ordered=[(p,n,'edited_function',0) for p,n in sorted(changed,key=lambda t:(t[0],resolver.defs[t].lineno,t[1]))]
 ordered += [(p,n,'public_entry_candidate',1) for p,n in entries if (p,n) not in changed]
 return ordered,edits

def extra_sites(resolver,file,name):
 n=resolver.defs[file,name]
 if isinstance(n,ast.ClassDef):return []
 globals_=set(resolver.imports[file])|{n.split('.')[0] for p,n in resolver.defs if p==file}
 for stmt in resolver.trees[file].body:
  if isinstance(stmt,(ast.Assign,ast.AnnAssign)):
   for target in stmt.targets if isinstance(stmt,ast.Assign) else [stmt.target]:
    globals_.update(x.id for x in ast.walk(target) if isinstance(x,ast.Name))
 locals_={a.arg for a in n.args.posonlyargs+n.args.args+n.args.kwonlyargs}|{x.id for x in ast.walk(n) if isinstance(x,ast.Name) and isinstance(x.ctx,ast.Store)}
 calls=ref.shared_sites(resolver,file,name);used={ref.position(resolver.sources[file],x) for x,_,_ in calls};extra=[]
 # Inspect receiver attributes separately: receiver definition is not method definition.
 for func,_,_ in calls:
  if isinstance(func,ast.Attribute):
   x=func.value
   while isinstance(x,ast.Attribute):
    if ref.position(resolver.sources[file],x) not in used:extra.append((x,'receiver_provenance',ast.unparse(x)))
    x=x.value
 for stmt in n.body:
  if isinstance(stmt,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):continue
  for x in ast.walk(stmt):
   if isinstance(x,ast.Name) and isinstance(x.ctx,ast.Load) and x.id not in locals_ and x.id in globals_ and ref.position(resolver.sources[file],x) not in used:
    extra.append((x,'global_or_import_reference',x.id))
 seen=set();out=[]
 for x,k,c in sorted(extra,key=lambda e:(e[0].lineno,e[0].col_offset,e[1])):
  key=(x.lineno,x.col_offset,ast.unparse(x))
  if key not in seen:seen.add(key);out.append((x,k,c))
 return out

def unit_locations(corpus,client,result):
 if isinstance(result,dict):result=[result]
 units=[]
 for loc in result or []:
  uri=loc.get('targetUri',loc.get('uri'));rng=loc.get('targetSelectionRange',loc.get('range'))
  if not uri or not rng:continue
  try:file=str(Path(unquote(urlparse(uri).path)).relative_to(client.root))
  except ValueError:continue
  line=rng['start']['line']+1
  units.extend(sorted(corpus.locations.get(('head',file,line),set())))
 return list(dict.fromkeys(units))

def packet(corpus,case,resolver,client,arm,versions):
 assert arm in ['D','E'];rootlist,edits=roots(corpus,case,resolver);state=initial(corpus,versions,case,edits);ref.old.old.lexical(corpus,case,PREFIX,state);prefix=state.audit();native=ref.LSPBackend(resolver,client);heap=[];queued=set();visited=set();audit=[];sequence=0
 def enqueue(file,name,depth,origin,rank):
  nonlocal sequence
  if (file,name) in queued or depth>3:return
  queued.add((file,name))
  calls=ref.shared_sites(resolver,file,name);extras=[]
  for node,kind,called in calls+extras:
   changed=any(getattr(node,'lineno',0)<=i<=getattr(node,'end_lineno',node.lineno) for i in edits['head'][file])
   # Native builtins/locals remain eligible in B; only order changes.
   priority=(0 if changed else rank+2,0 if kind=='receiver_provenance' else 1 if kind=='global_or_import_reference' else 2 if kind=='call' else 3,depth,node.lineno)
   heapq.heappush(heap,(priority,sequence,file,name,node,kind,called,depth,origin));sequence+=1
 for p,n,origin,rank in rootlist:enqueue(p,n,0,origin,rank)
 # Same lexical fallback roots as A, after directly changed/entry candidates.
 for uid in sorted(corpus.seeds|set(state.lines)):
  u=corpus.units[uid]
  if u.version=='head' and (u.file,u.symbol) in resolver.defs:enqueue(u.file,u.symbol,0,'lexical_fallback',8)
 extra_used=0
 while heap and len(audit)<MAX_QUERIES:
  priority,_,file,name,node,kind,called,depth,origin=heapq.heappop(heap);line,char=ref.position(resolver.sources[file],node);site=(file,line,char)
  if site in visited:continue
  if kind in ['receiver_provenance','global_or_import_reference'] and (extra_used>=4 or CAP-state.tokens<80):continue
  visited.add(site)
  for uid in resolver.unit(file,name):state.add([uid],'L1',CAP)
  if kind in ['receiver_provenance','global_or_import_reference']:extra_used+=1
  error=None;result=None;targets=[];excluded=[]
  try:result=client.definition(file,line,char);targets,excluded=native.map_result(result)
  except Exception as e:error=repr(e)
  units=list(dict.fromkeys(uid for t in targets for uid in resolver.unit(*t)))
  if arm=='E' and kind in ['receiver_provenance','global_or_import_reference'] and not error:units=list(dict.fromkeys(units+unit_locations(corpus,client,result)))
  # C includes only source import statements for names actually referenced at this site.
  import_units=[]
  if arm=='E' and kind in ['receiver_provenance','global_or_import_reference']:
   names={x.id for x in ast.walk(node) if isinstance(x,ast.Name)}
   for stmt in resolver.trees[file].body:
    if isinstance(stmt,(ast.Import,ast.ImportFrom)) and any((a.asname or a.name.split('.')[0]) in names for a in stmt.names):
     import_units.extend(sorted(corpus.locations.get(('head',file,stmt.lineno),set())))
  added=[]
  for uid in import_units+units:
   if state.add([uid],'L1',CAP):added.append(uid)
  rec={'source_file':file,'source_symbol':name,'line':line+1,'utf16_character':char,'called':called,'kind':kind,'depth':depth,'origin':origin,'priority':list(priority),'targets':targets,'raw_result':result,'excluded':excluded,'error':error,'resolution':'server_error' if error else 'multiple_static_candidates' if len(targets)>1 else 'unique_static_candidate' if targets else 'unresolved','unit_targets':units,'import_units':import_units,'added':added}
  audit.append(rec)
  # E opens provenance queries only for an unresolved call or a visible undeclared dependency.
  if arm=='E' and kind=='call' and not targets and not error:
   for x,k,c in extra_sites(resolver,file,name):
    if k!='receiver_provenance' or not isinstance(node,ast.Attribute):continue
    if not (node.lineno==x.lineno and ast.unparse(x) in ast.unparse(node.value)):continue
    heapq.heappush(heap,((1,0,depth,x.lineno),sequence,file,name,x,k,c,depth,'unresolved_receiver_gate'));sequence+=1
  if arm=='E' and kind not in ['receiver_provenance','global_or_import_reference']:
   visible=set().union(*(state.lines.get(uid,set()) for uid in resolver.unit(file,name)))
   for x,k,c in extra_sites(resolver,file,name):
    if k!='global_or_import_reference' or x.lineno not in visible:continue
    # Query only when the imported/module-declared name's declaration is not already supplied.
    declaration_lines=[]
    for stmt in resolver.trees[file].body:
     if isinstance(stmt,(ast.Assign,ast.AnnAssign)) and any(isinstance(t,ast.Name) and t.id==c for t in ast.walk(stmt)):declaration_lines.append(stmt.lineno)
    if declaration_lines and any(any(uid in state.lines and line in state.lines[uid] for uid in corpus.locations.get(('head',file,line),set())) for line in declaration_lines):continue
    heapq.heappush(heap,((2,1,depth,x.lineno),sequence,file,name,x,k,c,depth,'visible_dependency_gate'));sequence+=1
  if depth<3:
   for p,n in targets:enqueue(p,n,depth+1,'native_definition',4+depth)
 ref.old.old.lexical(corpus,case,CAP,state)
 return state,{'prefix':prefix,'jumps':audit,'state':state.audit(),'priority_roots':rootlist,'extra_queries':extra_used,'limits':{'candidate_queries':12,'depth':3,'source_tokens':1600,'C_extra_queries_max':4},'backend':'actual_pyright_'+arm,'note':'B scheduling with explicit partial branch/caller source. E gates extra queries on unresolved call receivers or visible unprovided dependencies. Syntax matching and native definitions are static candidates, not runtime proof.'}
