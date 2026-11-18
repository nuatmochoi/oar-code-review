"""Sequential contrasts, three parallel PRs, real official scoring after each arm."""
from pathlib import Path
import concurrent.futures,json,os,subprocess,sys,time,traceback,urllib.request
from experiment import ROOT,ARMS,load,write,finalize,tokens
from freeze_run import verify

def state(phase,**values):
 (ROOT/'status.json').write_text(json.dumps(dict(phase=phase,updated_at=time.time(),pid=os.getpid(),**values),indent=2))
def ids():return {p.parent.name for p in (ROOT/'api').glob('*/admission.json')}
def score(arm):
 out=ROOT/'arms'/arm;dest=out/'scoring';dest.mkdir(exist_ok=True)
 if (dest/'results.json').exists():return
 files=list((out/'findings').glob('*.json'));assert len(files)==25
 token=(ROOT/'broker.token').read_text().strip()
 env={**os.environ,'PI_CODING_AGENT_DIR':str(ROOT/'judge-config'),'REVIEW_BROKER_TOKEN':token,'OPENAI_API_KEY':token,'REVIEW_BENCH_JUDGE_TIMEOUT_MS':'1800000'}
 for key in ['OPENAI_PROJECT_ID','OPENAI_ORG_ID','ANTHROPIC_API_KEY']:env.pop(key,None)
 command=['npm','run','judge','--','--candidate',str(out/'findings'),'--golden',str(ROOT/'upstream/golden'),'--manifest',str(ROOT/'upstream/corpus/manifest.json'),'--provider','openai','--model','gpt-5.6-sol','--output',str(dest/'results.json'),'--repo-dir',str(ROOT/'judge-repos'),'--concurrency','3']
 before=ids();write(dest/'command.json',dict(command=command,before_api_ids=sorted(before)))
 with (dest/'judge.log').open('x')as f:r=subprocess.run(command,cwd=ROOT/'upstream',env=env,stdout=f,stderr=f)
 # Streaming receipts are persisted as upstream connections close.
 for _ in range(20):
  active=[p for p in (ROOT/'api').glob('*/admission.json')if not(p.parent/'receipt.json').exists()]
  if not active:break
  time.sleep(.5)
 write(dest/'exit.json',dict(returncode=r.returncode,api_ids=sorted(ids()-before)))
 if r.returncode:raise RuntimeError(f'Official judge {arm} failed; all raw artifacts preserved')

def main():
 verify();guardlog=(ROOT/'audit/proxy.log').open('x');guard=subprocess.Popen([sys.executable,'-B',str(ROOT/'budget_proxy.py')],stdout=guardlog,stderr=guardlog,cwd=ROOT)
 try:
  for _ in range(40):
   try:urllib.request.urlopen('http://127.0.0.1:8793/health',timeout=1).read();break
   except Exception:time.sleep(.5)
  else:raise RuntimeError('Budget transport did not start')
  packets=[load(ref['path'])for ref in load(ROOT/'public/manifest.json')]
  # Largest requests run while all budget headroom is available; no PR selection by results.
  packets.sort(key=lambda p:len(p['case']['diff']),reverse=True)
  for arm in ARMS:
   state('review',arm=arm);errors=[]
   with concurrent.futures.ThreadPoolExecutor(max_workers=3)as pool:
    futures={pool.submit(finalize,p,arm):p['index']for p in packets}
    for f in concurrent.futures.as_completed(futures):
     i=futures[f]
     try:r=f.result()
     except Exception as exc:
      value=dict(index=i,arm=arm,error=type(exc).__name__,message=str(exc),traceback=traceback.format_exc());write(ROOT/'arms'/arm/'failures'/f'{i:02d}.json',value);errors.append(value);print(json.dumps(value),flush=True)
   if errors:raise RuntimeError(f'{arm}: {len(errors)} failed PR(s); no score silently excludes them')
   state('judge',arm=arm);score(arm)
   state('report',arm=arm)
   from report import update
   result=update();print(json.dumps(dict(event='arm_completed',arm=arm,result=result['arms'][arm])),flush=True)
  state('complete',report=str(ROOT.parents[1]/'paper/REVIEWBENCH_CHANGEUNITS_COMMIT_QUERY25_20261105_KO.md'))
 except Exception as exc:state('failed',error=str(exc));raise
 finally:guard.terminate();guard.wait(timeout=15);guardlog.close()
if __name__=='__main__':main()
