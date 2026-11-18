"""Account key stays on host. Central, durable $70 admission incl. unknown calls.
No retries. Streamed judge responses are forwarded and logged until terminal usage.
"""
import argparse,hashlib,http.server,json,os,secrets,socketserver,sys,threading,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]/'tools'))
MODEL='gpt-5.6-sol';CAP=70.;REVIEW_CAP=55.;MAX_OUTPUT=16384

def write(path,data):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('x')as f:json.dump(data,f,ensure_ascii=False,indent=2)
def estimated_cost(usage):
 i=usage.get('input_tokens',usage.get('prompt_tokens'));o=usage.get('output_tokens',usage.get('completion_tokens'))
 if not isinstance(i,int)or not isinstance(o,int):return None
 details=usage.get('input_tokens_details')or usage.get('prompt_tokens_details')or{}
 cached=details.get('cached_tokens',0);writes=details.get('cache_write_tokens',0);plain=i-cached-writes
 if plain<0:raise ValueError('Usage breakdown is inconsistent')
 rate=8 if i>272000 else 4;out=30 if i>272000 else 20
 usd=(plain*rate+cached*rate*.1+writes*rate*1.25+o*out)/1e6
 return dict(input_tokens=i,output_tokens=o,cached_tokens=cached,cache_write_tokens=writes,usd_list_rate_estimate=usd,usd_conservative_upper=usd)
class Budget:
 def __init__(self,root=ROOT,cap=CAP):
  self.root=root;self.cap=cap;self.lock=threading.Lock();self.records={}
  for p in (root/'api').glob('*/receipt.json'):
   row=json.loads(p.read_text());self.records[row['id']]=row
  # Admitted without a receipt remains worst-case reserved after restart.
  for p in (root/'api').glob('*/admission.json'):
   row=json.loads(p.read_text());self.records.setdefault(row['id'],row)
 def totals(self):
  used=sum(v.get('settled_upper',v['reserved_usd'])for v in self.records.values())
  review=sum(v.get('settled_upper',v['reserved_usd'])for v in self.records.values()if v['phase']=='review')
  return used,review
 def admit(self,body,phase):
  if body.get('model')!=MODEL:raise ValueError('Only the frozen model is allowed')
  limit=body.get('max_output_tokens',body.get('max_completion_tokens',body.get('max_tokens')))
  if not isinstance(limit,int)or limit<=0 or limit>MAX_OUTPUT:raise ValueError('Explicit max output must be 1..16384')
  if body.get('previous_response_id'):raise ValueError('Unmetered server-side history is not allowed')
  if body.get('service_tier','default')not in('default','auto'):raise ValueError('Only standard billing is allowed')
  wire=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()
  # Text-only JSON: UTF-8 byte count is a conservative token upper bound;
  # reserve extra API framing, plus the entire output ceiling.
  input_bound=len(wire)+8192;reserve=(input_bound*(10 if input_bound>272000 else 5)+limit*30)/1e6
  with self.lock:
   used,review=self.totals()
   if used+reserve>self.cap or(phase=='review'and review+reserve>REVIEW_CAP):raise ValueError('Budget admission denied; no upstream request sent')
   rid=time.strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(5)
   row=dict(id=rid,phase=phase,reserved_usd=reserve,input_token_upper_bound=input_bound,max_output_tokens=limit,request_sha256=hashlib.sha256(wire).hexdigest(),admitted_at=time.time(),status='admitted_unknown')
   folder=self.root/'api'/rid;write(folder/'request.json',body);write(folder/'admission.json',row);self.records[rid]=row
   return row,folder
 def settle(self,row,folder,usage,status,error=None):
  with self.lock:
   value={**row,'status':status,'finished_at':time.time(),'usage':usage,'error':error};cost=estimated_cost(usage or{})
   if cost:
    # If our byte bound ever undercounts, stop subsequent admissions visibly.
    value['cost']=cost;value['settled_upper']=cost['usd_conservative_upper']
    value['reservation_underestimated']=value['settled_upper']>row['reserved_usd']
   elif status=='upstream_rejected':value['settled_upper']=0.
   write(folder/'receipt.json',value);self.records[row['id']]=value
   used,review=self.totals();print(json.dumps(dict(event='settled',id=row['id'],phase=row['phase'],status=status,nominal=(cost or{}).get('usd_list_rate_estimate'),upper_total=round(used,5),review_upper=round(review,5))),flush=True)
class Handler(http.server.BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def reply(self,status,body):
  raw=json.dumps(body).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  if self.path=='/health':return self.reply(200,dict(model=MODEL,cap=CAP,cost_upper=self.server.budget.totals()[0],calls=len(self.server.budget.records)))
  self.reply(404,{'error':'unknown path'})
 def do_POST(self):
  phase='judge'if self.path.startswith('/judge/')else'review'if self.path.startswith('/review/')else None
  if not phase or self.headers.get('Authorization')!='Bearer '+self.server.token:return self.reply(403,{'error':'unauthorized'})
  suffix=self.path.split('/',2)[2]
  if suffix not in('v1/responses','v1/chat/completions'):return self.reply(404,{'error':'unsupported endpoint'})
  try:
   from freeze_run import verify
   verify()
   received=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
   body=dict(received)
   if not any(k in body for k in ('max_output_tokens','max_completion_tokens','max_tokens')):body['max_output_tokens']=MAX_OUTPUT
   body['service_tier']='default'
   row,folder=self.server.budget.admit(body,phase)
   write(folder/'received_request.json',received)
   write(folder/'gateway_parameter_policy.json',dict(max_output_cap=MAX_OUTPUT,inserted_max_output='max_output_tokens'not in received,service_tier='default',prompt_changed=False))
  except Exception as exc:return self.reply(400,{'error':{'message':str(exc),'type':'budget_guard'}})
  usage=None;status='usage_unknown';error=None;headers_sent=False
  try:
   from research_openai_credentials import load_credentials,headers
   request=urllib.request.Request('https://api.openai.com/'+suffix,data=json.dumps(body).encode(),headers=headers(load_credentials()),method='POST')
   with urllib.request.urlopen(request,timeout=900)as response:
    content_type=response.headers.get('Content-Type','application/json')
    self.send_response(response.status);self.send_header('Content-Type',content_type);self.send_header('Connection','close');self.end_headers();headers_sent=True;self.close_connection=True
    chunks=[];client_alive=True
    with (folder/'response.raw').open('xb')as f:
     while True:
      chunk=response.readline()if 'text/event-stream'in content_type else response.read(65536)
      if not chunk:break
      f.write(chunk);f.flush();chunks.append(chunk)
      if client_alive:
       try:self.wfile.write(chunk);self.wfile.flush()
       except (BrokenPipeError,ConnectionResetError):client_alive=False
      if chunk.startswith(b'data: '):
       try:
        event=json.loads(chunk[6:]);r=event.get('response',{})
        if r.get('usage'):usage=r['usage'];status=r.get('status','completed')
        if event.get('usage'):usage=event['usage'];status='completed'
       except ValueError:pass
    if 'text/event-stream'not in content_type:
     result=json.loads(b''.join(chunks));usage=result.get('usage');status=result.get('status','completed');write(folder/'response.json',result)
  except urllib.error.HTTPError as exc:
   raw=exc.read();(folder/'upstream_error.raw').write_bytes(raw);status='upstream_rejected'if exc.code in (400,401,403,404,422,429)else'usage_unknown';error='HTTP '+str(exc.code)
   if not headers_sent:
    self.send_response(exc.code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  except Exception as exc:
   error=type(exc).__name__
   if not headers_sent:
    try:self.reply(502,{'error':{'message':'upstream transport failed; reservation retained','type':'upstream_unknown'}})
    except OSError:pass
  finally:self.server.budget.settle(row,folder,usage,status,error)
class Server(http.server.ThreadingHTTPServer):daemon_threads=True
if __name__=='__main__':
 token_path=ROOT/'broker.token'
 if not token_path.exists():
  token_path.write_text(secrets.token_urlsafe(32));token_path.chmod(0o600)
 server=Server(('0.0.0.0',8795),Handler);server.budget=Budget();server.token=token_path.read_text().strip();print('Budget proxy ready on port 8795',flush=True);server.serve_forever()
