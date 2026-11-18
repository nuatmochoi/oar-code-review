import concurrent.futures,json,os,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
entries=json.loads((ROOT/'upstream/corpus/test/test.json').read_text())
def one(item):
 i,e=item;start=time.monotonic();repo=ROOT/'work'/f'w{i%3}'/'repos'/e['nwo'].replace('/','_');repo.mkdir(parents=True,exist_ok=True);log=ROOT/'audit/fetch'/f'{i:02d}.log';log.parent.mkdir(exist_ok=True)
 env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GIT_LFS_SKIP_SMUDGE='1')
 with log.open('x')as f:
  def run(args,check=True):return subprocess.run(['git','-C',str(repo),*args],stdout=f,stderr=f,env=env,timeout=240,check=check)
  try:
   run(['init','-q']);run(['remote','add','origin','https://github.com/review-bench/'+e['nwo'].replace('/','_')+'.git']);run(['remote','add','upstream','https://github.com/'+e['nwo']+'.git'])
   r=run(['fetch','-q','--filter=blob:none','origin',e['base'],e['head']],False)
   if r.returncode:run(['fetch','-q','--filter=blob:none','upstream',e['base'],e['head']])
   row=dict(index=i,nwo=e['nwo'],ok=True,seconds=time.monotonic()-start)
  except Exception as ex:row=dict(index=i,nwo=e['nwo'],ok=False,error=type(ex).__name__+': '+str(ex),seconds=time.monotonic()-start)
 print(json.dumps(row),flush=True);return row
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor(max_workers=3)as pool:rows=list(pool.map(one,enumerate(entries)))
 (ROOT/'audit/prefetch.json').write_text(json.dumps(rows,indent=2))
