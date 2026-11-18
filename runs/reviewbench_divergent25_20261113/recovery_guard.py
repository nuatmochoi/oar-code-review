"""Transport-only durable queue; frozen model inputs/scorer and $70 stay fixed."""
import hashlib,json,secrets,threading,time
from pathlib import Path
import budget_proxy as base
ROOT=base.ROOT
MODEL_CONTEXT_CEILING=1050000
class QueuedBudget(base.Budget):
 def __init__(self,root=ROOT,cap=70):
  super().__init__(root,cap);self.changed=threading.Condition(self.lock);self.active=set();self.corrections={}
  for rid,row in self.records.items():
   if not row.get('usage'):
    # A request cannot successfully consume more input tokens than the model's
    # published context window. Add the full output cap again, conservatively.
    ceiling=(MODEL_CONTEXT_CEILING*10+row.get('max_output_tokens',16384)*30)/1e6
    original=row.get('settled_upper',row['reserved_usd'])
    if original>ceiling:self.corrections[rid]=dict(original_upper=original,corrected_upper=ceiling,basis='Published 1,050,000 context window; maximum 30m long-context write rate $10/M plus full max output at $30/M. Usage remains unknown, never released as zero.')
 def totals(self):
  values=[(row,self.corrections.get(rid,{}).get('corrected_upper',row.get('settled_upper',row['reserved_usd'])))for rid,row in self.records.items()]
  return sum(v for _,v in values),sum(v for row,v in values if row['phase']=='review')
 def admit(self,body,phase):
  if body.get('model')!=base.MODEL:raise ValueError('Only the frozen model is allowed')
  limit=body.get('max_output_tokens',body.get('max_completion_tokens',body.get('max_tokens')))
  if not isinstance(limit,int)or not 0<limit<=base.MAX_OUTPUT:raise ValueError('Explicit frozen output ceiling required')
  if body.get('previous_response_id'):raise ValueError('Unmetered server history forbidden')
  if body.get('service_tier','default')not in('default','auto'):raise ValueError('Only frozen standard tier')
  wire=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode();raw_bound=len(wire)+8192;bound=min(raw_bound,MODEL_CONTEXT_CEILING)
  reserve=(bound*(10 if bound>272000 else 5)+limit*30)/1e6;started=time.monotonic()
  with self.changed:
   while self.totals()[0]+reserve>self.cap:
    if not self.active:raise ValueError('Global $70 remains insufficient even with no active reservations; no upstream call sent')
    if time.monotonic()-started>1000:raise ValueError('Admission queue timeout; no upstream call sent')
    self.changed.wait(timeout=1)
   rid=time.strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(5)
   row=dict(id=rid,phase=phase,reserved_usd=reserve,input_token_upper_bound=bound,byte_upper_before_model_limit=raw_bound,max_output_tokens=limit,request_sha256=hashlib.sha256(wire).hexdigest(),admitted_at=time.time(),status='admitted_unknown',admission_queue_seconds=time.monotonic()-started,global_cap=70,review_subcap_removed_within_same_global_cap=True)
   folder=self.root/'api'/rid;base.write(folder/'request.json',body);base.write(folder/'admission.json',row);self.records[rid]=row;self.active.add(rid)
   return row,folder
 def settle(self,row,folder,usage,status,error=None):
  try:super().settle(row,folder,usage,status,error)
  finally:
   with self.changed:self.active.discard(row['id']);self.changed.notify_all()
def main():
 server=base.Server(('0.0.0.0',8795),base.Handler);server.budget=QueuedBudget();server.token=(ROOT/'broker.token').read_text().strip()
 path=ROOT/'audit/reservation-corrections.json';base.write(path,dict(corrections=server.budget.corrections,global_cap=70,review_and_judge_share_same_global_cap=True,original_receipts_unchanged=True,model_source='https://developers.openai.com/api/docs/models/gpt-5.6-sol',pricing_source='https://developers.openai.com/api/docs/pricing'))
 print(json.dumps(dict(ready=True,port=8795,corrections=server.budget.corrections,total_upper=server.budget.totals()[0])),flush=True);server.serve_forever()
if __name__=='__main__':main()
