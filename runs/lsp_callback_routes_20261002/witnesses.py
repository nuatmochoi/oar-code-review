import concurrent.futures,json,subprocess
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];py=ROOT/'tools/venv-lsp-expansion-20261001/bin/python';rows=json.loads((R/'selected.json').read_text())
def run(job):
 row,rev,rep=job;iid=row['instance_id'];p=subprocess.run([str(py),str(R/'witness_one.py'),str(R/'checkouts'/iid/rev),iid],capture_output=True,text=True,timeout=90)
 try:x=json.loads(p.stdout.splitlines()[-1])
 except Exception:x={'ok':False,'error':'No JSON output','stdout':p.stdout}
 x.update(revision=rev,repeat=rep,exit_code=p.returncode,stderr=p.stderr);return x
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:out=list(pool.map(run,[(row,rev,rep) for row in rows for rev in ['original','fixed'] for rep in range(2)]))
(R/'witnesses.json').write_text(json.dumps(out,indent=2)+'\n')
for x in out:print(json.dumps({k:x[k] for k in ['instance_id','revision','repeat','ok']}|{'result':x.get('observation',x.get('error'))}),flush=True)
