import subprocess,sys,time,urllib.request
from experiment import ROOT,load,write,sha
from freeze_run import verify
from pipeline import score
def main():
 verify();folder=ROOT/'arms/S/scoring';assert load(folder/'exit.json')['returncode']!=0;folder.rename(ROOT/'attempts/S/scoring-second-pass')
 write(ROOT/'audit/judge-replay-v2-amendment.json',dict(reason='Reuse earliest completed S responses when every field except cache-routing key is equal; preserve all unknown costs and both failed full scorer attempts',model_prompt_schema_tools_scorer_unchanged=True,reviewer_reruns=0,guard_sha256=sha(ROOT/'replay_judge_guard_v2.py'),launcher_sha256=sha(__file__)))
 log=(ROOT/'audit/judge-replay-v2-proxy.log').open('x');guard=subprocess.Popen([sys.executable,'-u',str(ROOT/'replay_judge_guard_v2.py')],cwd=ROOT,stdout=log,stderr=log)
 try:
  for _ in range(40):
   try:urllib.request.urlopen('http://127.0.0.1:8793/health',timeout=1);break
   except Exception:time.sleep(.5)
  else:raise RuntimeError('Replay-v2 guard not ready')
  score('S')
 finally:guard.terminate();guard.wait(timeout=15);log.close()
if __name__=='__main__':main()
