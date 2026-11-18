"""Scheduling-only overlap while one official judge call waits; no method changes."""
import concurrent.futures,hashlib,json,os,signal,time,traceback
from experiment import ROOT,load,write,plan_one
from freeze_run import verify
def main():
 verify();status=load(ROOT/'status.json');assert status['phase']=='judge'and status['arm']=='A';parent=status['pid']
 api=[load(p)for p in (ROOT/'api').glob('*/admission.json')if not(p.parent/'receipt.json').exists()]
 assert len(api)<=1 and all(x['phase']=='judge'for x in api)
 record=dict(created_at=time.time(),parent_pid=parent,change='Temporarily stop waiting orchestration parent, keep judge and budget server alive, precompute B/C shared public-only plans with two workers, then resume parent.',no_frozen_input_prompt_schema_budget_or_score_change=True,maximum_combined_model_workers=3,source_sha256=hashlib.sha256(__file__.encode()).hexdigest())
 write(ROOT/'audit/scheduling-amendment.json',record)
 os.kill(parent,signal.SIGSTOP)
 try:
  packets=[load(ref['path'])for ref in load(ROOT/'public/manifest.json')];packets.sort(key=lambda p:len(p['case']['diff']),reverse=True)
  errors=[]
  with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:
   todo={pool.submit(plan_one,p,True):p['index']for p in packets}
   for f in concurrent.futures.as_completed(todo):
    i=todo[f]
    try:
     f.result();print(json.dumps(dict(index=i,history_plan_ready=True)),flush=True)
    except Exception as exc:errors.append(dict(index=i,error=str(exc)));print(json.dumps(errors[-1]),flush=True)
  write(ROOT/'audit/history-plan-prefetch.json',dict(completed_at=time.time(),errors=errors,ready=len(list((ROOT/'plans/history').glob('*/parsed.json')))))
 finally:os.kill(parent,signal.SIGCONT)
if __name__=='__main__':main()
