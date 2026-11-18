from concurrent.futures import ThreadPoolExecutor
import json,hashlib
from pathlib import Path
import requests
R=Path(__file__).resolve().parent
samples=json.loads(Path('data/sources/aacr-bench/dataset/positive_samples.json').read_text())
selected=[s for s in samples if s['githubPrUrl'].split('/')[-1] in ('1482','3172','857','6044','6652','6553','7151') and s['project_main_language']=='Python']
(R/'candidate_metadata.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2))
jobs=[]
for s in selected:
 repo=s['githubPrUrl'].split('github.com/')[1].split('/pull/')[0];pr=s['githubPrUrl'].split('/')[-1]
 paths={c['path'] for c in s['comments'] if c['category']=='Code Defect' and c['path'].endswith('.py')}
 for version,key in [('base','source_commit'),('head','target_commit')]:
  for f in paths:jobs.append((repo,pr,version,s[key],f))
def fetch(t):
 repo,pr,v,commit,f=t
 url=f'https://raw.githubusercontent.com/{repo}/{commit}/{f}'
 response=requests.get(url,timeout=30)
 out=R/'screen_v2'/repo.replace('/','__')/pr/v/f
 r={'repository':repo,'pr':pr,'version':v,'commit':commit,'file':f,'url':url,'status':response.status_code}
 if response.ok:
  out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(response.content)
  r['sha256']=hashlib.sha256(response.content).hexdigest()
 return r
with ThreadPoolExecutor(max_workers=6) as pool:r=list(pool.map(fetch,jobs))
(R/'screen_v2_receipts.json').write_text(json.dumps(r,indent=2))
print(json.dumps({'downloaded':sum(x['status']==200 for x in r),'attempts':len(r)}))
