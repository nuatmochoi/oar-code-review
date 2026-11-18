"""Official scorer rerun with exact completed response replay; no review reruns."""
import json,os,subprocess,sys,time,urllib.request
from experiment import ROOT,load,write,sha
from freeze_run import verify
from pipeline import score
def main():
 verify();folder=ROOT/'arms/S/scoring';assert load(folder/'exit.json')['returncode']!=0
 assert len(list((ROOT/'arms/S/review').glob('*/complete.json')))==25
 folder.rename(ROOT/'attempts/S/scoring-first-pass')
 write(ROOT/'audit/judge-replay-amendment.json',dict(reason='One upstream classifier timeout, then conservative byte admission denied. Replay successful identical judge requests; count full input on otherwise inadmissible new requests, preserving all unknown charges and $70.',same_model_prompt_schema_scorer=True,approximate_replay=False,review_reruns=0,guard_sha256=sha(ROOT/'replay_judge_guard.py'),launcher_sha256=sha(__file__)))
 log=(ROOT/'audit/judge-replay-proxy.log').open('x');guard=subprocess.Popen([sys.executable,'-u',str(ROOT/'replay_judge_guard.py')],cwd=ROOT,stdout=log,stderr=log)
 try:
  for _ in range(40):
   try:urllib.request.urlopen('http://127.0.0.1:8793/health',timeout=1);break
   except Exception:time.sleep(.5)
  else:raise RuntimeError('Replay guard did not start')
  score('S')
 finally:guard.terminate();guard.wait(timeout=15);log.close()
if __name__=='__main__':main()
