"""Recover local partial-clone state without modifying the official runner."""
from pathlib import Path
import concurrent.futures,json,os,subprocess,time
ROOT=Path(__file__).resolve().parent
entries=json.loads((ROOT/'upstream/corpus/test/test.json').read_text())
def one(item):
 i,e=item;repo=ROOT/'work'/f'w{i%3}'/'repos'/e['nwo'].replace('/','_');log=ROOT/'audit/hydration'/f'{i:02d}.log';log.parent.mkdir(exist_ok=True)
 start=time.monotonic();env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GIT_LFS_SKIP_SMUDGE='1')
 with log.open('x')as f:
  for commit in [e['base'],e['head']]:
   subprocess.run(['git','-C',str(repo),'-c','remote.origin.promisor=true','checkout','--force','--detach',commit],env=env,stdout=f,stderr=f,timeout=600,check=True)
  # Archive verifies all source blobs of the three-dot base as well.
  base=subprocess.check_output(['git','-C',str(repo),'merge-base',e['base'],e['head']]).decode().strip()
  subprocess.run(['git','-C',str(repo),'-c','remote.origin.promisor=true','archive',base],env=env,stdout=subprocess.DEVNULL,stderr=f,timeout=600,check=True)
 row=dict(index=i,ok=True,seconds=time.monotonic()-start);print(json.dumps(row),flush=True);return row
with concurrent.futures.ThreadPoolExecutor(max_workers=3)as p:rows=list(p.map(one,enumerate(entries)))
(ROOT/'audit/hydration.json').write_text(json.dumps(rows,indent=2))
