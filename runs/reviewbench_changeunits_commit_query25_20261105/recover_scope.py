"""Replay unreturned/pre-admission S requests, preserving every original attempt."""
import concurrent.futures,hashlib,json,os,shutil,subprocess,sys,time,urllib.request
from experiment import ROOT,load,write,finalize,sha
from freeze_run import verify
from pipeline import score
def state(phase,**kw):(ROOT/'recovery.status.json').write_text(json.dumps(dict(phase=phase,updated_at=time.time(),pid=os.getpid(),**kw),indent=2))
def main():
 verify();oldstatus=load(ROOT/'status.json');assert oldstatus['phase']=='failed';assert len(list((ROOT/'arms/C/review').glob('*/complete.json')))==25
 write(ROOT/'audit/first-pass.status.json',oldstatus)
 write(ROOT/'audit/transport-budget-amendment.json',dict(created_at=time.time(),reason='One scope request returned HTTP503 upstream connection refused, preserving unknown usage reservation. The $55 internal reviewer subcap then denied pre-admission calls. Keep global $70, replace subcap with shared global queue and mathematically tighten byte reservation by published 1,050,000 context window.',original_frozen_files_unchanged=True,model_prompt_schema_inputs_and_scoring_unchanged=True,quality_based_resampling=False,unknown_usage_remains_reserved=True,recovery_guard_sha256=sha(ROOT/'recovery_guard.py'),recovery_launcher_sha256=sha(__file__),sources=['https://developers.openai.com/api/docs/models/gpt-5.6-sol','https://developers.openai.com/api/docs/pricing']))
 packets=[load(ref['path'])for ref in load(ROOT/'public/manifest.json')];packets.sort(key=lambda p:len(p['case']['diff']),reverse=True);pending=[];attempts={}
 for p in packets:
  dest=ROOT/'arms/S/review'/f'{p["index"]:02d}'
  if (dest/'complete.json').exists():continue
  assert not(dest/'response.json').exists(),'Never rerun a returned response for quality'
  request=load(dest/'request.json')if(dest/'request.json').exists()else None
  archive=ROOT/'attempts/S'/f'{p["index"]:02d}'/'first-pass-review';archive.parent.mkdir(parents=True,exist_ok=True);dest.rename(archive)
  attempts[p['index']]=dict(archive=str(archive),request=request);pending.append(p)
 write(ROOT/'audit/recovery-inputs-before.json',dict(rows=[dict(index=i,archive=v['archive'],request_sha256=hashlib.sha256(json.dumps(v['request'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()if v['request']else None)for i,v in attempts.items()],no_original_response_discarded=True))
 log=(ROOT/'audit/recovery-proxy.log').open('x');guard=subprocess.Popen([sys.executable,'-u',str(ROOT/'recovery_guard.py')],cwd=ROOT,stdout=log,stderr=log)
 try:
  for _ in range(40):
   try:urllib.request.urlopen('http://127.0.0.1:8793/health',timeout=1).read();break
   except Exception:time.sleep(.5)
  else:raise RuntimeError('Recovery guard not ready')
  state('review',remaining=len(pending))
  # Settle the largest reservation first, then keep the remaining PRs parallel.
  first=pending.pop(0);finalize(first,'S')
  with concurrent.futures.ThreadPoolExecutor(max_workers=3)as pool:
   futures={pool.submit(finalize,p,'S'):p['index']for p in pending}
   for f in concurrent.futures.as_completed(futures):f.result()
  proof=[]
  for i,v in attempts.items():
   new=load(ROOT/'arms/S/review'/f'{i:02d}'/'request.json')
   same=v['request'] is None or new==v['request'];assert same,('Recovery changed frozen request',i)
   proof.append(dict(index=i,original_request_available=v['request']is not None,exact_body_equal=same))
  write(ROOT/'audit/recovery-input-equality.json',dict(rows=proof,all_equal=True))
  state('judge');score('S')
  from report import update
  update();state('complete',PRs=25)
 finally:guard.terminate();guard.wait(timeout=15);log.close()
if __name__=='__main__':main()
