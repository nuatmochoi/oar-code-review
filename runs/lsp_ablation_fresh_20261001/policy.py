"""P shared protected branch/import augmentation; R adds only native reference candidates to frozen P."""
import ast,importlib.util,re,sys
from pathlib import Path
from urllib.parse import unquote,urlparse
ROOT=Path(__file__).resolve().parents[2];R=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'runs/lsp_backend_20261001'))
import backend as ref
from lsp_client import Client as BaseClient
from ace_review.context_adaptive import EvidenceState,token_count
sp=importlib.util.spec_from_file_location('f_fragments',ROOT/'runs/lsp_branch_context_20261001/fragments.py');fm=importlib.util.module_from_spec(sp);sp.loader.exec_module(fm)
sp=importlib.util.spec_from_file_location('b_policy_fixed',ROOT/'runs/lsp_priority_20261001/policy.py');b=importlib.util.module_from_spec(sp);sp.loader.exec_module(b)
CAP=1600
class Client(BaseClient):
 def references(self,file,line,char):
  assert self.initialization['capabilities'].get('referencesProvider')
  if file not in self.opened:
   path=self.root/file;self.send({'jsonrpc':'2.0','method':'textDocument/didOpen','params':{'textDocument':{'uri':path.as_uri(),'languageId':'python','version':1,'text':path.read_text()}}});self.opened.add(file)
  return self.request('textDocument/references',{'textDocument':{'uri':(self.root/file).as_uri()},'position':{'line':line,'character':char},'context':{'includeDeclaration':False}})

def restored(corpus,p):
 state=EvidenceState(corpus);state.lines={k:set(v) for k,v in p['B_audit']['state']['source_lines'].items()};state.paths=p['B_audit']['state']['paths'];assert state.render()==p['B'];return state

def declaration_position(resolver,file,name):
 node=resolver.defs[file,name];line=resolver.sources[file].splitlines()[node.lineno-1];m=re.search(r'\bdef\s+('+re.escape(node.name)+r')\b',line);assert m;return node.lineno-1,len(line[:m.start(1)].encode('utf-16-le'))//2

def locations(corpus,client,result):
 out=[];excluded=0
 for loc in result or []:
  try:file=str(Path(unquote(urlparse(loc['uri']).path)).relative_to(client.root))
  except (ValueError,KeyError):excluded+=1;continue
  line=loc['range']['start']['line']+1;keys=sorted(corpus.locations.get(('head',file,line),set()))
  if not keys:excluded+=1;continue
  out.extend((k,line) for k in keys)
 return list(dict.fromkeys(out)),excluded

def observation(jumps,extras):
 # Common B observations are identical; at most120 of the400 tokens reserved for new observations.
 common=[]
 for i,j in enumerate(jumps,1):
  targets='; '.join(p+'::'+n for p,n in j['targets']) or 'unresolved in this analysis';line=f'[E-NAV-{i:02d}] head {j["source_file"]}:{j["line"]}, UTF16 column {j["utf16_character"]}, symbol {j["called"]}: static definition candidates -> {targets}.'
  if token_count('\n'.join(common+[line]))>280:break
  common.append(line)
 new=[]
 for i,x in enumerate(extras,1):
  ids=x.get('units',[]);line=f'[E-EXTRA-{i:02d}] {x["method"]} at {x["file"]}:{x["line"]}; static source candidates: '+(', '.join(ids[:4]) or 'unresolved')+'. Runtime execution/arguments unknown.'
  if token_count('\n'.join(new+[line]))>120:break
  new.append(line)
 return '\n'.join(common+new)

def packet(corpus,versions,case,resolver,client,p,arm):
 assert arm in ['P','R'];initial=restored(corpus,p);roots,edits=b.roots(corpus,case,resolver);extras=[];native=ref.LSPBackend(resolver,client)
 state=fm.FragmentState(corpus,versions,edits,case['scope']);state.lines={k:set(v) for k,v in initial.lines.items()};state.paths=dict(initial.paths)
 protected=set(p.get("inherited_protected_ids",[]))
 for k in state.lines:
  u=corpus.units[k]
  if k in corpus.seeds or u.kind!='function':protected.add(k)
 for file,name,_,_ in roots:
  protected.update(k for k in resolver.unit(file,name) if k in state.lines)
 for j in p['B_audit']['jumps']:protected.update(k for k in j.get('unit_targets',[]) if k in state.lines)
 # Protect already-visible callable imports, returns, and direct caller argument links to native/changed targets.
 targets={name.split('.')[-1] for file,name,origin,_ in roots if origin=='edited_function' and name.split('.')[-1] not in ['__init__','get','request']}
 for k in state.lines:
  u=corpus.units[k]
  if u.kind=='function':
   nodes=state.fn_index[u.version,u.file,u.end]
   if any(isinstance(x,ast.Call) and (x.func.attr if isinstance(x.func,ast.Attribute) else x.func.id if isinstance(x.func,ast.Name) else '') in targets for n in nodes for x in ast.walk(n)):protected.add(k)
 scores=corpus.scores(case['scope']+' '+ref.old.old.changed_query(case['diff'])) if hasattr(ref.old.old,'changed_query') else corpus.scores(case['scope'])
 removable=sorted(set(state.lines)-protected,key=lambda k:(scores.get(k,0),k));evicted=[];additions=[];ref_candidates=[];queue=[(file,name) for file,name,origin,rank in sorted(roots,key=lambda t:(t[2]!='edited_function',t[1].split('.')[-1]=='__init__',t[0],resolver.defs[t[0],t[1]].lineno))];queried=set()
 for _ in range(3 if arm=='R' else 0):
  while queue and queue[0] in queried:queue.pop(0)
  if not queue:break
  file,name=queue.pop(0);queried.add((file,name));line,char=declaration_position(resolver,file,name);error=None;result=None
  try:result=client.references(file,line,char);hits,excluded=locations(corpus,client,result)
  except Exception as e:error=repr(e);hits=[];excluded=0
  hits.sort(key=lambda t:(corpus.units[t[0]].file!=file,corpus.units[t[0]].symbol.startswith('_'),corpus.units[t[0]].start,t[0],t[1]));units=list(dict.fromkeys(k for k,l in hits));extras.append({'method':'references','file':file,'line':line+1,'symbol':name,'units':units,'mapped_reference_count':len(hits),'excluded_reference_count':excluded,'raw_result':result,'error':error})
  for k,l in hits[:8]:
   state.focus[k].add(l);ref_candidates.append((k,'native_reference_caller'))
   u=corpus.units[k]
   if k in state.lines:protected.add(k)
   if u.kind=='function' and (u.file,u.symbol) in resolver.defs and (u.file,u.symbol) not in queried:queue.insert(0,(u.file,u.symbol))
 # Include head anchors next to deletions, without claiming deleted code still exists.
 deleted_head={};file=None;headline=0
 for row in case['diff'].splitlines():
  if row.startswith('+++ b/'):file=row[6:]
  elif row.startswith('@@'):
   m=re.match(r'@@ -\d+(?:,\d+)? \+(\d+)',row)
   if m:headline=int(m.group(1))
  elif file and row.startswith('-') and not row.startswith('---'):deleted_head.setdefault(file,set()).add(headline)
  elif row.startswith('+') and not row.startswith('+++') or row.startswith(' '):headline+=1
 changed_head={(file,name) for file,name,origin,rank in roots if origin=='edited_function'}
 # Missing changed function: include parameter constructions/returns as well as changed branch.
 for k,u in corpus.units.items():
  changed=any(u.start<=n<=u.end for n in edits[u.version][u.file]) or u.version=='head' and (u.file,u.symbol) in changed_head
  if arm=='R' or u.kind!='function' or not changed or k in initial.lines:continue
  if u.version=='head':state.focus[k].update(n for n in deleted_head.get(u.file,set()) if u.start<=n<=u.end)
  nodes=state.fn_index[u.version,u.file,u.end]
  for fn in nodes:
   params={arg.arg for arg in fn.args.posonlyargs+fn.args.args+fn.args.kwonlyargs}
   for x in ast.walk(fn):
    if isinstance(x,ast.Call) and isinstance(x.func,ast.Name) and x.func.id in params:state.focus[k].add(x.lineno)
  ref_candidates.insert(0,(k,'missing_changed_branch_parameter_construction'))
 # P admits imports used by already-visible changed functions too. This shared fix is not specific to R.
 if arm=='P':
  for k in initial.lines:
   u=corpus.units[k]
   if u.kind=='function' and (any(u.start<=n<=u.end for n in edits[u.version][u.file]) or u.version=='head' and (u.file,u.symbol) in changed_head):
    ref_candidates.append((k,'visible_changed_imports_only'))
 # Source statements for actually referenced imported names in changed/caller units.
 imports=[]
 for k,reason in ref_candidates:
  u=corpus.units[k];names=set()
  for n in state.fn_index[u.version,u.file,u.end]:names.update(x.id for x in ast.walk(n) if isinstance(x,ast.Name) and isinstance(x.ctx,ast.Load))
  for stmt in state.trees[u.version][u.file].body:
   if isinstance(stmt,(ast.Import,ast.ImportFrom)) and any((alias.asname or alias.name.split('.')[0]) in names for alias in stmt.names):imports.extend((uid,'referenced_import_statement') for uid in sorted(corpus.locations.get((u.version,u.file,stmt.lineno),set())))
 for k,reason in list(dict.fromkeys(ref_candidates+imports)):
  if k in initial.lines and k in protected:continue # Never replace a preserved B whole unit with a fragment.
  snapshot={x:set(y) for x,y in state.lines.items()};snapshot_paths=dict(state.paths);removed=[]
  while True:
   before={x:set(y) for x,y in state.lines.items()};ok=state.add_partial(k,CAP,reason) if corpus.units[k].kind=='function' else EvidenceState.add(state,[k],'L1',CAP)
   mandatory=set()
   u=corpus.units[k]
   if u.kind=='function':
    if reason=='missing_changed_branch_parameter_construction':
     changed=sorted(n for n in edits[u.version][u.file] if u.start<=n<=u.end)
     if changed:mandatory.add(changed[0])
     if state.focus[k]:mandatory.add(min(state.focus[k]))
    elif state.focus[k]:mandatory.add(min(state.focus[k]))
   if ok and not mandatory.issubset(state.lines.get(k,set())):state.lines=before;ok=False
   if ok:
    additions.append({'unit_id':k,'reason':reason,'evicted':removed});protected.add(k);evicted+=removed;break
   choices=[uid for uid in removable if uid in state.lines and uid not in protected and uid!=k]
   if not choices:state.lines=snapshot;state.paths=snapshot_paths;break
   victim=choices[0];state.lines.pop(victim);removed.append(victim)
   state.paths={pid:nodes for pid,nodes in state.paths.items() if victim not in nodes}
 assert all(k in state.lines and state.lines[k]==initial.lines[k] for k in protected&set(initial.lines));assert state.tokens<=CAP
 return state,{'state':state.audit(),'jumps':p['B_audit']['jumps'],'extra_navigation':extras,'protected_ids':sorted(protected&set(initial.lines)),'evicted_ids':evicted,'additions':additions,'source_policy':'B protected core unchanged; only unprotected lexical units may be evicted; missing source explicitly partial'},observation(p['B_audit']['jumps'],extras)
