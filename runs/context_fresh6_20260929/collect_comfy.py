from concurrent.futures import ThreadPoolExecutor
import json,hashlib,tarfile,io
from pathlib import Path,PurePosixPath
import requests
R=Path(__file__).resolve().parent
samples=json.loads((R/'candidate_metadata.json').read_text())
selected=[s for s in samples if s['githubPrUrl'].split('/')[-1] in ('7952','9560')]
def collect(s):
 repo=s['githubPrUrl'].split('github.com/')[1].split('/pull/')[0];pr=s['githubPrUrl'].split('/')[-1];commit=s['target_commit']
 fork=json.loads((R/f'github_pr_{pr}.json').read_text())['head']['repo']['full_name']
 url=f'https://codeload.github.com/{fork}/tar.gz/{commit}'
 response=requests.get(url,timeout=180);response.raise_for_status()
 directory=R/'snapshots'/pr
 directory.mkdir(parents=True,exist_ok=False)
 sources={};excluded=[]
 with tarfile.open(fileobj=io.BytesIO(response.content),mode='r:gz') as archive:
  for m in archive:
   p=PurePosixPath(*PurePosixPath(m.name).parts[1:])
   if not m.isfile() or '..' in p.parts or p.is_absolute():continue
   # Archive source inventory is complete for Python and dependency/license metadata.
   if p.suffix != '.py' and p.name not in ('pyproject.toml','requirements.txt','uv.lock','LICENSE','LICENSE.md'):continue
   b=archive.extractfile(m).read(); f=directory/str(p);f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b)
   sources[str(p)]=hashlib.sha256(b).hexdigest()
 result={'repository':repo,'pr':pr,'commit':commit,'url':url,'archive_sha256':hashlib.sha256(response.content).hexdigest(),'archive_bytes':len(response.content),'files':sources}
 (R/f'snapshot_{pr}.json').write_text(json.dumps(result,indent=2))
 print(json.dumps({'pr':pr,'files':len(sources),'archive_bytes':len(response.content)}),flush=True)
 return result
with ThreadPoolExecutor(max_workers=3) as pool: rows=list(pool.map(collect,selected))
(R/'comfy_snapshot_receipts.json').write_text(json.dumps([{k:v for k,v in r.items() if k!='files'} for r in rows],indent=2))
