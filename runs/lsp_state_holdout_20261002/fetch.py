import concurrent.futures,hashlib,io,json,subprocess,tarfile,urllib.request
from pathlib import Path
R=Path(__file__).resolve().parent
rows=json.loads((R/'selected.json').read_text())
def fetch(row):
 iid=row['instance_id'];root=R/'checkouts'/iid; archive=R/'archives'/(iid+'.tar.gz');archive.parent.mkdir(exist_ok=True)
 if not archive.exists():
  url=f"https://codeload.github.com/{row['repo']}/tar.gz/{row['base_commit']}"
  with urllib.request.urlopen(url,timeout=120) as resp:archive.write_bytes(resp.read())
 if not root.exists():
  root.mkdir(parents=True)
  with tarfile.open(archive) as tf:
   for member in tf:
    rel=Path(*Path(member.name).parts[1:])
    if not member.isfile() or not rel.parts or '..' in rel.parts:continue
    p=root/'original'/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(tf.extractfile(member).read())
  for name in ['patch','test_patch']:(root/(name+'.diff')).write_text(row[name])
  # Local complete snapshots, no source modifications beyond the official code patch.
  import shutil
  shutil.copytree(root/'original',root/'fixed')
  subprocess.run(['git','apply','--check',str(root/'patch.diff')],cwd=root/'fixed',check=True,capture_output=True)
  subprocess.run(['git','apply',str(root/'patch.diff')],cwd=root/'fixed',check=True,capture_output=True)
 info={'instance_id':iid,'repo':row['repo'],'base_commit':row['base_commit'],'source_url':f"https://github.com/{row['repo']}/pull/{iid.rsplit('-',1)[1]}",'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'patch_sha256':hashlib.sha256(row['patch'].encode()).hexdigest()}
 print(json.dumps(info),flush=True);return info
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:out=list(pool.map(fetch,rows))
(R/'provenance.json').write_text(json.dumps(out,indent=2)+'\n')
