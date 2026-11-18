"""Detached pipeline: budget guard -> official25 -> official judge -> report."""
import json,os,subprocess,sys,time,urllib.request
from pathlib import Path
from freeze_run import verify
ROOT=Path(__file__).resolve().parent
status=ROOT/'background.status.json'
def state(phase,**extra):status.write_text(json.dumps(dict(phase=phase,pid=os.getpid(),updated_at=time.time(),**extra),indent=2))
verify();guardlog=(ROOT/'audit/budget-proxy.log').open('x');guard=subprocess.Popen([sys.executable,'-B',str(ROOT/'budget_proxy.py')],cwd=ROOT,stdout=guardlog,stderr=guardlog)
try:
 for _ in range(60):
  try:urllib.request.urlopen('http://127.0.0.1:8791/health',timeout=1).read();break
  except Exception:time.sleep(.5)
 else:raise RuntimeError('Budget guard did not start')
 for phase,script in [('review','run_official.py'),('judge','run_judge.py'),('report','finish_compare.py')]:
  state(phase)
  with (ROOT/'audit'/f'background-{phase}.log').open('x')as f:r=subprocess.run([sys.executable,'-B',str(ROOT/script)],cwd=ROOT,stdout=f,stderr=f)
  if r.returncode:raise RuntimeError(f'{phase} failed: {r.returncode}; raw logs retained')
 state('complete',comparison=json.loads((ROOT/'comparison.json').read_text()))
except Exception as exc:state('failed',error=str(exc));raise
finally:
 guard.terminate();guard.wait(timeout=10);guardlog.close()
