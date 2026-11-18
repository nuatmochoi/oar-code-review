import concurrent.futures,json,subprocess
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];py=ROOT/'tools/venv-lsp-expansion-20261001/bin/python';rows=json.loads((R/'selected.json').read_text())
def run(job):
 row,rev,rep=job;iid=row['instance_id'];p=subprocess.run([str(py),'-bb',str(R/'witness_one.py'),str(R/'checkouts'/iid/rev),iid],capture_output=True,text=True,timeout=90)
 try:result=json.loads(p.stdout.splitlines()[-1])
 except Exception:result={'ok':False,'error':'No JSON witness output','stdout':p.stdout}
 result.update(revision=rev,repeat=rep,exit_code=p.returncode,stderr=p.stderr);return result
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:out=list(pool.map(run,[(row,rev,rep) for row in rows for rev in ['original','fixed'] for rep in range(2)]))
(R/'witnesses.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
for row in rows:
 iid=row['instance_id'];rr=[x for x in out if x['instance_id']==iid];print(iid,[(x['revision'],x.get('observation',x.get('error'))) for x in rr],flush=True)
