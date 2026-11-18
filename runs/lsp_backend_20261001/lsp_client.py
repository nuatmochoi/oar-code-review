"""Minimal actual stdio JSON-RPC/LSP client. No symbol-resolution fallback."""
from __future__ import annotations
import json,subprocess,threading,queue,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SERVER=ROOT/'tools/pyright-lsp-20261001/node_modules/pyright/dist/pyright-langserver.js'
class Client:
 def __init__(self,root):
  self.root=Path(root).resolve();self.trace=[];self.errors=[];self.next_id=1;self.pending={};self.lock=threading.RLock();self.opened=set();self.started=time.monotonic()
  self.settings={'python':{'pythonPath':str(ROOT/'tools/venv-parallel-review20260928/bin/python'),'analysis':{'typeCheckingMode':'off','diagnosticMode':'openFilesOnly','autoSearchPaths':False,'useLibraryCodeForTypes':False,'extraPaths':[str(self.root)]}}}
  self.proc=subprocess.Popen(['/opt/homebrew/bin/node',str(SERVER),'--stdio'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=self.root)
  threading.Thread(target=self._reader,daemon=True).start();threading.Thread(target=self._stderr,daemon=True).start()
  self.initialization=self.request('initialize',{'processId':None,'rootUri':self.root.as_uri(),'workspaceFolders':[{'uri':self.root.as_uri(),'name':self.root.name}],'capabilities':{'workspace':{'configuration':True,'workspaceFolders':True},'textDocument':{'definition':{'linkSupport':True},'synchronization':{'didSave':False}},'general':{'positionEncodings':['utf-16']}}})
  assert self.initialization['capabilities'].get('definitionProvider'),self.initialization
  self.send({'jsonrpc':'2.0','method':'initialized','params':{}})
  self.send({'jsonrpc':'2.0','method':'workspace/didChangeConfiguration','params':{'settings':self.settings}})
  self.startup_seconds=time.monotonic()-self.started
 def _stderr(self):
  for line in iter(self.proc.stderr.readline,b''):self.errors.append(line.decode(errors='replace').rstrip())
 def send(self,obj):
  b=json.dumps(obj,ensure_ascii=False).encode();frame=f'Content-Length: {len(b)}\r\n\r\n'.encode()+b
  with self.lock:self.proc.stdin.write(frame);self.proc.stdin.flush()
 def request(self,method,params,timeout=45):
  q=queue.Queue()
  with self.lock:rid=self.next_id;self.next_id+=1;self.pending[rid]=q
  start=time.monotonic();self.send({'jsonrpc':'2.0','id':rid,'method':method,'params':params})
  try:response=q.get(timeout=timeout)
  except queue.Empty:raise TimeoutError((method,params,self.proc.poll(),self.errors[-3:]))
  finally:
   with self.lock:self.pending.pop(rid,None)
  self.trace.append({'id':rid,'method':method,'params':params,'response':response,'duration_seconds':time.monotonic()-start})
  if 'error' in response:raise RuntimeError(response['error'])
  return response.get('result')
 def _reader(self):
  try:
   while True:
    headers={}
    while True:
     line=self.proc.stdout.readline()
     if not line:return
     if line in (b'\r\n',b'\n'):break
     k,_,v=line.decode().partition(':');headers[k.lower()]=v.strip()
    length=int(headers['content-length']);data=self.proc.stdout.read(length)
    if len(data)!=length:raise EOFError('short JSON-RPC frame')
    obj=json.loads(data)
    if 'method' in obj and 'id' in obj:
     if obj['method']=='workspace/configuration':
      result=[]
      for item in obj.get('params',{}).get('items',[]):
       value=self.settings
       for part in item.get('section','').split('.'):
        if part:value=value.get(part,{}) if isinstance(value,dict) else {}
       result.append(value)
     elif obj['method']=='workspace/workspaceFolders':result=[{'uri':self.root.as_uri(),'name':self.root.name}]
     else:result=None
     self.send({'jsonrpc':'2.0','id':obj['id'],'result':result})
     self.trace.append({'server_request':obj['method'],'params':obj.get('params'),'result':result})
    elif 'id' in obj:
     with self.lock:q=self.pending.get(obj['id'])
     if q:q.put(obj)
    elif obj.get('method')=='window/logMessage':self.trace.append({'notification':obj['method'],'params':obj.get('params')})
    elif obj.get('method')=='textDocument/publishDiagnostics':
     self.trace.append({'notification':obj['method'],'uri':obj['params'].get('uri'),'diagnostic_count':len(obj['params'].get('diagnostics',[]))})
  except Exception as e:self.errors.append('Reader: '+str(e))
 def definition(self,file,line,character):
  path=self.root/file
  if file not in self.opened:
   self.send({'jsonrpc':'2.0','method':'textDocument/didOpen','params':{'textDocument':{'uri':path.as_uri(),'languageId':'python','version':1,'text':path.read_text()}}});self.opened.add(file)
  return self.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':{'line':line,'character':character}})
 def close(self):
  try:self.request('shutdown',None,timeout=10);self.send({'jsonrpc':'2.0','method':'exit','params':None});self.proc.wait(timeout=10)
  except Exception:self.proc.kill();self.proc.wait()
