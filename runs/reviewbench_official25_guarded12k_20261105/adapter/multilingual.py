"""Language extension of frozen L6: complete AST units + actual native LSP RPC.
References and definitions remain static candidates, never runtime proof.
"""
import copy,dataclasses,importlib,json,os,re,sys,time
from pathlib import Path
from symbol_context import ContextCollector as Original
import context48 as core
from context48 import put,Limit

EXT={'.ts':'TypeScript','.tsx':'TSX','.js':'JavaScript','.jsx':'JavaScript','.mjs':'JavaScript','.cjs':'JavaScript','.cs':'CSharp','.java':'Java','.kt':'Kotlin','.kts':'Kotlin','.rs':'Rust','.swift':'Swift','.php':'PHP','.rb':'Ruby','.sh':'Shell','.bash':'Shell'}
PARSERS={'TypeScript':'typescript','TSX':'tsx','JavaScript':'javascript','CSharp':'csharp','Java':'java','Kotlin':'kotlin','Rust':'rust','Swift':'swift','PHP':'php','Ruby':'ruby','Shell':'bash'}
COMMANDS={'TypeScript':['typescript-language-server','--stdio'],'TSX':['typescript-language-server','--stdio'],'JavaScript':['typescript-language-server','--stdio'],'CSharp':['csharp-ls'],'Java':['jdtls'],'Kotlin':['kotlin-language-server'],'Rust':['rust-analyzer'],'Swift':['sourcekit-lsp'],'PHP':['intelephense','--stdio'],'Ruby':['solargraph','stdio'],'Shell':['bash-language-server','start']}
LANGIDS={'TypeScript':'typescript','TSX':'typescriptreact','JavaScript':'javascript','CSharp':'csharp','Java':'java','Kotlin':'kotlin','Rust':'rust','Swift':'swift','PHP':'php','Ruby':'ruby','Shell':'shellscript'}
# Explicit syntax nodes; no lexical chunks masquerading as AST functions.
FUNCTIONS={'function_declaration','function_definition','method_declaration','method_definition','constructor_declaration','local_function_statement','function_item','closure_expression','arrow_function','function_expression','lambda_literal','init_declaration','deinit_declaration','anonymous_function','singleton_method','method'}
CLASSES={'class_declaration','class_definition','struct_declaration','struct_item','enum_declaration','enum_item','interface_declaration','interface_body','trait_item','impl_item','object_declaration','module'}
CALLS={'call_expression','invocation_expression','method_invocation','function_call_expression','member_call_expression','scoped_call_expression','object_creation_expression','call','command'}
IDENTIFIERS={'identifier','type_identifier','simple_identifier','field_identifier','property_identifier','name','constant','word'}
class ContextCollector(Original):
 @staticmethod
 def language_for(path):return Original.language_for(path)or EXT.get(Path(path).suffix.lower())
 def _parse(self,snapshot,path):
  lang=self.language_for(path)
  if lang in ('Python','Go'):return super()._parse(snapshot,path)
  if lang not in PARSERS:raise ValueError('unsupported_actual_file_language')
  data=self._read(snapshot,path);key=snapshot,path,data['sha256']
  if key in self.cache:return self.cache[key]
  from tree_sitter_language_pack import get_parser
  raw=data['text'].encode();tree=get_parser(PARSERS[lang]).parse(raw);units=[];calls=[];identifiers=[]
  def walk(n):
   yield n
   for ch in n.named_children:yield from walk(ch)
  def text(n):return raw[n.start_byte:n.end_byte].decode()
  def token(n):
   if n is None:return None
   if n.type in IDENTIFIERS and re.fullmatch(r'[^\W\d]\w*',text(n)):return n
   for ch in reversed(n.named_children):
    value=token(ch)
    if value is not None:return value
   return None
  for n in walk(tree.root_node):
   if n.type in IDENTIFIERS and re.fullmatch(r'[^\W\d]\w*',text(n)):
    identifiers.append(dict(name=text(n),line=n.start_point.row+1,column=n.start_point.column+1,role='AST_identifier'))
   if n.type in FUNCTIONS|CLASSES:
    name=token(n.child_by_field_name('name'))
    if name is None and lang=='Kotlin':name=next((ch for ch in n.named_children if ch.type=='simple_identifier'),None)
    if name is None and n.type in ('arrow_function','closure_expression','lambda_literal'):
     name=token(n.parent.child_by_field_name('name'))if n.parent else None
    # An anonymous complete closure is still a body; no fabricated symbol anchor.
    name_text=text(name)if name is not None else n.type+'@'+str(n.start_point.row+1)
    if not n.has_error:
     units.append(dict(name=name_text,start_line=n.start_point.row+1,end_line=n.end_point.row+(1 if n.end_point.column else 0),name_line=name.start_point.row+1 if name else None,name_column=name.start_point.column+1 if name else None,kind='class'if n.type in CLASSES else'function',node=n,syntax_kind=n.type))
   if n.type in CALLS:
    target=n.child_by_field_name('function')or n.child_by_field_name('name')or n.child_by_field_name('constructor')
    if target is None and n.named_children:target=n.named_children[0]
    name=token(target)
    if name is not None:calls.append(dict(name=text(name),line=name.start_point.row+1,column=name.start_point.column+1,start_line=n.start_point.row+1,end_line=n.end_point.row+1,node=n))
  self.stats['ast_parses']+=1
  if tree.root_node.has_error:self.unresolved.append(dict(reason='AST_parse_contains_error_nodes',snapshot=snapshot,path=path,policy='only_error_free_complete_units_admitted'))
  data.update(units=units,calls=calls,tree=tree,_identifiers=identifiers,_declarations=[]);self.cache[key]=data;return data
 def _client(self,snapshot,path):
  lang=self.language_for(path)
  if lang in ('Python','Go'):return super()._client(snapshot,path)
  self._profile(snapshot,lang);module=Path(self.snapshots[snapshot]['root']).resolve();key=snapshot,str(module),lang
  if key in self.clients:return self.clients[key]
  self._check(2)
  if str(core.PRODUCT)not in sys.path:sys.path.insert(0,str(core.PRODUCT))
  m=importlib.import_module('review_agent.lsp.client')
  command=COMMANDS[lang];opts=()
  if lang=='Java':opts=(('settings',{'java':{'import':{'gradle':{'enabled':False},'maven':{'enabled':False}},'autobuild':{'enabled':False}}}),)
  workspace_settings=(('csharp',{}),)if lang=='CSharp'else()
  config=m.LspServerConfig(LANGIDS[lang],command[0],tuple(command[1:]),workspace_settings=workspace_settings,initialization_options=opts,waits_for_project_load=lang in('Java','Kotlin','CSharp','Rust','Ruby'))
  folder=self.out/'native'/snapshot/core.digest([str(module),lang])[:12];folder.mkdir(parents=True,exist_ok=True)
  client=m.LspClient(module,config,timeout_seconds=self._timeout(),initialize_timeout_seconds=self._timeout(),project_load_timeout_seconds=min(15.,self._timeout()),project_load_grace_seconds=3.,environment=self._environment(snapshot,lang,module))
  original=client._request;count=[0]
  def request(method,params,*,timeout=None):
   self._check(1);self.stats['native_calls']+=1;count[0]+=1;begin=time.monotonic();row={'method':method,'params':params}
   try:value=original(method,params,timeout=min(self._timeout(),timeout or self._timeout()));row['result']=value;return value
   except Exception as e:row['error']=type(e).__name__+': '+str(e);raise
   finally:
    row['seconds']=time.monotonic()-begin;self.stats['native_seconds']+=row['seconds'];p=folder/f'rpc-{count[0]:04d}.json';put(p,row);self.raw_paths.append(str(p))
  client._request=request;self.clients[key]=client;client.start();return client
 def _native_uncached(self,q,operation):
  # Original lower() differs from LSP IDs for C#/JSX; temporarily use the
  # original canonical implementation with an explicit protocol-ID adapter.
  lang=self.language_for(q['file'])
  if lang in ('Python','Go'):return super()._native_uncached(q,operation)
  data=self._read(q['snapshot'],q['file']);name,column=self._anchor(q,data);client=self._client(q['snapshot'],q['file'])
  if not client.supports(operation):return dict(state='unsupported',operation=operation,locations=[],capabilities=client.server_capabilities)
  m=importlib.import_module('review_agent.code_intelligence.tree_sitter_index');absolute=Path(data['path']);module=client.root
  rel=str(absolute.relative_to(module))if absolute.is_relative_to(module)else str(absolute)
  symbol=m.StructuralSymbol(path=rel,name=name,kind='actual_public_question_token',line_start=q['line'],line_end=q['line'],column_start=column,column_end=column+len(name),language=LANGIDS[lang],source='actual_source_AST_or_verified_token')
  try:values=getattr(client,operation)(symbol)
  except RuntimeError as exc:
   if not any(code in str(exc)for code in ('-32801','-32800')):raise
   self.events.append(dict(event='transient_native_content_changed',detail=str(exc),operation=operation));values=[]
  # A first empty response during startup is not proof of no binding. Drain
  # pending progress and retry once within the same native/token/time caps.
  if not values and not getattr(client,'_l6_empty_retry_used',False):
   client._l6_empty_retry_used=True
   begin=time.monotonic();quiet=time.monotonic()
   while min(2.,self._timeout())>time.monotonic()-begin:
    try:
     message=client._read_response(.2);client._handle_server_message(message);quiet=time.monotonic()
    except TimeoutError:
     if time.monotonic()-quiet>=1. and client.project_load_state!='loading':break
    except (EOFError,OSError,RuntimeError):break
   self.stats['native_seconds']+=time.monotonic()-begin
   self.events.append(dict(event='startup_empty_native_retry',language=lang,operation=operation,first_empty_preserved=True,semantic_absence_proven=False))
   while True:
    try:
     values=getattr(client,operation)(symbol);break
    except RuntimeError as exc:
     if not any(code in str(exc)for code in ('-32801','-32800')):raise
     self.events.append(dict(event='transient_native_content_changed',detail=str(exc),operation=operation))
     self._check(1);begin_wait=time.monotonic()
     try:client._handle_server_message(client._read_response(min(.5,self._timeout())))
     except TimeoutError:pass
     self.stats['native_seconds']+=time.monotonic()-begin_wait
  out=[]
  for value in values:
   row=dataclasses.asdict(value);p=Path(row['path']);p=p if p.is_absolute()else module/p;row['absolute_path']=str(p.resolve());out.append(row)
  return dict(state='completed'if out else'empty',operation=operation,locations=out,capabilities=client.server_capabilities)
 def _local_flow(self,q):
  if self.language_for(q['file'])in('Python','Go'):return super()._local_flow(q)
  self.unresolved.append(dict(reason='new_language_def_use_not_implemented',snapshot=q['snapshot'],path=q['file']))
 def _flow(self,q):
  if self.language_for(q['file'])in('Python','Go'):return super()._flow(q)
  self.unresolved.append(dict(reason='new_language_value_flow_not_implemented',snapshot=q['snapshot'],path=q['file']))

 def close(self):
  clients=dict(self.clients)
  result=super().close()
  path=self.out/'native-server-final-state.json'
  if not path.exists():
   rows=[dict(snapshot=key[0],workspace=key[1],language=key[2],capabilities=c.server_capabilities,events=[dataclasses.asdict(e)for e in c.events],stderr=c.stderr_lines)for key,c in clients.items()]
   put(path,rows)
  return result
