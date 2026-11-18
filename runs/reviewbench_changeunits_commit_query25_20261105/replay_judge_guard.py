"""Transport recovery: exact successful judge-response replay and token admission.

Never replays reviewer requests, never matches approximately, never drops unknown
charges. Model/prompt/schema/scorer and global $70 remain unchanged.
"""
import hashlib,io,json,secrets,sys,time,urllib.request
from pathlib import Path
import budget_proxy as base
from exact_count_guard import ExactBudget,ROOT
from experiment import load,write,sha
from research_openai_credentials import load_credentials,headers

def key(body):return hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
class CountBudget(ExactBudget):
 def admit(self,body,phase):
  assert body.get('model')==base.MODEL
  limit=body.get('max_output_tokens',body.get('max_completion_tokens',body.get('max_tokens')))
  assert isinstance(limit,int)and 0<limit<=base.MAX_OUTPUT and not body.get('previous_response_id')
  wire=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode();bound=min(len(wire)+8192,1050000);reserve=(bound*(10 if bound>272000 else 5)+limit*30)/1e6;count_ref=None
  if reserve>self.cap-self.totals()[0]:
   allowed={'model','input','instructions','conversation','previous_response_id','reasoning','text','tools','tool_choice','parallel_tool_calls','truncation','personality'}
   assert set(body)-allowed <= {'max_output_tokens','store','service_tier','metadata','stream','prompt_cache_key','include'}
   count_request={k:v for k,v in body.items()if k in allowed};dest=ROOT/'audit/admission_input_counts'/key(body)
   if(dest/'response.json').exists():result=load(dest/'response.json')
   else:
    write(dest/'request.json',count_request)
    req=urllib.request.Request('https://api.openai.com/v1/responses/input_tokens',data=json.dumps(count_request).encode(),headers=headers(load_credentials()),method='POST')
    with urllib.request.urlopen(req,timeout=60)as response:result=json.load(response)
    write(dest/'response.json',result)
   assert result['object']=='response.input_tokens';bound=result['input_tokens']+8192;reserve=(bound*(10 if bound>272000 else 5)+limit*30)/1e6;count_ref=str(dest)
  started=time.monotonic()
  with self.changed:
   while self.totals()[0]+reserve>self.cap:
    if not self.active:raise ValueError('Exact input-token bound still exceeds unchanged $70; no upstream call sent')
    if time.monotonic()-started>1000:raise ValueError('Budget queue timeout')
    self.changed.wait(timeout=1)
   rid=time.strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(5)
   row=dict(id=rid,phase=phase,reserved_usd=reserve,input_token_upper_bound=bound,max_output_tokens=limit,request_sha256=hashlib.sha256(wire).hexdigest(),admitted_at=time.time(),status='admitted_unknown',global_cap=70,input_count_audit=count_ref)
   folder=self.root/'api'/rid;base.write(folder/'request.json',body);base.write(folder/'admission.json',row);self.records[rid]=row;self.active.add(rid);return row,folder

class ReplayHandler(base.Handler):
 def do_POST(self):
  if not self.path.startswith('/judge/'):return super().do_POST()
  if self.headers.get('Authorization')!='Bearer '+self.server.token:return self.reply(403,{'error':'unauthorized'})
  raw=self.rfile.read(int(self.headers['Content-Length']));body=json.loads(raw)
  if not any(k in body for k in ('max_output_tokens','max_completion_tokens','max_tokens')):body['max_output_tokens']=base.MAX_OUTPUT
  body['service_tier']='default';hit=self.server.replays.get(key(body))
  if hit:
   data=(hit/'response.raw').read_bytes();entry=ROOT/'audit/judge_replays'/(time.strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(4)+'.json')
   write(entry,dict(original_api_id=hit.name,request_sha256=key(body),request_structurally_equal=True,response_raw_sha256=sha(hit/'response.raw'),new_inference_calls=0))
   self.send_response(200);self.send_header('Content-Type','text/event-stream'if body.get('stream')else'application/json');self.send_header('Content-Length',str(len(data)));self.send_header('Connection','close');self.end_headers();self.close_connection=True;self.wfile.write(data);self.wfile.flush();return
  previous=self.rfile;self.rfile=io.BytesIO(raw)
  try:return super().do_POST()
  finally:self.rfile=previous

def main():
 server=base.Server(('0.0.0.0',8793),ReplayHandler);server.budget=CountBudget();server.token=(ROOT/'broker.token').read_text().strip();server.replays={}
 for p in (ROOT/'api').glob('*/receipt.json'):
  row=load(p)
  if row['phase']=='judge'and row.get('usage')and row['status']=='completed'and(p.parent/'response.raw').exists():server.replays.setdefault(key(load(p.parent/'request.json')),p.parent)
 write(ROOT/'audit/judge-replay-guard-start.json',dict(exact_cached_requests=len(server.replays),global_cap=70,unknown_charges_reserved=True,reviewer_responses_never_replayed=True,total_upper=server.budget.totals()[0],guard_sha256=sha(__file__)))
 print(json.dumps(dict(ready=True,replays=len(server.replays),cost_upper=server.budget.totals()[0])),flush=True);server.serve_forever()
if __name__=='__main__':main()
