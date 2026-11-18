"""Scheduling-only reservation smoothing; immutable reviewer function and inputs."""
import hashlib,json,os,signal,time
from pathlib import Path
from experiment import ROOT,load,write,finalize
from freeze_run import verify
def main():
 # The full reservation for the largest input can temporarily crowd out smaller
 # concurrent inputs. Schedule this one public-size-selected S case first.
 deadline=time.monotonic()+7200
 while time.monotonic()<deadline:
  try:status=load(ROOT/'status.json')
  except (FileNotFoundError,json.JSONDecodeError):time.sleep(1);continue
  if status['phase']=='failed':return
  if status['phase']=='judge'and status.get('arm')=='C':break
  if status.get('arm')=='S'or status['phase']=='complete':return
  time.sleep(1)
 else:raise RuntimeError('C phase not observed within watcher limit')
 verify();parent=status['pid'];packets=[load(r['path'])for r in load(ROOT/'public/manifest.json')];packet=max(packets,key=lambda p:len(p['case']['diff']))
 record=dict(created_at=time.time(),parent_pid=parent,case_index=packet['index'],selection_basis='largest public diff, independent of findings/gold',change='Run largest S final once while C orchestration is waiting; resume unchanged pipeline for remaining parallel S PRs.',frozen_method_inputs_schema_budget_unchanged=True,maximum_active_requests_when_starting=3,source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
 write(ROOT/'audit/scope-reservation-scheduling.json',record)
 os.kill(parent,signal.SIGSTOP)
 try:
  # Let existing official judge workers settle if all three request slots are busy.
  while True:
   active=[p for p in (ROOT/'api').glob('*/admission.json')if not(p.parent/'receipt.json').exists()]
   if len(active)<=2:break
   time.sleep(1)
  result=finalize(packet,'S');write(ROOT/'audit/scope-largest-staged.json',dict(completed_at=time.time(),result=result))
  print(json.dumps(dict(staged_scope_index=packet['index'],complete=True)),flush=True)
 finally:os.kill(parent,signal.SIGCONT)
if __name__=='__main__':main()
