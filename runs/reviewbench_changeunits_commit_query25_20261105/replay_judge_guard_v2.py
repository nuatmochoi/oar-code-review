"""Replay exact S model inputs, ignoring only the provider's cache routing key."""
import hashlib,json
import budget_proxy as base
import replay_judge_guard as old
from experiment import ROOT,load,write,sha

def input_key(body):
 return hashlib.sha256(json.dumps({k:v for k,v in body.items()if k!='prompt_cache_key'},sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def main():
 # Every prompt message, tool, schema, model and generation setting remains in
 # this equality check. prompt_cache_key only chooses cache routing.
 old.key=input_key
 server=base.Server(('0.0.0.0',8793),old.ReplayHandler);server.budget=old.CountBudget();server.token=(ROOT/'broker.token').read_text().strip();server.replays={}
 first=set(load(ROOT/'attempts/S/scoring-first-pass/exit.json')['api_ids'])
 second=set(load(ROOT/'attempts/S/scoring-second-pass/exit.json')['api_ids'])
 # Earliest successful S response wins; never choose by correctness or score.
 for group in [first,second]:
  for rid in sorted(group,key=lambda rid:load(ROOT/'api'/rid/'admission.json')['admitted_at']):
   p=ROOT/'api'/rid
   if not(p/'receipt.json').exists():continue
   row=load(p/'receipt.json')
   if row.get('usage')and row['status']=='completed'and(p/'response.raw').exists():server.replays.setdefault(input_key(load(p/'request.json')),p)
 write(ROOT/'audit/judge-replay-v2-start.json',dict(cached_S_requests=len(server.replays),matching_excludes=['prompt_cache_key'],all_model_input_and_generation_fields_equal=True,earliest_successful_S_response_priority=True,quality_selection=False,global_cap=70,total_upper=server.budget.totals()[0],guard_sha256=sha(__file__)))
 print(json.dumps(dict(ready=True,replays=len(server.replays),upper=server.budget.totals()[0])),flush=True);server.serve_forever()
if __name__=='__main__':main()
