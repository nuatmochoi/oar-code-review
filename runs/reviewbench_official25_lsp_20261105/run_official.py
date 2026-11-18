"""Unmodified official runner, separate work cache per concurrent worker."""
from pathlib import Path
import concurrent.futures,json,os,subprocess,time,signal
from freeze_run import verify
ROOT=Path(__file__).resolve().parent

def worker(worker_id):
 rows=[]
 for index in range(worker_id,25,3):
  verify();out=ROOT/'execution';out.mkdir(exist_ok=True);log=ROOT/'logs'/f'pr-{index:02d}.log';log.parent.mkdir(exist_ok=True)
  assert not log.exists(),'Never overwrite an attempted PR'
  env=dict(os.environ,TRY_AGENT_WORK=str(ROOT/'work'/f'w{worker_id}'),REVIEW_BASE_URL='http://host.docker.internal:8789/review/v1',REVIEW_BROKER_TOKEN=(ROOT/'broker.token').read_text().strip())
  command=[str(ROOT/'upstream/scripts/try-agent.sh'),'research-reviewbench-lsp:20261105-final','--pr',str(index),'-e','REVIEW_BASE_URL','-e','REVIEW_BROKER_TOKEN']
  start=time.monotonic()
  with log.open('x')as f:
   proc=subprocess.Popen(command,cwd=out,env=env,stdout=f,stderr=f,start_new_session=True)
   try:code=proc.wait(timeout=2400)
   except subprocess.TimeoutExpired:
    os.killpg(proc.pid,signal.SIGTERM);code=124
  row=dict(index=index,worker=worker_id,returncode=code,seconds=time.monotonic()-start,command=command)
  with (ROOT/'logs'/f'pr-{index:02d}.status.json').open('x')as f:json.dump(row,f,indent=2)
  print(json.dumps(row),flush=True);rows.append(row)
 return rows
if __name__=='__main__':
 verify()
 with concurrent.futures.ThreadPoolExecutor(max_workers=3)as p:rows=[r for group in p.map(worker,range(3))for r in group]
 with (ROOT/'audit/runner-completion.json').open('x')as f:json.dump(dict(PR_count=len(rows),passed=sum(r['returncode']==0 for r in rows),failed=sum(r['returncode']!=0 for r in rows),rows=rows),f,indent=2)
 if any(r['returncode']for r in rows):raise SystemExit(1)
