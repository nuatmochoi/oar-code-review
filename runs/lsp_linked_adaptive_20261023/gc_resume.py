"""Operational continuation only: same frozen experiment, GC off during AST loads.

Only launch after a quiescent checkpoint with no live child process, no unrecorded
model input and no unfinished adaptive retrieval. Completed outcomes are retained.
An already saved first response can continue only its identical original Q0 job.
"""
import gc,hashlib,json,os,time
from pathlib import Path
import experiment as e
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
checkpoint=read(R/'runtime_checkpoint.json')
assert checkpoint['no_live_children'] and checkpoint['no_unrecorded_model_inputs']
assert all('Q0_' in rid for rid in checkpoint['partial_trajectories'])
assert all(hashlib.sha256((R/p).read_bytes()).hexdigest()==h for p,h in checkpoint['preserved_hashes'].items())
replay_allowed=set(checkpoint['partial_trajectories']);original_save=e.save;original_call=e.call_packet
def save_once(p,obj):
 if p.exists():
  assert read(p)==json.loads(json.dumps(obj)),('Existing artifact mismatch',str(p))
 else:original_save(p,obj)
def continue_call(row,root,model,schema):
 p=Path(root)/(row['run_id']+'.json')
 if p.exists():
  rid=row['run_id'][:-3]
  assert rid in replay_allowed and row['run_id'].endswith('_s1'),row['run_id']
  x=read(p);assert x['prompt_sha256']==row['prompt_sha256'] and x['model']==model
  print(json.dumps({'continued_existing_stage':row['run_id'],'new_physical_call':False}),flush=True)
  return x
 return original_call(row,root,model,schema)
e.save=save_once;e.call_packet=continue_call
gc.disable()
print(json.dumps({'runtime_change':'cyclic GC disabled; refcount reclamation remains active','pid':os.getpid(),'frozen_policy_and_prompt_changed':False,'started_unix':time.time()}),flush=True)
e.run()
