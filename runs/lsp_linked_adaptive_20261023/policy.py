"""Real LSP traversal, two-sided diff roots and evidence-linked presentation.
No model labels, patches from tests, witness outputs, or name-based target fallback.
"""
import ast, builtins, difflib, heapq, importlib.util, json, re, sys
from pathlib import Path
from urllib.parse import unquote, urlparse
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'runs/lsp_backend_20261001'))
import lsp_client as _lsp
_lsp.SERVER=ROOT/'tools/pyright-lsp-20261023/node_modules/pyright/dist/pyright-langserver.js'
from lsp_client import Client as BaseClient
from ace_review.context_adaptive import digest,token_count
from ace_review.protocol_hypothesis import mask_source
SOURCE_CAP=5200; OBS_CAP=800; QUERY_CAP=12; FIRST_QUERIES=6; DEPTH=3
class Client(BaseClient):
 def references(self,file,line,char):
  if file not in self.opened:
   p=self.root/file;self.send({'jsonrpc':'2.0','method':'textDocument/didOpen','params':{'textDocument':{'uri':p.as_uri(),'languageId':'python','version':1,'text':p.read_text()}}});self.opened.add(file)
  return self.request('textDocument/references',{'textDocument':{'uri':(self.root/file).as_uri()},'position':{'line':line,'character':char},'context':{'includeDeclaration':False}})
def index(src):
 defs={};owners={};trees={}
 for f,s in src.items():
  try: tree=ast.parse(s)
  except SyntaxError:continue
  trees[f]=tree
  def visit(nodes,prefix='',parent=()):
   for n in nodes:
    if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
     t=(f,prefix+n.name);defs[t]=n;owners[t]=parent
     visit(n.body,prefix+n.name+'.',parent+(n,))
    else:
     for field in ['body','orelse','finalbody']:
      v=getattr(n,field,None)
      if isinstance(v,list):visit(v,prefix,parent)
     if isinstance(n,ast.Try):
      for h in n.handlers:visit(h.body,prefix,parent)
  visit(tree.body)
 return defs,owners,trees

def changed_roots(base,head,bd,hd):
 roots=set();deleted=set();events=[]
 def containing(defs,f,lo,hi):
  ns=[(t,n) for t,n in defs.items() if t[0]==f and not isinstance(n,ast.ClassDef) and n.lineno<=hi and n.end_lineno>=lo]
  # All functions containing actual edits, including enclosing lexical bindings.
  return {t for t,n in ns if not any(m.lineno<=lo and m.end_lineno>=hi and m.end_lineno-m.lineno<n.end_lineno-n.lineno for _,m in ns)}
 for f in sorted(set(base)|set(head)):
  if base.get(f)==head.get(f):continue
  a=base.get(f,'').splitlines();b=head.get(f,'').splitlines()
  for op,a0,a1,b0,b1 in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
   if op=='equal':continue
   hs=containing(hd,f,b0+1,b1) if b1>b0 else set()
   bs=containing(bd,f,a0+1,a1) if a1>a0 else set()
   for t in bs:
    if t in hd:hs.add(t)
    else:deleted.add(t)
   roots|=hs
   events.append({'file':f,'operation':op,'base_lines':[a0+1,a1],'head_lines':[b0+1,b1],'head_roots':sorted(hs),'base_only_roots':sorted(bs-set(hd))})
 return sorted(roots),sorted(deleted),events

def position(src,n):
 row=src.splitlines()[n.lineno-1];prefix=row.encode()[:n.col_offset].decode()
 if isinstance(n,ast.Attribute):
  row=src.splitlines()[n.end_lineno-1];prefix=row.encode()[:n.end_col_offset].decode()[:-len(n.attr)];line=n.end_lineno-1
 else:line=n.lineno-1
 return line,len(prefix.encode('utf-16-le'))//2

def package(src,defs,owners,trees,t):
 n=defs[t];lines=set()
 def header(x):
  lo=min([x.lineno]+[d.lineno for d in x.decorator_list]);lines.update(range(lo,x.body[0].lineno))
 for o in owners[t]:header(o)
 if isinstance(n,ast.ClassDef):
  header(n)
  for x in n.body:
   if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and x.name not in {'__init__','__call__'}:continue
   if isinstance(x,ast.ClassDef):continue
   lo=min([x.lineno]+[d.lineno for d in getattr(x,'decorator_list',[])])
   lines.update(range(lo,x.end_lineno+1))
 else:lines.update(range(min([n.lineno]+[d.lineno for d in n.decorator_list]),n.end_lineno+1))
 used={x.id for x in ast.walk(n) if isinstance(x,ast.Name)}
 for x in trees[t[0]].body:
  if isinstance(x,(ast.Import,ast.ImportFrom)) and any((a.asname or a.name.split('.')[0]) in used for a in x.names):lines.update(range(x.lineno,x.end_lineno+1))
 return lines

def render(src,lines,version='head'):
 out=[]
 for f,ls in sorted(lines.items()):
  if not ls:continue
  out.append(f'[E-{version}-{digest(f)[:10]}] {version} {f}; numbered supplied lines only; omitted code unknown')
  rows=mask_source(src[f]).splitlines();prev=None
  for ln in sorted(ls):
   if prev is not None and ln!=prev+1:out.append('... omitted ...')
   out.append(f'{ln}: {rows[ln-1]}');prev=ln
 return '\n'.join(out)

class Navigator:
 def __init__(self,base,head,root,scope):
  self.base=base;self.head=head;self.root=Path(root);self.scope=scope
  self.defs,self.owners,self.trees=index(head);self.bd,self.bo,self.bt=index(base)
  self.roots,self.deleted,self.root_events=changed_roots(base,head,self.bd,self.defs)
  self.lines={};self.base_lines={};self.admitted=[];self.blocked=[];self.queue=[];self.seq=0;self.queued=set();self.seen=set();self.queries=[];self.requests=[];self.bindings=[]
  words=set(re.findall(r'[a-z_]\w*',scope.lower()))
  self.roots.sort(key=lambda t:(-len(words&set(re.findall(r'[a-z_]\w*',(t[1]+' '+ast.unparse(self.defs[t])).lower()))),t))
  for t in self.deleted:self.admit(t,'deleted_base',base=True)
  for t in self.roots:
   if self.admit(t,'changed_root'):self.enqueue(t,0)
 def source(self):return render(self.base,self.base_lines,'base')+'\n'+render(self.head,self.lines)
 def admit(self,t,origin,base=False):
  key=('base' if base else 'head',*t)
  if any(tuple(a['key'])==key for a in self.admitted):return True
  src,defs,owners,trees,lines=(self.base,self.bd,self.bo,self.bt,self.base_lines) if base else (self.head,self.defs,self.owners,self.trees,self.lines)
  add=package(src,defs,owners,trees,t);before=set(lines.get(t[0],set()));lines[t[0]]=before|add
  if token_count(self.source())>SOURCE_CAP:
   lines[t[0]]=before;self.blocked.append({'key':key,'origin':origin,'reason':'whole package exceeds source cap'});return False
  self.admitted.append({'key':key,'origin':origin,'lines':sorted(add)});return True
 def push(self,method,f,ln,ch,symbol,depth,priority,origin):
  if depth>DEPTH:return
  heapq.heappush(self.queue,(priority,self.seq,method,f,ln,ch,symbol,depth,origin));self.seq+=1
 def enqueue(self,t,depth):
  if t in self.queued or depth>DEPTH:return
  self.queued.add(t);f,s=t;n=self.defs[t];row=self.head[f].splitlines()[n.lineno-1];m=re.search(r'\b(?:def|class)\s+('+re.escape(n.name)+r')\b',row)
  if m:self.push('references',f,n.lineno-1,len(row[:m.start(1)].encode('utf-16-le'))//2,s,depth,(1 if depth==0 else 4,depth,n.lineno),'declaration_references')
  returns={id(x) for r in ast.walk(n) if isinstance(r,ast.Return) for x in ast.walk(r) if isinstance(x,ast.Call)}
  for c in ast.walk(n):
   if not isinstance(c,ast.Call) or not isinstance(c.func,(ast.Name,ast.Attribute)):continue
   if isinstance(c.func,ast.Name) and c.func.id in vars(builtins):continue
   if isinstance(c.func,ast.Attribute) and isinstance(c.func.value,(ast.Constant,ast.List,ast.Tuple,ast.Dict,ast.Set)):continue
   ln,ch=position(self.head[f],c.func);changed=any(e['file']==f and e['head_lines'][0]<=c.lineno<=e['head_lines'][1] for e in self.root_events)
   lane=0 if changed and depth==0 else 3 if depth==0 else 2
   self.push('definition',f,ln,ch,ast.unparse(c.func),depth,(lane,0 if id(c) in returns else 1,ln),'call_expression')
 def enclosing(self,f,line):
  matches=[(t,n) for t,n in self.defs.items() if t[0]==f and n.lineno<=line<=n.end_lineno]
  return min(matches,key=lambda x:(x[1].end_lineno-x[1].lineno,x[0]))[0] if matches else None
 def callback(self,t,f,ln,ch,depth):
  n=self.defs[t]
  if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):return
  params=list(n.args.posonlyargs)+list(n.args.args)
  if params and params[0].arg in {'self','cls'} and self.owners[t] and isinstance(self.owners[t][-1],ast.ClassDef) and not any(isinstance(d,ast.Name) and d.id=='staticmethod' for d in n.decorator_list):params=params[1:]
  names={x.func.id for x in ast.walk(n) if isinstance(x,ast.Call) and isinstance(x.func,ast.Name)}
  calls=[x for x in ast.walk(self.trees[f]) if isinstance(x,ast.Call) and isinstance(x.func,(ast.Name,ast.Attribute)) and position(self.head[f],x.func)==(ln,ch)]
  for c in calls:
   vals={} if any(isinstance(x,ast.Starred) for x in c.args) else {p.arg:v for p,v in zip(params,c.args)}
   vals.update({k.arg:k.value for k in c.keywords if k.arg})
   for name,v in vals.items():
    if name not in names or not isinstance(v,(ast.Name,ast.Attribute)):continue
    vl,vc=position(self.head[f],v)
    if vl+1 not in self.lines.get(f,set()):continue
    self.bindings.append({'parameter':name,'callee':t,'caller_file':f,'line':c.lineno,'argument':ast.unparse(v),'kind':'syntactic argument association; runtime binding unknown'})
    self.push('definition',f,vl,vc,ast.unparse(v),depth,(2,-1,vl),'native_callsite_callback_argument')
 def add_requests(self,requests):
  for r in requests[:6]:
   record=dict(r);self.requests.append(record)
   f=r.get('file');ln=r.get('line',0);col=r.get('column',0);method=r.get('operation')
   if method not in {'definition','references'} or f not in self.head or ln not in self.lines.get(f,set()) or col<1:
    record['accepted']=False;record['reason']='request must target visible head source and supported native operation';continue
   row=self.head[f].splitlines()[ln-1]
   if col>len(row) or not (row[col-1].isalnum() or row[col-1]=='_'):
    record['accepted']=False;record['reason']='column is not an identifier position';continue
   ch=len(row[:col-1].encode('utf-16-le'))//2
   if (method,f,ln-1,ch) in self.seen:
    record['accepted']=False;record['reason']='already queried';continue
   record['accepted']=True;self.push(method,f,ln-1,ch,row[col-1:],1,(-1,0,ln),'model_gap_request')
 def advance(self,client,limit):
  while self.queue and len(self.queries)<limit:
   pri,seq,method,f,ln,ch,symbol,depth,origin=heapq.heappop(self.queue);key=(method,f,ln,ch)
   if key in self.seen:continue
   self.seen.add(key);raw=None;targets=[];excluded=[];error=None;sites=[]
   try:
    raw=getattr(client,method)(f,ln,ch)
    for loc in raw or []:
     uri=loc.get('uri',loc.get('targetUri'));rng=loc.get('range',loc.get('targetSelectionRange'))
     try:tf=str(Path(unquote(urlparse(uri).path)).relative_to(client.root))
     except (TypeError,ValueError):excluded.append('outside workspace');continue
     if tf not in self.head:excluded.append('outside production corpus');continue
     tl=rng['start']['line']+1;t=self.enclosing(tf,tl);sites.append((tf,tl-1,rng['start']['character']))
     if t:
      targets.append(t)
      if self.admit(t,'native_'+method):self.enqueue(t,depth+1)
      if method=='definition':self.callback(t,f,ln,ch,depth+1)
     else:excluded.append({'file':tf,'line':tl,'reason':'native result outside callable/class declaration'})
    if method=='references':
     t=self.enclosing(f,ln+1)
     if t:
      for tf,tl,tc in sites:self.callback(t,tf,tl,tc,depth+1)
   except Exception as exc:error=repr(exc)
   self.queries.append({'operation':method,'file':f,'line':ln+1,'character_utf16':ch,'symbol':symbol,'depth':depth,'origin':origin,'targets':list(dict.fromkeys(targets)),'excluded':excluded,'error':error,'raw':raw})
 def observations(self,linked):
  out=['[E-COVERAGE] Static source evidence only. Native definition/reference targets and argument associations do not certify runtime execution.']
  if linked:
   for i,q in enumerate(self.queries):
    row=f'[E-NAV-{i+1}] {q["operation"]} at {q["file"]}:{q["line"]}; returned locations are contained in '+('; '.join(f'{f}::{n}' for f,n in q['targets']) or 'unresolved')
    if token_count('\n'.join(out+[row]))<650:out.append(row)
   for b in self.bindings:
    row=f'Argument association: {b["caller_file"]}:{b["line"]} passes {b["argument"]} as {b["parameter"]} to {b["callee"]}; runtime unresolved.'
    if token_count('\n'.join(out+[row]))<740:out.append(row)
  out.append(f'Changed roots: {len(self.roots)}; deleted base-only roots: {len(self.deleted)}; blocked whole packages: {len(self.blocked)}; pending queries: {len(self.queue)}. Missing source is unknown, not evidence of absence.')
  text='\n'.join(out);assert token_count(text)<=OBS_CAP;return text
 def audit(self):
  return {'roots':self.roots,'deleted_roots':self.deleted,'root_events':self.root_events,'source_lines':{f:sorted(v) for f,v in self.lines.items()},'base_lines':{f:sorted(v) for f,v in self.base_lines.items()},'source_tokens':token_count(self.source()),'admitted':self.admitted,'blocked':self.blocked,'queries':self.queries,'requests':self.requests,'bindings':self.bindings,'pending':len(self.queue),'root_coverage':all(any(tuple(a['key'])==('head',*t) for a in self.admitted) for t in self.roots) and all(any(tuple(a['key'])==('base',*t) for a in self.admitted) for t in self.deleted),'has_roots':bool(self.roots or self.deleted)}
