"""Same $70 transport guard, with audited free input counts for unknown calls."""
import json
import budget_proxy as base
from recovery_guard import QueuedBudget,ROOT
from experiment import load,sha

class ExactBudget(QueuedBudget):
 def __init__(self,root=ROOT,cap=70):
  super().__init__(root,cap)
  for p in (root/'audit/input_token_counts').glob('*/audit.json'):
   x=load(p);rid=x['generation_request_id'];row=self.records[rid]
   assert x['original_request_sha256']==sha(root/'api'/rid/'request.json')
   if row.get('usage'):continue
   current=self.corrections.get(rid,{}).get('corrected_upper',row.get('settled_upper',row['reserved_usd']))
   if x['unknown_cost_upper_usd']<current:self.corrections[rid]=dict(original_upper=row.get('settled_upper',row['reserved_usd']),corrected_upper=x['unknown_cost_upper_usd'],basis='Official complete-input count plus 8192 token margin and full maximum output; unknown charge remains reserved',count_audit=str(p))

def main():
 server=base.Server(('0.0.0.0',8793),base.Handler);server.budget=ExactBudget();server.token=(ROOT/'broker.token').read_text().strip()
 base.write(ROOT/'audit/exact-reservation-corrections.json',dict(corrections=server.budget.corrections,global_cap=70,original_receipts_unchanged=True))
 print(json.dumps(dict(ready=True,port=8793,total_upper=server.budget.totals()[0])),flush=True);server.serve_forever()
if __name__=='__main__':main()
