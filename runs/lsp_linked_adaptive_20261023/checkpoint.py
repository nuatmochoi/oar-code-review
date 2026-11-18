"""Take an infrastructure-only checkpoint without interrupting any model/LSP call."""
import hashlib,json,os,signal,subprocess,time
from pathlib import Path
R=Path(__file__).resolve().parent;PID=77390
def children():
 s=subprocess.check_output(['ps','-axo','pid,ppid'],text=True)
 return [int(z[0]) for line in s.splitlines()[1:] if len(z:=line.split())==2 and int(z[1])==PID]
def attempt():
 if children():return False
 os.kill(PID,signal.SIGSTOP)
 try:
  ch=children();missing=[p.stem for p in (R/'inputs').glob('*.json') if not (R/'raw'/p.name).exists()]
  partial=[p.stem[:-3] for p in (R/'raw').glob('*_s1.json') if not (R/'trajectories'/f'{p.stem[:-3]}.json').exists()]
  if ch or missing or any('Q1_' in rid or (R/'inputs'/f'{rid}_s2.json').exists() for rid in partial):return False
  paths=[p for folder in ['inputs','raw','trajectories','retrieval'] for p in (R/folder).glob('*.json')]
  try:
   for p in paths:json.loads(p.read_text())
  except json.JSONDecodeError:return False
  obj={'old_pid':PID,'checkpoint_unix':time.time(),'no_live_children':True,'no_unrecorded_model_inputs':True,'partial_trajectories':partial,'completed_trajectories':len(list((R/'trajectories').glob('*.json'))),'completed_physical_calls':len(list((R/'raw').glob('*_s1.json')))+len(list((R/'raw').glob('*_s2.json'))),'preserved_hashes':{str(p.relative_to(R)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},'reason':'Cyclic GC during repository AST unpickling dominates sampled CPU. No active native/model child, no model input lacking saved result, no partial adaptive traversal. Frozen policy and prompts unchanged.'}
  with (R/'runtime_checkpoint.json').open('x') as f:json.dump(obj,f,indent=2)
  os.kill(PID,signal.SIGTERM);print('checkpointed',obj['completed_physical_calls'],obj['completed_trajectories'],partial,flush=True);return True
 finally:os.kill(PID,signal.SIGCONT)
deadline=time.monotonic()+45
while time.monotonic()<deadline:
 if attempt():break
 time.sleep(1)
else:print('No quiescent checkpoint within 45 seconds; original run continues.',flush=True)
