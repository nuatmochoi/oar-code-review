"""Public-diff-directed staged context and bounded native questions.

No benchmark labels are imported. Definitions/references are static candidates;
source bodies and lexical value facts retain that scope. Evidence objects are
immutable physical source identities; how they were selected is an event log.
"""
from __future__ import annotations
import ast,copy,dataclasses,difflib,hashlib,importlib,json,os,re,subprocess,sys,time
from pathlib import Path,PurePosixPath
from urllib.parse import urlparse,unquote
from cache_isolation import isolation_environment,sandbox_command

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[1]
PRODUCT=PROJECT/'runs/oar_product_pilot_v3_20261026/product_snapshot_20261026'
GO_PARSER=PROJECT/'runs/causal_route_separation_20261103/go_flow/go-flow'
GOPLS=PROJECT/'tools/bin/gopls'
PYRIGHT=PROJECT/'tools/pyright-lsp-20261023/node_modules/.bin/pyright-langserver'
SOURCE_KINDS={'source_lines','implementation_body','exception_branch','body'}
DEFAULTS=dict(max_source_tokens=12000,max_native_calls=48,max_native_seconds=180.,
 max_file_reads=256,max_query_seconds=20.,max_wall_seconds=900.,max_file_bytes=16000000,
 max_metadata_tokens=2000,max_seed_questions=6,max_scip_candidates=24,max_scip_scan_candidates=96,
 max_ast_files=12,max_ast_units=24,max_seed_files=12,min_stage_headroom_tokens=64,
 ast_source_tokens=3000,lsp_source_tokens=6000,scip_source_tokens=9000)

def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def tokens(s):
 import tiktoken
 return len(tiktoken.get_encoding('o200k_base').encode(s,disallowed_special=()))
def lines(text):return text.splitlines()
def put(path,value):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
def safe_rel(value):
 if not isinstance(value,str)or not value or '\\'in value:raise ValueError('invalid_source_path')
 p=PurePosixPath(value)
 if p.is_absolute()or'..'in p.parts:raise ValueError('source_path_escape')
 return str(p)
def source_rows(evidence):
 out={}
 for e in evidence:
  if e.get('kind')not in SOURCE_KINDS:continue
  for n,text in enumerate(lines(e['content']),e['start_line']):
   key=e['snapshot'],e['path'],n
   if key in out and out[key]!=text:raise ValueError('conflicting_delivered_source_rows')
   out[key]=text
 return out
def source_tokens(evidence):
 chunks=[];part=[];last=None
 for key,text in sorted(source_rows(evidence).items()):
  if last and key!=(last[0],last[1],last[2]+1):chunks.append('\n'.join(part));part=[]
  part.append(text);last=key
 if part:chunks.append('\n'.join(part))
 return sum(tokens(s)for s in chunks)
def changed_lines(diff):
 out={'BASE':{},'HEAD':{}};old=new=None;a=b=0
 for row in diff.splitlines():
  if row.startswith('--- '):old=row[4:].removeprefix('a/').split('\t')[0];old=None if old=='/dev/null'else old
  elif row.startswith('+++ '):new=row[4:].removeprefix('b/').split('\t')[0];new=None if new=='/dev/null'else new
  elif row.startswith('@@'):
   m=re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)',row)
   if m:a,b=map(int,m.groups())
  elif row.startswith('-')and not row.startswith('---'):
   if old:out['BASE'].setdefault(old,set()).add(a)
   if new:out['HEAD'].setdefault(new,set()).add(max(1,b))
   a+=1
  elif row.startswith('+')and not row.startswith('+++'):
   if new:out['HEAD'].setdefault(new,set()).add(b)
   if old:out['BASE'].setdefault(old,set()).add(max(1,a))
   b+=1
  elif row.startswith(' '):a+=1;b+=1
 return out

class Limit(RuntimeError):pass
class SCIPDisabled(Limit):pass

class ContextCollector:
 def __init__(self,case,out_dir,budgets=None,*,initial_evidence=None,initial_state=None,native_provider=None):
  self.case=copy.deepcopy(case);self.out=Path(out_dir).resolve()
  if not self.out.is_relative_to(ROOT):raise ValueError('dynamic_query_output_outside_new_run')
  self.out.mkdir(parents=True,exist_ok=True)
  self.budgets={**DEFAULTS,**(budgets or{})};self.started=time.monotonic();self.query_started=self.started
  self.language=case['language'];self.snapshots={};self.native_provider=native_provider;self.clients={};self.cache={}
  for snapshot,s in case.get('snapshots',{}).items():
   if isinstance(s,str):s=json.loads(Path(s).read_text())
   if 'source_manifest'in s and 'files'not in s:
    p=s['source_manifest'];v=json.loads(Path(p).read_text());s={**v,**s}
   self.snapshots[snapshot]=s
  self.evidence=copy.deepcopy(initial_evidence if initial_evidence is not None else(initial_state or{}).get('evidence',[]));source_rows(self.evidence)
  if source_tokens(self.evidence)>self.budgets['max_source_tokens']:raise ValueError('initial_source_exceeds_global_cap')
  self.events=[];self.unresolved=[];self.raw_paths=[];self.stages={};self.stage_order=[];self.stage_exports={};self.sequence=0;self.active_source_cap=self.budgets['max_source_tokens']
  self.stats=dict(file_reads=0,source_bytes_read=0,ast_parses=0,go_ast_invocations=0,python_ast_external_invocations=0,native_calls=0,native_seconds=0.,query_calls=0,body_units_admitted=0,body_units_rejected=0,scip_index_reads=0,scip_index_bytes=0,native_lookup_cache_hits=0)
  self.native_lookup_cache={}
  self.changed=changed_lines(case.get('diff',''))
  self.closed=False;self.external_bindings={s:{}for s in self.snapshots}
  if initial_state:
   for k,v in initial_state.get('cost',{}).items():
    if k in self.stats:self.stats[k]=v
   self.external_bindings=copy.deepcopy(initial_state.get('external_source_bindings',self.external_bindings))
   self.stages=copy.deepcopy(initial_state.get('stages',{}));self.stage_order=list(self.stages)
   if self.stage_order:self.stage_exports[self.stage_order[-1]]=copy.deepcopy(initial_state)
   self.events=[{'event':'frozen_context_resumed','delivery_sha256':initial_state.get('delivery_sha256'),'carried_logical_cost':copy.deepcopy(self.stats)}]
   self.unresolved=copy.deepcopy(initial_state.get('unresolved',[]))
 @staticmethod
 def language_for(path):return 'Python'if str(path).endswith('.py')else'Go'if str(path).endswith('.go')else None
 def _check(self,native=0):
  if time.monotonic()-self.started>self.budgets['max_wall_seconds']:raise Limit('context_wall_budget')
  if time.monotonic()-self.query_started>self.budgets['max_query_seconds']:raise Limit('context_query_time_budget')
  if self.stats['native_calls']+native>self.budgets['max_native_calls']:raise Limit('native_call_budget')
  if self.stats['native_seconds']>=self.budgets['max_native_seconds']:raise Limit('native_time_budget')
 def _timeout(self):
  return max(.001,min(self.budgets['max_query_seconds']-(time.monotonic()-self.query_started),self.budgets['max_native_seconds']-self.stats['native_seconds'],self.budgets['max_wall_seconds']-(time.monotonic()-self.started)))
 def _target(self,snapshot,path):
  path=safe_rel(path);spec=self.snapshots.get(snapshot)
  if not spec:raise ValueError('snapshot_unavailable')
  root=Path(spec['root']).resolve()
  if path in spec.get('files',{}):
   p=(root/path).resolve()
   if not p.is_relative_to(root):raise ValueError('source_symlink_outside_pool')
   return p,spec['files'][path],{'status':'repository_snapshot','commit':spec.get('commit'),'runtime_dispatch':'unresolved'}
  external=spec.get('external_files',{}).get(path)or self.external_bindings.get(snapshot,{}).get(path)
  if external:
   p=Path(external['absolute_source']).resolve();status=external.get('binding_status','environment_assumption')
   if status=='confirmed'and not external.get('binding_evidence'):status='environment_assumption'
   return p,external['sha256'],{'status':status,'origin':external.get('origin'),'version':external.get('version'),'binding_evidence':external.get('binding_evidence',[]),'assumptions':external.get('assumptions',[]),'authority_reference':external.get('authority_reference'),'runtime_dispatch':'unresolved'}
  raise ValueError('source_outside_common_allowed_pool')
 def _read(self,snapshot,path):
  self._check()
  if self.stats['file_reads']>=self.budgets['max_file_reads']:raise Limit('file_read_budget')
  p,h,binding=self._target(snapshot,path);raw=p.read_bytes();self.stats['file_reads']+=1;self.stats['source_bytes_read']+=len(raw)
  if len(raw)>self.budgets['max_file_bytes']:raise Limit('source_file_byte_cap')
  if hashlib.sha256(raw).hexdigest()!=h:raise ValueError('source_manifest_hash_mismatch')
  text=raw.decode('utf-8');return {'path':str(p),'sha256':h,'text':text,'rows':lines(text),'binding':binding}
 def _parse(self,snapshot,path):
  data=self._read(snapshot,path);key=snapshot,path,data['sha256']
  if key in self.cache:return self.cache[key]
  units=[];calls=[];tree=None
  if path.endswith('.py'):
   try:tree=ast.parse(data['text'],filename=path)
   except SyntaxError:tree=self._python_ast_fallback(snapshot,path,data)
   parents={child:p for p in ast.walk(tree)for child in ast.iter_child_nodes(p)}
   for n in ast.walk(tree):
    if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
     start=min([n.lineno]+[d.lineno for d in n.decorator_list]);row=data['rows'][n.lineno-1];m=re.search(r'\b'+re.escape(n.name)+r'\b',row)
     units.append({'name':n.name,'start_line':start,'end_line':n.end_lineno,'name_line':n.lineno,'name_column':m.start()+1 if m else 1,'kind':'class'if isinstance(n,ast.ClassDef)else'function','is_method':isinstance(parents.get(n),ast.ClassDef),'node':n})
    if isinstance(n,ast.Call):
     name=n.func.attr if isinstance(n.func,ast.Attribute)else n.func.id if isinstance(n.func,ast.Name)else''
     if name:
      line=n.func.end_lineno;column=n.func.end_col_offset-len(name.encode('utf-8'))+1
      calls.append({'name':name,'line':line,'column':column,'start_line':n.lineno,'end_line':n.end_lineno,'node':n})
   for n in tree.body:
    if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))and hasattr(n,'end_lineno'):
     units.append({'name':'module_statement@'+str(n.lineno),'start_line':n.lineno,'end_line':n.end_lineno,'name_line':None,'name_column':None,'kind':'statement','node':n})
   self.stats['ast_parses']+=1
  elif path.endswith('.go'):
   self._check(1);self.stats['native_calls']+=1;self.stats['go_ast_invocations']+=1;start=time.monotonic()
   try:
    p=subprocess.run(sandbox_command([str(GO_PARSER)]),input=json.dumps({'path':data['path']}),text=True,capture_output=True,timeout=self._timeout(),check=True,env=self._environment(snapshot,'Go'));value=json.loads(p.stdout)
   finally:self.stats['native_seconds']+=time.monotonic()-start
   if value.get('sha256')!=data['sha256']:raise ValueError('Go_AST_source_hash_mismatch')
   if value.get('errors'):raise ValueError('Go_AST_parse_error:'+str(value['errors']))
   self.stats['ast_parses']+=1
   for u in value.get('units',[]):
    ns=u.get('name_span')or{};units.append({**u,'name_line':ns.get('start_line'),'name_column':ns.get('start_column'),'kind':u.get('kind','function')})
    calls.extend(u.get('calls')or[])
   tree=value
  else:raise ValueError('unsupported_actual_file_language')
  result={**data,'units':units,'calls':calls,'tree':tree};self.cache[key]=result;return result
 def _python_ast_fallback(self,snapshot,path,data):
  profile=self._profile(snapshot,'Python');python=profile.get('python_executable',profile.get('python',self.case.get('python_executable')))
  if not python:raise ValueError('newer_Python_syntax_without_declared_analysis_interpreter')
  program='''import ast,json,sys,hashlib
raw=open(sys.argv[1],'rb').read()
def enc(value):
 if isinstance(value,ast.AST):return {'_type':type(value).__name__,'fields':{k:enc(v)for k,v in ast.iter_fields(value)},'attrs':{k:getattr(value,k)for k in ('lineno','col_offset','end_lineno','end_col_offset')if hasattr(value,k)}}
 if isinstance(value,list):return [enc(v)for v in value]
 if isinstance(value,(str,int,float,bool))or value is None:return value
 return {'_literal_repr':repr(value)}
print(json.dumps({'sha256':hashlib.sha256(raw).hexdigest(),'python':sys.version,'tree':enc(ast.parse(raw,filename=sys.argv[1]))},ensure_ascii=False))'''
  self._check(1)
  if self.stats['file_reads']>=self.budgets['max_file_reads']:raise Limit('Python_AST_helper_file_read_budget')
  self.stats['native_calls']+=1;self.stats['python_ast_external_invocations']+=1;self.stats['file_reads']+=1;self.stats['source_bytes_read']+=len(data['text'].encode());start=time.monotonic()
  try:
   proc=subprocess.run(sandbox_command([str(python),'-B','-c',program,data['path']]),text=True,capture_output=True,timeout=self._timeout(),env=self._environment(snapshot,'Python'))
   if proc.returncode:raise ValueError('declared_Python_AST_parse_failed:'+proc.stderr[-500:])
   value=json.loads(proc.stdout)
   if value['sha256']!=data['sha256']:raise ValueError('declared_Python_AST_source_hash_mismatch')
   folder=self.out/'ast';folder.mkdir(exist_ok=True);record=folder/(snapshot+'-'+digest(path)[:12]+'.json')
   put(record,{'path':path,'snapshot':snapshot,'python':str(python),'program_sha256':hashlib.sha256(program.encode()).hexdigest(),'result':value,'semantic_scope':'Static AST parsing only; no target import/exec/runtime probe.'});self.raw_paths.append(str(record))
  finally:self.stats['native_seconds']+=time.monotonic()-start
  def dec(v):
   if isinstance(v,list):return [dec(x)for x in v]
   if not isinstance(v,dict):return v
   if '_literal_repr'in v:return None
   cls=getattr(ast,v['_type'],None);node=cls()if cls else ast.AST()
   if cls is None:node._fields=tuple(v['fields'])
   for k,x in v['fields'].items():setattr(node,k,dec(x))
   for k,x in v['attrs'].items():setattr(node,k,x)
   return node
  return dec(value['tree'])
 def _unit_at(self,data,line,*,definition=False,name=None):
  choices=[u for u in data['units']if u['start_line']<=line<=u['end_line']]
  if definition:choices=[u for u in choices if u['name_line']==line and u['kind']in('function','class')and(not name or u['name']==name)]
  return min(choices,key=lambda u:(u['end_line']-u['start_line'],u['start_line']))if choices else None
 def _admit(self,e,reason):
  if any(x['id']==e['id']for x in self.evidence):
   self.events.append({'event':'existing_evidence_reused','id':e['id'],'selection_reason':reason});return e
  if e['kind']in SOURCE_KINDS:
   count=source_tokens(self.evidence+[e]);cap=min(self.active_source_cap,self.budgets['max_source_tokens'])
   if count>cap:
    self.stats['body_units_rejected']+=1;self.unresolved.append({'reason':'whole_body_source_token_cap','path':e['path'],'start_line':e['start_line'],'end_line':e['end_line'],'unit_tokens':tokens(e['content']),'union_tokens_if_added':count,'stage_cap':cap,'truncation_performed':False});return None
  else:
   count=sum(tokens(x['content'])for x in self.evidence if x['kind']not in SOURCE_KINDS)+tokens(e['content'])
   if count>self.budgets['max_metadata_tokens']:
    self.unresolved.append({'reason':'metadata_token_cap','kind':e['kind'],'metadata_tokens_if_added':count});return None
  self.evidence.append(e);self.events.append({'event':'evidence_admitted','id':e['id'],'selection_reason':reason,'source_tokens_after':source_tokens(self.evidence)})
  if e['kind']in SOURCE_KINDS:self.stats['body_units_admitted']+=1
  return e
 def _body(self,snapshot,path,unit,reason,*,relation='complete_static_source_unit'):
  data=self._read(snapshot,path);a,b=unit['start_line'],unit['end_line']
  if not 1<=a<=b<=len(data['rows']):raise ValueError('body_range_outside_physical_file')
  content='\n'.join(data['rows'][a-1:b]);physical=[{'line':n,'text':t}for n,t in enumerate(data['rows'][a-1:b],a)]
  e={'snapshot':snapshot,'path':path,'start_line':a,'end_line':b,'content':content,'kind':'implementation_body'if unit['kind']in('function','class','closure')else'source_lines','source_representation':'actual_unmasked_snapshot_lines','mechanical_checks':{'complete_unit':True,'unit_kind':unit['kind'],'source_hash_verified':True,'authoritative_file_sha256':data['sha256'],'raw_sha256':hashlib.sha256(content.encode()).hexdigest(),'actual_range':[a,b],'range_exact':True,'semantic_truth_guaranteed':False},'provenance':{'provider':'authoritative_snapshot_read','file_sha256':data['sha256'],'source_representation':'actual_unmasked_snapshot_lines'},'binding':data['binding'],'physical_rows':physical}
  e['id']='E-'+digest({k:e[k]for k in('snapshot','path','start_line','end_line','content','kind','mechanical_checks')})[:20]
  return self._admit(e,{'reason':reason,'relation':relation,'runtime_dispatch_confirmed':False})
 def _metadata(self,snapshot,path,line,kind,content,reason):
  text=json.dumps(content,ensure_ascii=False,separators=(',',':'))
  if tokens(text)>650:
   self.unresolved.append({'reason':'whole_metadata_unit_too_large','kind':kind,'path':path});return None
  e={'snapshot':snapshot,'path':path,'start_line':line,'end_line':line,'content':text,'kind':kind,'mechanical_checks':{'complete_unit':False,'semantic_truth_guaranteed':False},'provenance':{'provider':'typed_static_metadata'},'binding':{'runtime_dispatch':'unresolved'}}
  e['id']='M-'+digest(e)[:20];return self._admit(e,reason)
 def _emit_unit(self,snapshot,path,unit,reason,relation):
  body=self._body(snapshot,path,unit,reason,relation=relation)
  if body:return [body]
  # A class may be too large. Constructor bodies are explicit partial class
  # evidence, never silently described as the full requested class body.
  if unit.get('kind')=='class':
   data=self._parse(snapshot,path);node=unit.get('node');direct=set(id(n)for n in getattr(node,'body',[]))
   constructors=[u for u in data['units']if u['name']in('__new__','__init__')and id(u.get('node'))in direct]
   out=[]
   for u in sorted(constructors,key=lambda x:(x['name']!='__new__',x['start_line'])):
    b=self._body(snapshot,path,u,reason,relation='constructor_body_candidate_not_full_class_or_runtime_dispatch')
    if b:out.append(b)
   if out:self.unresolved.append({'reason':'requested_class_body_partial_constructor_only','snapshot':snapshot,'path':path,'class_name':unit['name']})
   return out
  return []

 def _profile(self,snapshot,language):
  spec=self.snapshots[snapshot];profiles=spec.get('native_profiles')
  if profiles is not None:
   if language not in profiles:raise ValueError('native_profile_unavailable_for_actual_file_language')
   return profiles[language]
  if language!=self.language:raise ValueError('secondary_language_native_profile_unavailable')
  return spec
 def _environment(self,snapshot,language,module=None):
  spec=self.snapshots[snapshot];env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1');env.pop('GOWORK',None)
  profile=self._profile(snapshot,language)
  for k,v in{**profile.get('native_env',profile.get('environment',{})),**self.case.get('native_env',{})}.items():
   if v is None:env.pop(k,None)
   else:env[k]=str(v)
  env.update(isolation_environment())
  if language=='Go'and module is not None:
   root=Path(spec['root']).resolve();relative=str(Path(module).resolve().relative_to(root))
   pin=profile.get('pinned_toolchains',{}).get(relative)
   if not pin:raise ValueError('recorded_Go_toolchain_unavailable_for_actual_module')
   env['GOROOT']=pin['GOROOT'];env['PATH']=str(Path(pin['go_binary']).parent)+os.pathsep+env.get('PATH','')
   for key in ('GOOS','GOARCH','CGO_ENABLED'):env[key]=pin[key]
  return env
 def _module_root(self,snapshot,path):
  spec=self.snapshots[snapshot];root=Path(spec['root']).resolve()
  if self.language_for(path)!='Go':return root
  if path.startswith('__external__/'):
   open_roots=[Path(k[1])for k in self.clients if k[0]==snapshot and k[2]=='Go']
   if open_roots:return open_roots[0]
   modules=sorted((n for n in spec.get('files',{})if n=='go.mod'or n.endswith('/go.mod')),key=lambda n:(len(PurePosixPath(n).parts),n))
   if modules:return root/str(PurePosixPath(modules[0]).parent)
   raise ValueError('no_Go_analysis_workspace_for_external_source')
  p=PurePosixPath(path).parent
  while True:
   name=str(p/'go.mod')
   if name in spec.get('files',{}):return root/str(p)
   if str(p)=='.':break
   p=p.parent
  raise ValueError('no_Go_module_for_actual_file')
 def _client(self,snapshot,path):
  language=self.language_for(path)
  if language is None:raise ValueError('unsupported_actual_file_language')
  profile=self._profile(snapshot,language);module=self._module_root(snapshot,path).resolve();key=snapshot,str(module),language
  if key in self.clients:return self.clients[key]
  self._check(2)
  existing=sys.modules.get('review_agent')
  if existing and not Path(existing.__file__).resolve().is_relative_to(PRODUCT.resolve()):raise RuntimeError('different_product_package_already_loaded')
  if str(PRODUCT)not in sys.path:sys.path.insert(0,str(PRODUCT))
  m=importlib.import_module('review_agent.lsp.client');py=profile.get('python_executable',profile.get('python',self.case.get('python_executable')))
  settings={'analysis':{'diagnosticMode':'workspace','useLibraryCodeForTypes':True,'autoSearchPaths':True}}
  if py:settings['pythonPath']=py
  command=sandbox_command([str(PYRIGHT if language=='Python'else GOPLS),'--stdio'if language=='Python'else'serve'])
  config=m.LspServerConfig(language.lower(),command[0],tuple(command[1:]),workspace_settings=(('python',settings),)if language=='Python'else())
  folder=self.out/'native'/snapshot/digest([str(module),language])[:12];folder.mkdir(parents=True,exist_ok=True)
  client=m.LspClient(module,config,timeout_seconds=self._timeout(),initialize_timeout_seconds=self._timeout(),environment=self._environment(snapshot,language,module))
  original=client._request;rpc_count=[0]
  def request(method,params,*,timeout=None):
   self._check(1);self.stats['native_calls']+=1;rpc_count[0]+=1;start=time.monotonic();row={'method':method,'params':params}
   try:
    value=original(method,params,timeout=min(self._timeout(),timeout or self._timeout()));row['result']=value;return value
   except Exception as e:row['error']=type(e).__name__+': '+str(e);raise
   finally:
    row['seconds']=time.monotonic()-start;self.stats['native_seconds']+=row['seconds'];p=folder/f'rpc-{rpc_count[0]:04d}.json';put(p,row);self.raw_paths.append(str(p))
  client._request=request
  self.clients[key]=client
  client.start();return client
 def _anchor(self,q,data):
  line=q.get('line',0);name=q.get('symbol','').rsplit('.',1)[-1]
  if not 1<=line<=len(data['rows']):raise ValueError('query_line_outside_actual_file')
  row=data['rows'][line-1];column=q.get('column',0)
  if column:
   # Public backend columns are one-based UTF-8 bytes, as Go/Python AST.
   prefix=row.encode()[:column-1].decode();tail=row.encode()[column-1:].decode();m=re.match(r'[^\W\d]\w*',tail)
   if not m or name and m[0]!=name:raise ValueError('query_column_not_requested_symbol_token')
   name=m[0];return name,len(prefix.encode('utf-16-le'))//2+1
  if not name:raise ValueError('query_requires_actual_symbol_token')
  hits=list(re.finditer(r'(?<!\w)'+re.escape(name)+r'(?!\w)',row))
  if len(hits)!=1:raise ValueError('query_symbol_token_missing_or_ambiguous')
  return name,len(row[:hits[0].start()].encode('utf-16-le'))//2+1
 def _native(self,q,operation):
  # Evidence kind is intentionally absent: metadata-only lookup can later be
  # enriched with an AST body without repeating the identical native RPC.
  key=(q['snapshot'],q['file'],q.get('line',0),q.get('column',0),operation,q.get('symbol',''))
  if key in self.native_lookup_cache:
   self.stats['native_lookup_cache_hits']+=1
   self.events.append({'event':'native_lookup_cache_reused','key':list(key),'result_sha256':digest(self.native_lookup_cache[key]),'new_native_calls':0,'semantic_runtime_dispatch_proven':False})
   return copy.deepcopy(self.native_lookup_cache[key])
  result=self._native_uncached(q,operation);self.native_lookup_cache[key]=copy.deepcopy(result)
  return result
 def _native_uncached(self,q,operation):
  data=self._read(q['snapshot'],q['file']);name,column=self._anchor(q,data)
  if self.native_provider:
   self._check(1);self.stats['native_calls']+=1;begin=time.monotonic()
   try:return self.native_provider({**q,'symbol':name,'column_utf16':column,'operation':operation})
   finally:self.stats['native_seconds']+=time.monotonic()-begin
  client=self._client(q['snapshot'],q['file'])
  if not client.supports(operation):return {'state':'unsupported','operation':operation,'locations':[],'capabilities':client.server_capabilities}
  module=client.root;absolute=Path(data['path']);rel=str(absolute.relative_to(module))if absolute.is_relative_to(module)else str(absolute)
  m=importlib.import_module('review_agent.code_intelligence.tree_sitter_index')
  symbol=m.StructuralSymbol(path=rel,name=name,kind='actual_public_question_token',line_start=q['line'],line_end=q['line'],column_start=column,column_end=column+len(name),language=self.language_for(q['file']).lower(),source='actual_source_AST_or_verified_token')
  values=getattr(client,operation)(symbol);out=[]
  for v in values:
   row=dataclasses.asdict(v);p=Path(row['path']);p=p if p.is_absolute()else module/p;row['absolute_path']=str(p.resolve());out.append(row)
  return {'state':'completed'if out else'empty','operation':operation,'locations':out,'capabilities':client.server_capabilities}
 def _native_path(self,snapshot,row):
  spec=self.snapshots[snapshot];root=Path(spec['root']).resolve();p=Path(row.get('absolute_path',row['path']))
  if not p.is_absolute():p=root/p
  p=p.resolve()
  # Writable cache paths are analysis aliases of sealed, read-only sources.
  # Compare actual bytes before mapping so a changed clone cannot inherit the
  # old source authority merely through a matching package/path name.
  for alias in spec.get('source_environment_aliases',[]):
   actual_root=Path(alias['actual_root']).resolve()
   if not p.is_relative_to(actual_root):continue
   canonical=Path(alias['canonical_root']).resolve()/p.relative_to(actual_root)
   authorities=list(spec.get('allowed_external_roots',[]))
   for profile in spec.get('native_profiles',{}).values():authorities.extend(profile.get('allowed_external_roots',[]))
   admitted=False
   for authority in authorities:
    allowed=Path(authority['root']).resolve()
    if not canonical.is_relative_to(allowed):continue
    relative=canonical.relative_to(allowed);owned=authority.get('relative_files')
    if owned is not None and str(relative)not in owned:continue
    excluded=set(authority.get('excluded_subdirectories',[]))
    if 'stdlib'in authority.get('kind',authority.get('origin','')):excluded.update(('site-packages','dist-packages'))
    if any(part in excluded for part in relative.parts):continue
    admitted=True;break
   if not admitted:raise ValueError('cache_alias_outside_declared_source_authority')
   self._check()
   if self.stats['file_reads']+2>self.budgets['max_file_reads']:raise Limit('cache_alias_attestation_file_read_budget')
   actual_bytes=p.read_bytes();canonical_bytes=canonical.read_bytes();self.stats['file_reads']+=2;self.stats['source_bytes_read']+=len(actual_bytes)+len(canonical_bytes)
   if max(len(actual_bytes),len(canonical_bytes))>self.budgets['max_file_bytes']:raise Limit('cache_alias_attestation_file_byte_cap')
   actual_sha=hashlib.sha256(actual_bytes).hexdigest()
   if actual_sha!=hashlib.sha256(canonical_bytes).hexdigest():raise ValueError('cloned_source_alias_byte_identity_mismatch')
   self.events.append({'event':'native_environment_path_canonicalized','actual_path':str(p),'canonical_source':str(canonical),'sha256':actual_sha,'identity_reference':alias['identity_reference'],'runtime_dispatch_proven':False})
   p=canonical.resolve();break
  if p.is_relative_to(root):
   rel=str(p.relative_to(root))
   if rel in spec.get('files',{}):return rel
  for rel,x in spec.get('external_files',{}).items():
   if p==Path(x['absolute_source']).resolve():return rel
  authorities=list(spec.get('allowed_external_roots',[]))
  for profile in spec.get('native_profiles',{}).values():authorities.extend(profile.get('allowed_external_roots',[]))
  for authority in authorities:
   declared=authority.get('root',authority.get('path'))
   if not isinstance(declared,str)or not declared:continue
   allowed=Path(declared).resolve()
   if not p.is_relative_to(allowed):continue
   rel=p.relative_to(allowed)
   owned=authority.get('relative_files')
   if owned is not None and str(rel)not in owned:continue
   excluded=set(authority.get('excluded_subdirectories',[]))
   if 'stdlib'in authority.get('kind',authority.get('origin','')):excluded.update(('site-packages','dist-packages'))
   if any(x in excluded for x in rel.parts):continue
   if not p.is_file():raise ValueError('external_native_target_unavailable')
   self._check()
   if self.stats['file_reads']>=self.budgets['max_file_reads']:raise Limit('external_attestation_file_read_budget')
   raw=p.read_bytes();self.stats['file_reads']+=1;self.stats['source_bytes_read']+=len(raw)
   if len(raw)>self.budgets['max_file_bytes']:raise Limit('external_source_file_byte_cap')
   h=hashlib.sha256(raw).hexdigest();suffix=p.name if authority.get('source_kind')=='file'or p==allowed else str(rel);alias='__external__/'+digest(authority)[:12]+'/'+suffix
   old=self.external_bindings.setdefault(snapshot,{}).get(alias)
   if old and old['sha256']!=h:raise ValueError('external_source_changed_after_attestation')
   self.external_bindings[snapshot][alias]={'absolute_source':str(p),'sha256':h,'origin':authority.get('origin',str(allowed)),'version':authority.get('version'),'binding_status':authority.get('binding_status','environment_assumption'),'binding_evidence':authority.get('binding_evidence',[]),'assumptions':authority.get('assumptions',[]),'authority_reference':authority.get('authority_reference',authority.get('reference')),'authority_sha256':digest(authority)}
   return alias
  raise ValueError('native_location_outside_common_allowed_pool')

 def _lookup(self,q,operation):
  native=self._native(q,operation);out=[]
  if native['state']not in('completed','empty'):
   self.unresolved.append({'reason':'native_operation_state','state':native['state'],'operation':operation});return out
  if not native['locations']:self.unresolved.append({'reason':'native_lookup_empty','operation':operation,'file':q['file'],'line':q['line']})
  for row in native['locations'][:6]:
   try:
    path=self._native_path(q['snapshot'],row);line=row['line_start'];rawdata=self._read(q['snapshot'],path)
    if not 1<=line<=len(rawdata['rows']):raise ValueError('native_location_outside_physical_file')
    data=None
    metadata=self._metadata(q['snapshot'],path,line,'reference_edge'if operation=='references'else'definition_location',{'operation':operation,'source_location':{k:row[k]for k in('line_start','line_end','column_start','column_end')if k in row},'static_candidate_only':True,'call_or_dataflow_proven':False},'native_LSP_location')
    if metadata:out.append(metadata)
    want_body=q.get('kind')in('implementation_body','exception_branch','value_path')or operation=='implementation'
    if want_body:
     if path.endswith('.pyi'):
      self.unresolved.append({'reason':'typing_stub_not_actual_implementation','path':path,'line':line});continue
     data=self._parse(q['snapshot'],path)
     unit=self._unit_at(data,line,definition=operation!='references')
     if not unit:
      self.unresolved.append({'reason':'native_target_not_function_or_class_definition'if operation!='references'else'reference_has_no_enclosing_unit','path':path,'line':line});continue
     out.extend(self._emit_unit(q['snapshot'],path,unit,'native_lookup_source','static_reference_consumer_candidate'if operation=='references'else'static_definition_implementation_candidate'))
   except(ValueError,OSError,SyntaxError,Limit,UnicodeError)as e:self.unresolved.append({'reason':'native_location_body_unresolved','operation':operation,'detail':str(e)})
  if len(native['locations'])>6:self.unresolved.append({'reason':'native_candidate_fanout_cap','total':len(native['locations']),'kept':6,'not_single_runtime_target':True})
  return out
 def _python_import_body(self,q):
  if not q['file'].endswith('.py'):return []
  caller=self._parse(q['snapshot'],q['file']);name=q.get('symbol','').rsplit('.',1)[-1]
  if not name:return []
  mappings=[];imports={};binding_names={name}
  for n in ast.walk(caller['tree']):
   if isinstance(n,ast.Import):
    for a in n.names:imports[a.asname or a.name.split('.')[0]]=a.name
   elif isinstance(n,ast.ImportFrom)and n.module and not n.level:
    for a in n.names:
     if(a.asname or a.name)==name:mappings.append((n.module,a.name,n.lineno))
  for call in caller['calls']:
   node=call.get('node')
   if not node or call['name']!=name or not call['start_line']<=q['line']<=call['end_line']:continue
   if isinstance(node.func,ast.Attribute)and isinstance(node.func.value,ast.Name)and node.func.value.id in imports:
    mappings.append((imports[node.func.value.id],name,node.lineno));binding_names.add(node.func.value.id)
  mappings=list(dict.fromkeys(mappings))
  if len(mappings)!=1:
   if mappings:self.unresolved.append({'reason':'public_import_binding_ambiguous','symbol':name,'candidates':len(mappings)})
   return []
  module,target,import_line=mappings[0]
  # A local store can shadow a public import; do not resolve by its name alone.
  if any(isinstance(n,ast.Name)and isinstance(n.ctx,ast.Store)and n.id in binding_names and n.lineno<=q['line']for n in ast.walk(caller['tree'])):
   self.unresolved.append({'reason':'import_name_rebound_before_use','symbol':name});return []
  spec=self.snapshots[q['snapshot']];authorities=list(spec.get('allowed_external_roots',[]))
  for profile in spec.get('native_profiles',{}).values():authorities.extend(profile.get('allowed_external_roots',[]))
  candidates=[]
  for a in authorities:
   if a.get('language')not in(None,'Python'):continue
   root=Path(a.get('root',a.get('path','')))
   if not root.is_absolute():continue
   if a.get('source_kind')=='file':
    if root.stem==module.split('.')[-1]:candidates.append(root)
    continue
   pieces=module.split('.');variants=[root.joinpath(*pieces).with_suffix('.py'),root.joinpath(*pieces)/'__init__.py']
   if root.name==pieces[0]:variants.extend([root.joinpath(*pieces[1:]).with_suffix('.py')if len(pieces)>1 else root/'__init__.py',root.joinpath(*pieces[1:])/'__init__.py'])
   candidates.extend(p for p in variants if p.is_file())
  out=[]
  for absolute in sorted(set(candidates)):
   try:
    alias=self._native_path(q['snapshot'],{'path':str(absolute),'absolute_path':str(absolute)});data=self._parse(q['snapshot'],alias);units=[u for u in data['units']if u['name']==target]
    if len(units)!=1:continue
    unit=units[0];shadowed=False
    for n in ast.walk(data['tree']):
     if isinstance(n,ast.ImportFrom)and n.lineno>unit['name_line']and any((a.asname or a.name)in(target,'*')for a in n.names):shadowed=True
     elif isinstance(n,ast.Assign)and n.lineno>unit['name_line']and any(isinstance(t,ast.Name)and t.id==target for t in n.targets):shadowed=True
    if shadowed:
     self.unresolved.append({'reason':'module_definition_later_overridden_or_fallback','module':module,'symbol':target,'path':alias,'actual_imported_implementation_unresolved':True});continue
    self._metadata(q['snapshot'],q['file'],import_line,'reference_edge',{'relation':'public_import_to_declared_analysis_module_candidate','module':module,'symbol':target,'target_path':alias,'runtime_import_binding_proven':False},'public_import_and_prepared_environment_only')
    out.extend(self._emit_unit(q['snapshot'],alias,unit,'public_import_plus_declared_environment_source_candidate','analysis_profile_implementation_candidate_not_deployed_runtime_proof'))
   except(ValueError,OSError,SyntaxError,Limit,UnicodeError)as e:self.unresolved.append({'reason':'analysis_module_source_unresolved','module':module,'detail':str(e)})
  return out
 def _query_body(self,q):
  data=self._parse(q['snapshot'],q['file']);name=q.get('symbol','').rsplit('.',1)[-1]
  if name:
   named=[u for u in data['units']if u['name']==name and(u['name_line']==q['line']or u['start_line']<=q['line']<=u['end_line'])]
   if len(named)==1:return self._emit_unit(q['snapshot'],q['file'],named[0],'explicit_named_definition_body','explicit_named_source_unit')
   # Resolve at the actual requested use; never substitute enclosing caller.
   result=self._lookup(q,'definition')
   if not any(e['kind']in SOURCE_KINDS for e in result):result.extend(self._python_import_body(q))
   if not any(e['kind']in SOURCE_KINDS for e in result):self.unresolved.append({'reason':'requested_callee_body_not_obtained','symbol':q.get('symbol'),'file':q['file'],'line':q['line'],'caller_substitution':False})
   return result
  unit=self._unit_at(data,q['line'])
  if not unit:self.unresolved.append({'reason':'no_complete_enclosing_source_unit','file':q['file'],'line':q['line']});return []
  body=self._body(q['snapshot'],q['file'],unit,'explicit_enclosing_source_request',relation='enclosing_context_not_callee_proof');return [body]if body else[]

 def seed_questions(self):
  choices=[]
  visited=0
  for snapshot in('HEAD','BASE'):
   for path,changed in sorted(self.changed[snapshot].items()):
    if path not in self.snapshots.get(snapshot,{}).get('files',{}):continue
    if not self.language_for(path):continue
    if visited>=self.budgets['max_seed_files']:break
    visited+=1
    try:data=self._parse(snapshot,path)
    except(ValueError,OSError,SyntaxError,Limit,UnicodeError)as e:self.unresolved.append({'reason':'changed_source_seed_unresolved','snapshot':snapshot,'path':path,'detail':str(e)});continue
    affected=[u for u in data['units']if any(u['start_line']<=n<=u['end_line']for n in changed)]
    for call in data['calls']:
     direct=any(call['start_line']<=n<=call['end_line']for n in changed)
     dependent=any(u['start_line']<=call['start_line']<=u['end_line']for u in affected)
     if direct or dependent:
      choices.append((0 if direct else 1,snapshot,path,call.get('line',call['start_line']),{'snapshot':snapshot,'file':path,'line':call.get('line',call['start_line']),'column':call.get('column',0),'symbol':call['name'],'kind':'implementation_body','query':'definition','reason':'Inspect actual implementation at a changed call or call inside a changed value-producing unit; no defect presumed.'}))
    for u in data['units']:
     if u.get('name_line')and any(u['start_line']<=n<=u['end_line']for n in changed):
      choices.append((2,snapshot,path,u['name_line'],{'snapshot':snapshot,'file':path,'line':u['name_line'],'column':u['name_column'],'symbol':u['name'],'kind':'value_path','query':'references','reason':'Inspect consumers/callers of the changed source unit; reference is not proven execution.'}))
    if len(choices)>=self.budgets['max_seed_questions']*3:break
  out=[];seen=set();groups={(s,k):[]for s in('HEAD','BASE')for k in('definition','references')}
  for _,snapshot,path,line,q in sorted(choices,key=lambda x:(x[0],x[1]!='HEAD',x[2],x[3],x[4]['symbol'])):
   key=snapshot,path,line,q['query'],q['symbol']
   if key in seen:continue
   seen.add(key);q.update(id='Q-'+digest(q)[:16],claim_id='exploration');groups[snapshot,q['query']].append(q)
  while any(groups.values())and len(out)<self.budgets['max_seed_questions']:
   for key in [('HEAD','definition'),('HEAD','references'),('BASE','definition'),('BASE','references')]:
    if groups[key]:out.append(groups[key].pop(0))
    if len(out)==self.budgets['max_seed_questions']:break
  return out
 def _ast_stage(self):
  visited=attempted=0
  for snapshot in('HEAD','BASE'):
   for path,changed in sorted(self.changed[snapshot].items()):
    if self.active_source_cap-source_tokens(self.evidence)<self.budgets['min_stage_headroom_tokens']:break
    if visited>=self.budgets['max_ast_files']or attempted>=self.budgets['max_ast_units']:break
    if path not in self.snapshots.get(snapshot,{}).get('files',{}):continue
    if not self.language_for(path):
     self.unresolved.append({'reason':'unsupported_actual_changed_file_language','snapshot':snapshot,'path':path});continue
    visited+=1
    try:
     data=self._parse(snapshot,path);selected={}
     for n in sorted(changed):
      unit=self._unit_at(data,n)
      if unit:selected[(unit['start_line'],unit['end_line'])]=unit
     for _,unit in sorted(selected.items()):
      if attempted>=self.budgets['max_ast_units']:break
      attempted+=1;self._emit_unit(snapshot,path,unit,'changed_AST_complete_unit','changed_AST_source_unit')
     if not selected:self.unresolved.append({'reason':'changed_module_scope_or_nonfunction_source','snapshot':snapshot,'path':path})
    except Limit:raise
    except(ValueError,OSError,SyntaxError,UnicodeError)as e:self.unresolved.append({'reason':'AST_stage_unresolved','snapshot':snapshot,'path':path,'detail':str(e)})
  self.events.append({'event':'AST_policy_limits','visited_files':visited,'attempted_units':attempted,'max_files':self.budgets['max_ast_files'],'max_units':self.budgets['max_ast_units']})

 def _scip_paths(self,snapshot):
  self.events.append({'event':'SCIP_disabled_before_index_IO','operation':'_scip_paths','snapshot':snapshot,'index_read_syscalls':0})
  raise SCIPDisabled('SCIP_DISABLED_IN_LSP_ONLY_EXPERIMENT')
  # Frozen predecessor implementation retained below for provenance only;
  # the unconditional guard above prevents fallback or any filesystem probe.
  spec=self.snapshots.get(snapshot,{})
  if spec.get('scip_usage_allowed')is False:return []
  declared=spec.get('scip_exports',[]);declared=list(declared.values())if isinstance(declared,dict)else declared
  out=[]
  for v in declared:
   path=v.get('path')if isinstance(v,dict)else v
   if isinstance(path,str)and path:out.append(Path(path))
  if 'scip_exports'not in spec and spec.get('scip_export'):out.append(Path(spec['scip_export']))
  # Explicit selected receipts only. Never rediscover a failed/overbudget old
  # index via case-ID directories or a recursive glob.
  return sorted({str(p.resolve())for p in out if p.is_file()})
 def _scip_stage(self):
  self.events.append({'event':'SCIP_disabled_before_index_IO','operation':'_scip_stage','index_read_syscalls':0})
  raise SCIPDisabled('SCIP_DISABLED_IN_LSP_ONLY_EXPERIMENT')
  candidates=[];counts={'index_documents':0,'source_bound_documents':0,'anchor_symbols':0,'candidate_total':0,'indexes':[]}
  for snapshot in('HEAD','BASE'):
   paths=self._scip_paths(snapshot)
   if not paths:self.unresolved.append({'reason':'SCIP_index_unavailable','snapshot':snapshot});continue
   defs={};refs={};anchors=set();relations=[]
   for path in paths:
    self._check();raw=Path(path).read_bytes();self.stats['scip_index_reads']+=1;self.stats['scip_index_bytes']+=len(raw)
    value=json.loads(raw);rooturi=value.get('metadata',{}).get('projectRoot','');projectroot=Path(unquote(urlparse(rooturi).path)).resolve();root=Path(self.snapshots[snapshot]['root']).resolve()
    if not rooturi.startswith('file:')or not projectroot.is_relative_to(root):self.unresolved.append({'reason':'SCIP_project_root_outside_snapshot','snapshot':snapshot,'path':path});continue
    counts['indexes'].append({'snapshot':snapshot,'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'prepared_index_reuse':True})
    for doc in value.get('documents',[]):
     counts['index_documents']+=1;absolute=(projectroot/doc.get('relativePath','')).resolve()
     if not absolute.is_relative_to(root):continue
     rel=str(absolute.relative_to(root))
     if rel not in self.snapshots[snapshot].get('files',{}):continue
     claimed_hash=doc.get('bound_source_sha256',doc.get('source_sha256'))
     if claimed_hash and claimed_hash!=self.snapshots[snapshot]['files'][rel]:
      self.unresolved.append({'reason':'SCIP_document_snapshot_hash_mismatch','snapshot':snapshot,'path':rel});continue
     counts['source_bound_documents']+=1
     for info in doc.get('symbols',[]):
      for relation in info.get('relationships',[]):
       if relation.get('isImplementation'):relations.append((info['symbol'],relation['symbol']))
     for o in doc.get('occurrences',[]):
      sym=o.get('symbol','');span=o.get('range');line=span[0]+1 if span else(o.get('singleLineRange')or{}).get('line',-1)+1 if o.get('singleLineRange')else(o.get('multiLineRange')or{}).get('startLine',-1)+1
      if not sym or line<1:continue
      key=(rel,sym)if sym.startswith('local ')else('',sym);row={'snapshot':snapshot,'path':rel,'line':line,'symbol':sym}
      (defs if int(o.get('symbolRoles',0))&1 else refs).setdefault(key,[]).append(row)
      if line in self.changed[snapshot].get(rel,set()):anchors.add(key)
   counts['anchor_symbols']+=len(anchors)
   for key in sorted(anchors):
    for kind,priority,pool in [('definition',0,defs),('reference',1,refs)]:
     for row in pool.get(key,[]):candidates.append((priority,{**row,'relation':kind,'candidate_definitions':len(defs.get(key,[]))}))
   for concrete,abstract in relations:
    for key in sorted(anchors):
     if key[1]in(concrete,abstract):
      for k,values in defs.items():
       if k[1]==concrete:
        for row in values:candidates.append((2,{**row,'relation':'implementation_candidate','candidate_definitions':len(values)}))
  ranked=[];seen=set()
  for priority,row in sorted(candidates,key=lambda x:(x[0],x[1]['snapshot']!='HEAD',x[1]['path'],x[1]['line'],x[1]['symbol'])):
   key=digest(row)
   if key in seen:continue
   seen.add(key);ranked.append((priority,row))
  queues={(s,p):[]for s in('HEAD','BASE')for p in(0,1,2)}
  for p,row in ranked:queues[row['snapshot'],p].append(row)
  ordered=[]
  while any(queues.values()):
   for key in [('HEAD',0),('HEAD',1),('BASE',0),('BASE',1),('HEAD',2),('BASE',2)]:
    if queues[key]:ordered.append(queues[key].pop(0))
  counts['candidate_total']=len(ordered);counts['distinct_unit_attempt_cap']=self.budgets['max_scip_candidates'];counts['candidate_scan_cap']=self.budgets['max_scip_scan_candidates']
  put(self.out/'scip_candidates.json',{'counts':counts,'candidates':ordered,'scope':'Static index candidates; no automatic call/dataflow interpretation. Not all candidates are source-admitted.'})
  units_seen=set();attempts=0;scanned=0
  for row in ordered[:self.budgets['max_scip_scan_candidates']]:
   try:
    self._check();scanned+=1;expected=self.snapshots[row['snapshot']]['files'][row['path']];cached=self.cache.get((row['snapshot'],row['path'],expected));data=cached or self._parse(row['snapshot'],row['path'])
    if not 1<=row['line']<=len(data['rows']):raise ValueError('SCIP_occurrence_outside_physical_file')
    unit=self._unit_at(data,row['line'])
    key=(row['snapshot'],row['path'],unit['start_line'],unit['end_line'])if unit else(row['snapshot'],row['path'],None,None)
    if key in units_seen:continue
    units_seen.add(key)
    self._metadata(row['snapshot'],row['path'],row['line'],'reference_edge'if row['relation']=='reference'else'definition_location',{'provider':'SCIP','relation':row['relation'],'symbol':row['symbol'],'candidate_definitions':row['candidate_definitions'],'source_role':'enclosing_scope_of_symbol_occurrence','static_reference_not_call_or_dataflow':True},'SCIP_changed_symbol_batch_candidate')
    if unit:
     attempts+=1;self._emit_unit(row['snapshot'],row['path'],unit,'SCIP_changed_symbol_batch_candidate','static_reference_consumer_candidate'if row['relation']=='reference'else'symbol_definition_enclosing_scope_not_callee_proof')
    else:self.unresolved.append({'reason':'SCIP_location_not_complete_source_unit','path':row['path'],'line':row['line']})
    if attempts>=self.budgets['max_scip_candidates']:break
   except Limit as e:self.unresolved.append({'reason':'SCIP_candidate_budget_stop','detail':str(e)});break
   except(ValueError,OSError,SyntaxError,UnicodeError)as e:self.unresolved.append({'reason':'SCIP_candidate_body_unresolved','detail':str(e),'path':row['path']})
  counts['candidates_scanned']=scanned;counts['distinct_units_attempted']=attempts;counts['distinct_scope_keys_seen']=len(units_seen)
  self.scip_summary=counts

 def collect(self,stage):
  if stage.upper()=='SCIP':
   self.events.append({'event':'SCIP_disabled_before_index_IO','operation':'collect','index_read_syscalls':0})
   raise SCIPDisabled('SCIP_DISABLED_IN_LSP_ONLY_EXPERIMENT')
  stage=stage.upper();order=('AST','LSP','SCIP')
  if stage not in order:raise ValueError('unknown_context_stage')
  if stage in self.stage_exports:return copy.deepcopy(self.stage_exports[stage])
  if stage in self.stages:raise ValueError('earlier_stage_not_available_in_resumed_context')
  for current in order[:order.index(stage)+1]:
   if current in self.stages:continue
   self.query_started=time.monotonic();before=copy.deepcopy(self.stats);before_rows=source_rows(self.evidence);start=time.monotonic();self.active_source_cap=self.budgets[{'AST':'ast_source_tokens','LSP':'lsp_source_tokens','SCIP':'scip_source_tokens'}[current]]
   try:
    if current=='AST':self._ast_stage()
    elif current=='LSP':
     for q in self.seed_questions():self.query(q,keep_stage_cap=True)
    else:self._scip_stage()
   except(Limit,ValueError,OSError,SyntaxError,UnicodeError)as e:self.unresolved.append({'reason':'context_stage_failed','stage':current,'detail':str(e)})
   now=source_rows(self.evidence);record={'stage':current,'source_tokens':source_tokens(self.evidence),'source_rows':len(now),'new_physical_rows':len(set(now)-set(before_rows)),'source_cap':self.active_source_cap,'global_source_cap':self.budgets['max_source_tokens'],'incremental_cost':{k:self.stats[k]-before[k]for k in self.stats},'seconds':time.monotonic()-start,'evidence_ids':[e['id']for e in self.evidence],'delivery_sha256':digest(self.evidence),'unresolved_count':len(self.unresolved)}
   self.stages[current]=record;self.stage_order.append(current);snapshot=self.export();self.stage_exports[current]=copy.deepcopy(snapshot);put(self.out/f'context_{current}.json',snapshot)
  self.active_source_cap=self.budgets['max_source_tokens'];return copy.deepcopy(self.stage_exports[stage])

 def query(self,q,*,keep_stage_cap=False):
  self.sequence+=1;self.query_started=time.monotonic();before=copy.deepcopy(self.stats);self.stats['query_calls']+=1;before_ids={e['id']for e in self.evidence};oldcap=self.active_source_cap
  if not keep_stage_cap:self.active_source_cap=self.budgets['max_source_tokens']
  unresolved_before=len(self.unresolved);events_before=len(self.events)
  try:
   self._check();snapshot=q.get('snapshot','HEAD');q={**q,'snapshot':snapshot,'file':safe_rel(q['file']),'line':int(q.get('line',0))};kind=q.get('query')
   if kind in('definition','references','implementation'):
    received=self._lookup(q,kind)
    if kind in('definition','implementation')and q.get('kind')in('implementation_body','exception_branch')and not any(e['kind']in SOURCE_KINDS for e in received):self._python_import_body(q)
   elif kind=='body':self._query_body(q)
   elif kind=='flow':self._flow(q)
   elif kind=='search':self._search(q)
   elif kind=='environment':self._metadata(snapshot,q['file'],max(1,q['line']),'environment_record',{'analysis_env_reference':self.snapshots[snapshot].get('analysis_env_reference'),'allowed_external_root_count':len(self.snapshots[snapshot].get('allowed_external_roots',[])),'runtime_binding_proven':False},'recorded_analysis_environment');self.unresolved.append({'reason':'environment_record_not_runtime_binding'})
   elif kind=='runtime':self.unresolved.append({'reason':'runtime_probe_unavailable_static_condition'})
   else:self.unresolved.append({'reason':'unsupported_typed_query','query':kind})
  except(ValueError,OSError,SyntaxError,Limit,UnicodeError,RuntimeError,subprocess.SubprocessError)as e:self.unresolved.append({'reason':'query_unresolved','detail':type(e).__name__+': '+str(e),'query':q.get('query')})
  self.active_source_cap=oldcap
  selected=list(dict.fromkeys(e['id']for e in self.events[events_before:]if e.get('event')in('existing_evidence_reused','evidence_admitted')))
  result={'question':copy.deepcopy(q),'evidence':[e for e in self.evidence if e['id']not in before_ids],'selected_evidence_ids':selected,'already_available_evidence_ids':[v for v in selected if v in before_ids],'unresolved':self.unresolved[unresolved_before:],'cost':{k:self.stats[k]-before[k]for k in self.stats},'source_tokens_after':source_tokens(self.evidence),'raw_paths':list(self.raw_paths),'requested_callee_substituted_by_caller':False}
  p=self.out/f'query-{self.sequence:04d}.json';put(p,result);result['query_receipt']=str(p);return result

 def _flow(self,q):
  data=self._parse(q['snapshot'],q['file']);unit=self._unit_at(data,q['line'])
  if not unit:self.unresolved.append({'reason':'flow_scope_unresolved'});return
  self._body(q['snapshot'],q['file'],unit,'flow_enclosing_complete_unit',relation='enclosing_source_not_execution_proof')
  facts=[]
  if q['file'].endswith('.go'):
   for f in(unit.get('facts')or[])[:20]:facts.append({k:f[k]for k in('relation','start_line','end_line','from','to','slot','unresolved')if k in f})
  else:
   node=unit['node'];params={n.arg for n in list(node.args.posonlyargs)+list(node.args.args)+list(node.args.kwonlyargs)}if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))else set()
   def names(n):return sorted({x.id for x in ast.walk(n)if isinstance(x,ast.Name)})
   scoped=list(self._scope_nodes(node));outer= params|{x.id for n in scoped for x in[n]if isinstance(x,ast.Name)and isinstance(x.ctx,ast.Store)}
   for n in scoped:
    if isinstance(n,(ast.Assign,ast.AnnAssign)):
     targets=n.targets if isinstance(n,ast.Assign)else[n.target]
     if n.value is not None:facts.append({'relation':'simple_alias'if isinstance(n.value,ast.Name)else'lexical_assignment','line':n.lineno,'from':names(n.value),'to':sorted({x.id for t in targets for x in ast.walk(t)if isinstance(x,ast.Name)}),'function_boundary_crossed':False})
    elif isinstance(n,ast.Return)and n.value is not None:facts.append({'relation':'return_expression','line':n.lineno,'from':names(n.value),'function_boundary_crossed':False})
    elif isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))and n is not node:
     local={x.id for x in ast.walk(n)if isinstance(x,ast.Name)and isinstance(x.ctx,ast.Store)}|{x.arg for x in list(n.args.posonlyargs)+list(n.args.args)+list(n.args.kwonlyargs)}
     captured=sorted(({x.id for x in ast.walk(n)if isinstance(x,ast.Name)and isinstance(x.ctx,ast.Load)}-local)&outer)
     if captured:facts.append({'relation':'closure_capture_candidate','line':n.lineno,'from':captured,'runtime_callback_dispatch':'unresolved'})
    if len(facts)>=20:break
  for i in range(0,len(facts),5):self._metadata(q['snapshot'],q['file'],unit['start_line'],'value_path',{'analysis':'lexical function-internal facts; not CFG/heap/runtime proof','facts':facts[i:i+5]},'AST_local_value_facts')
  calls=[c for c in data['calls']if unit['start_line']<=c['start_line']<=unit['end_line']]
  calls=sorted(calls,key=lambda c:(not c['start_line']<=q['line']<=c['end_line'],c['start_line'],c['name']))
  for call in calls[:2]:self._interprocedural(q,data,unit,call)
  if len(calls)>2:self.unresolved.append({'reason':'interprocedural_call_candidate_cap','total':len(calls),'inspected':2})
 @staticmethod
 def _scope_nodes(node):
  yield node
  for child in ast.iter_child_nodes(node):
   if isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):
    yield child
   else:yield from ContextCollector._scope_nodes(child)
 def _interprocedural(self,q,caller,caller_unit,call):
  cq={**q,'line':call.get('line',call['start_line']),'column':call.get('column',0),'symbol':call['name']}
  try:
   native=self._native(cq,'definition')
   if native['state']!='completed':self.unresolved.append({'reason':'callee_value_binding_native_unresolved','symbol':call['name'],'state':native['state']});return
   for row in native['locations'][:2]:
    path=self._native_path(q['snapshot'],row);data=self._parse(q['snapshot'],path);target=self._unit_at(data,row['line_start'],definition=True)
    if not target or target['kind']!='function':self.unresolved.append({'reason':'callee_value_target_not_function','path':path,'line':row['line_start']});continue
    self._emit_unit(q['snapshot'],path,target,'question_driven_value_path_callee','static_native_callee_candidate_not_runtime_dispatch')
    relations=[]
    if q['file'].endswith('.py')and path.endswith('.py'):
     callnode=call['node'];tn=target['node'];pos=list(tn.args.posonlyargs)+list(tn.args.args);kw={p.arg:p for p in list(tn.args.kwonlyargs)+pos}
     if target.get('is_method'):
      self.unresolved.append({'reason':'method_receiver_argument_mapping_unresolved','callee':target['name']});continue
     if any(isinstance(a,ast.Starred)for a in callnode.args)or any(a.arg is None for a in callnode.keywords)or len(callnode.args)>len(pos):
      self.unresolved.append({'reason':'star_variadic_or_arity_argument_mapping_unresolved','line':call['line']});continue
     for arg,param in zip(callnode.args,pos):relations.append({'relation':'argument_to_parameter','from_path':q['file'],'from_line':arg.lineno,'argument_expression':ast.get_source_segment(caller['text'],arg),'to_path':path,'parameter':param.arg,'to_line':param.lineno,'binding':'static_native_definition_candidate'})
     for arg in callnode.keywords:
      if arg.arg in kw:relations.append({'relation':'argument_to_parameter','from_path':q['file'],'from_line':arg.value.lineno,'argument_expression':ast.get_source_segment(caller['text'],arg.value),'to_path':path,'parameter':arg.arg,'to_line':kw[arg.arg].lineno,'binding':'static_named_argument_candidate'})
      else:self.unresolved.append({'reason':'keyword_parameter_not_declared','keyword':arg.arg})
     assignments=[]
     for n in self._scope_nodes(caller_unit['node']):
      if isinstance(n,ast.Assign)and n.value is callnode:assignments.extend(ast.get_source_segment(caller['text'],a)for a in n.targets)
      elif isinstance(n,ast.AnnAssign)and n.value is callnode:assignments.append(ast.get_source_segment(caller['text'],n.target))
     if assignments:
      for n in self._scope_nodes(tn):
       if isinstance(n,ast.Return)and n.value is not None:relations.append({'relation':'return_to_assignment','from_path':path,'from_line':n.lineno,'return_expression':ast.get_source_segment(data['text'],n.value),'to_path':q['file'],'to_line':call['line'],'assignment_targets':assignments,'return_branch_feasibility':'unresolved'})
    elif q['file'].endswith('.go')and path.endswith('.go'):
     if target.get('receiver')or target.get('variadic')or len(call.get('args',[]))!=len(target.get('params',[]))or len(call.get('arg_bindings')or[])!=len(target.get('params',[])):
      self.unresolved.append({'reason':'Go_receiver_variadic_or_arity_mapping_unresolved','callee':target['name']});continue
     for index,param in enumerate(target.get('params',[])):relations.append({'relation':'argument_to_parameter','from_path':q['file'],'from_line':call['line'],'argument_bindings':(call.get('arg_bindings')or[])[index],'to_path':path,'parameter_binding':param,'slot':index,'binding':'static_positional_candidate'})
     assigned=call.get('assigned')or[]
     for fact in target.get('facts')or[]:
      if fact['relation']=='return_value'and fact['slot']<len(assigned):relations.append({'relation':'return_to_assignment','from_path':path,'from_line':fact['start_line'],'return_bindings':fact.get('from',[]),'to_path':q['file'],'to_line':call['line'],'assignment_binding':assigned[fact['slot']],'return_branch_feasibility':'unresolved'})
    for i in range(0,len(relations),3):self._metadata(q['snapshot'],q['file'],call['line'],'value_path',{'analysis':'Function-boundary static candidate mapping, separate from local def-use; not heap/path/runtime proof','runtime_dispatch':'unresolved','facts':relations[i:i+3]},'static_argument_parameter_return_assignment_mapping')
   if len(native['locations'])>2:self.unresolved.append({'reason':'multiple_callee_definition_candidates','total':len(native['locations']),'inspected':2,'single_runtime_target_confirmed':False})
  except(ValueError,OSError,SyntaxError,Limit,UnicodeError,RuntimeError,subprocess.SubprocessError)as e:self.unresolved.append({'reason':'interprocedural_value_binding_unresolved','symbol':call['name'],'detail':str(e)})

 def export(self):
  return {'case_id':self.case['id'],'language':self.language,'evidence':copy.deepcopy(self.evidence),'source_tokens':source_tokens(self.evidence),'physical_source_row_count':len(source_rows(self.evidence)),'source_row_sha256':digest([[*k,v]for k,v in sorted(source_rows(self.evidence).items())]),'cost':copy.deepcopy(self.stats),'stages':copy.deepcopy(self.stages),'admission_events':copy.deepcopy(self.events),'unresolved':copy.deepcopy(self.unresolved),'raw_paths':list(self.raw_paths),'scip_summary':copy.deepcopy(getattr(self,'scip_summary',None)),'source_pool_sha256':digest({s:{k:v for k,v in p.items()if k in('commit','files','external_files','allowed_external_roots','native_profiles')}for s,p in self.snapshots.items()}),'external_source_bindings':copy.deepcopy(self.external_bindings),'delivery_sha256':digest(self.evidence),'policy_caps':{k:self.budgets[k]for k in('ast_source_tokens','lsp_source_tokens','scip_source_tokens','max_source_tokens','max_ast_files','max_ast_units','max_seed_files','max_scip_candidates','max_scip_scan_candidates','max_metadata_tokens')},'semantic_truth_guaranteed':False,'runtime_probe_calls':0,'SCIP_disabled':True,'native_lookup_cache_summary':{'entries':len(self.native_lookup_cache),'keys':[list(k)for k in self.native_lookup_cache],'result_sha256':{digest(list(k)):digest(v)for k,v in self.native_lookup_cache.items()},'new_RPC_on_hit':False}}
 def close(self):
  if self.closed:return {'cost':{k:0 for k in self.stats},'cumulative_cost':copy.deepcopy(self.stats),'already_closed':True}
  self.query_started=time.monotonic();before=copy.deepcopy(self.stats)
  for key,client in self.clients.items():
   try:client.close()
   except Exception as e:
    self.unresolved.append({'reason':'native_shutdown_error','detail':str(e)})
    try:client.close(graceful=False)
    except Exception:pass
  self.clients={};self.closed=True;out={'cost':{k:self.stats[k]-before[k]for k in self.stats},'cumulative_cost':copy.deepcopy(self.stats),'unresolved':copy.deepcopy(self.unresolved),'raw_paths':list(self.raw_paths)};put(self.out/'closed.json',out);return out
