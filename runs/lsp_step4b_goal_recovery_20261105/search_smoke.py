"""Five predetermined public cases, fixed previous plans, no model/gold."""
import copy,json
from concurrent.futures import ThreadPoolExecutor
from common import ROOT,BUDGETS,load,write_new
from sequential import ORIGINAL
from persistent_context import ContextCollector
from loop_policy import Frontier,execute,plan_only,replay
from prompts_lsp48 import changed_operations
from diff48 import diff_evidence

def one(case):
 source=load(ORIGINAL/'results/shared'/f'{case["id"]}_r1'/'shared.json');initial=source['initial'];base=load(ROOT/'contexts'/case['id']/'context_LSP.json');es=diff_evidence(case['diff'])+base['evidence'];f=Frontier(initial,es,changed_operations=changed_operations(case,es));tool=ContextCollector(case,ROOT/'audit/search_smoke'/case['id'],BUDGETS,initial_state=base)
 try:
  a=execute(f,plan_only(initial),tool,enabled=True)
  from persistent_context import release_budget_cached_answers
  release_budget_cached_answers(f)
  b=execute(f,plan_only(source['replan']),tool,enabled=True)
  reconstructed=replay(case,dict(initial=initial,initial_evidence=es,first=a,replan=source['replan'],second=b))
  assert reconstructed.raw_claims==f.raw_claims
  sessions=tool.export()['search_sessions'];record=dict(case_id=case['id'],passed=True,first=a,second=b,search_sessions=sessions,model_calls=0,private_gold_read=False)
 finally:tool.close()
 write_new(ROOT/'audit/search_smoke'/f'{case["id"]}.json',record)
 return dict(case_id=case['id'],passed=True,searches=len(sessions),candidate_count=sum(len(s['candidates'])for s in sessions.values()))

def main():
 cases=load(ROOT/'prepared/cases.json')[:5]
 with ThreadPoolExecutor(max_workers=4)as pool:rows=list(pool.map(one,cases))
 write_new(ROOT/'audit/search_smoke.json',dict(passed=True,cases=rows,selection='First five prepared public cases, fixed before new model execution.',model_calls=0))
 print(json.dumps(rows),flush=True)
if __name__=='__main__':main()
