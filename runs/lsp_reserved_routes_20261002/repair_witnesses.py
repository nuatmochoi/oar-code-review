import concurrent.futures,json,subprocess
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];py=ROOT/'tools/venv-lsp-expansion-20261001/bin/python';iid='pytest-dev__pytest-5631'
def run(job):
 rev,rep=job;p=subprocess.run([str(py),str(R/'witness_one.py'),str(R/'checkouts'/iid/rev),iid],capture_output=True,text=True,timeout=90)
 try:x=json.loads(p.stdout.splitlines()[-1])
 except Exception:x={'ok':False,'error':'No JSON output','stdout':p.stdout}
 x.update(revision=rev,repeat=rep,exit_code=p.returncode,stderr=p.stderr);return x
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:repaired=list(pool.map(run,[(rev,rep) for rev in ['original','fixed'] for rep in range(2)]))
(R/'witnesses_repaired.json').write_text(json.dumps(repaired,indent=2)+'\n')
initial=json.loads((R/'witnesses_initial.json').read_text());final=[x for x in initial if x['instance_id']!=iid]+repaired;(R/'witnesses.json').write_text(json.dumps(final,indent=2)+'\n')
print(json.dumps(repaired,indent=2))
