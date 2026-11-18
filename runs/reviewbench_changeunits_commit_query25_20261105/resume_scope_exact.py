"""Recover only missing S responses after first recovery's natural timeout."""
import concurrent.futures,json,os,subprocess,sys,time,urllib.request
from experiment import ROOT,load,write,finalize,sha
from freeze_run import verify
from pipeline import score
def state(phase,**kw):(ROOT/'recovery2.status.json').write_text(json.dumps(dict(phase=phase,updated_at=time.time(),pid=os.getpid(),**kw),indent=2))
def main():
 verify();assert len(list((ROOT/'arms/S/review').glob('*/complete.json')))<25
 # Never interrupt an admitted request merely to free a reservation.
 try:urllib.request.urlopen('http://127.0.0.1:8793/health',timeout=1)
 except Exception:pass
 else:raise RuntimeError('Prior budget guard still active; wait for natural completion')
 packets=[load(ref['path'])for ref in load(ROOT/'public/manifest.json')];packets.sort(key=lambda p:len(p['case']['diff']),reverse=True);pending=[];attempts={}
 for p in packets:
  dest=ROOT/'arms/S/review'/f'{p["index"]:02d}'
  if(dest/'complete.json').exists():continue
  assert not(dest/'response.json').exists(),'Do not resample a returned response'
  request=load(dest/'request.json');archive=ROOT/'attempts/S'/f'{p["index"]:02d}'/'second-pass-review';dest.rename(archive);attempts[p['index']]=request;pending.append(p)
 write(ROOT/'audit/exact-recovery-amendment.json',dict(created_at=time.time(),reason='Prior transport request timed out naturally; preserve unknown charge, use official free complete-input counts to tighten conservative reservations within unchanged global $70',recovered_indices=[p['index']for p in pending],model_prompt_schema_inputs_scoring_unchanged=True,quality_resampling=False,guard_sha256=sha(ROOT/'exact_count_guard.py'),launcher_sha256=sha(__file__)))
 log=(ROOT/'audit/exact-recovery-proxy.log').open('x');guard=subprocess.Popen([sys.executable,'-u',str(ROOT/'exact_count_guard.py')],cwd=ROOT,stdout=log,stderr=log)
 try:
  for _ in range(40):
   try:urllib.request.urlopen('http://127.0.0.1:8793/health',timeout=1).read();break
   except Exception:time.sleep(.5)
  else:raise RuntimeError('Exact-count guard not ready')
  state('review',remaining=len(pending))
  with concurrent.futures.ThreadPoolExecutor(max_workers=3)as pool:
   for f in concurrent.futures.as_completed([pool.submit(finalize,p,'S')for p in pending]):f.result()
  proof=[]
  for i,request in attempts.items():
   same=load(ROOT/'arms/S/review'/f'{i:02d}'/'request.json')==request;assert same;proof.append(dict(index=i,exact_body_equal=same))
  write(ROOT/'audit/exact-recovery-input-equality.json',dict(rows=proof,all_equal=True));state('judge');score('S')
  from report import update
  update();state('complete',PRs=25)
 except Exception as exc:state('failed',error=str(exc));raise
 finally:guard.terminate();guard.wait(timeout=15);log.close()
if __name__=='__main__':main()
