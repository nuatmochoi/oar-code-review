import concurrent.futures,json,subprocess
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];py=ROOT/'tools/venv-linked-adaptive-20261023/bin/python';rows=json.loads((R/'selected.json').read_text())
def one(job):
 row,rev,rep=job;iid=row['instance_id']
 try:
  p=subprocess.run([str(py),str(R/'witness_one.py'),str(R/'checkouts'/iid/rev),iid],capture_output=True,text=True,timeout=90)
  try:x=json.loads(p.stdout.splitlines()[-1])
  except Exception:x={'ok':False,'stdout':p.stdout}
  x.update(exit_code=p.returncode,stderr=p.stderr)
 except Exception as exc:x={'ok':False,'error':repr(exc)}
 x.update(instance_id=iid,revision=rev,repeat=rep);return x
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:out=list(pool.map(one,[(r,v,n) for r in rows for v in ['original','fixed'] for n in [1,2]]))
index=1
while (R/f'witness_attempt{index}.json').exists():index+=1
(R/f'witness_attempt{index}.json').write_text(json.dumps(out,indent=2))
valid=all(x['ok'] and x.get('exit_code')==0 and x['observation']['contract']==(x['revision']=='fixed') for x in out)
(R/'witness_validation.json').write_text(json.dumps({'all_contracts_verified':valid,'attempt':index,'rows':out},indent=2))
for x in out:
 if x['repeat']==1:print(x['instance_id'],x['revision'],x['ok'],x.get('observation',x.get('error')),flush=True)
print('ALL_VALID',valid)
