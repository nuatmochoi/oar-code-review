"""Prior sealed artifacts plus prior mutable-state snapshot, read-only checks.

The new run may mutate its build/cache metadata. Only selected new module source
is protected; no new GOCACHE/build outputs are frozen as experiment inputs.
"""
from pathlib import Path
import argparse,hashlib,json,os,time
ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[1]
PREVIOUS=ROOT.parent/'lsp_agentic48_20261103'
EXPECTED_PREVIOUS_SEAL='948c601fffa8f53f33690f87cdc0a632c20d1faf9ca40953e43a954be7f15704'

def load(p):return json.loads(Path(p).read_text())
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb')as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def put(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')
def walk_files(root):
 for base,dirs,names in os.walk(root):
  for name in names:
   p=Path(base)/name
   if p.is_file():yield p
def merge(target,refs):
 for p,h in refs.items():
  key=str(Path(p).absolute())
  if key in target:assert target[key]==h,('Expected hash conflict',key)
  target[key]=h

def expectations():
 prior=load(PREVIOUS/'audit/preservation_before.json');assert prior['passed']
 sealp=PREVIOUS/'completed_result_seal.json';assert sha(sealp)==EXPECTED_PREVIOUS_SEAL
 seal=load(sealp);assert seal['status']=='complete'
 resume=load(PREVIOUS/'audit/preservation_resume_20261104.json');assert resume['passed']
 known=dict(prior['files']);merge(known,{str(PREVIOUS/p):h for p,h in seal['files'].items()})
 merge(known,resume['freezer_external_references'])
 merge(known,resume['selected_clone_module_source']['file_references'])
 for p in (sealp,PREVIOUS/'RUN_STATE_KO.md',PROJECT/'paper/LSP_AGENTIC48_F1_20261104_KO.md',PROJECT/'paper/LSP_AGENTIC48_COMPLETION_20261104.json'):
  merge(known,{str(p):sha(p)})
 # v1 excludes these live directories from its result seal. Snapshot every
 # current file before v2 native execution so even its mutable caches/logs are
 # read-only in the new experiment and auditable afterward.
 extra={str(p)for folder in('cache','logs')for p in walk_files(PREVIOUS/folder)}
 sidecar=PROJECT/'runs/lsp_agentic48_diagnostics_20261104'
 if sidecar.exists():extra.update(str(p)for p in walk_files(sidecar))
 unknown=sorted(extra-set(known))
 newsource={}
 for p,h in resume['selected_clone_module_source']['file_references'].items():
  old=Path(p);assert old.is_relative_to(PREVIOUS/'cache/go-mod')
  dest=ROOT/'cache/go-mod'/old.relative_to(PREVIOUS/'cache/go-mod')
  newsource[str(dest)]=h
 return known,unknown,newsource

def check(mode):
 out=ROOT/'audit'/f'preservation_{mode}.json'
 if out.exists():r=load(out);assert r['passed'],'Cached failed preservation audit';return r
 if mode=='before':known,unknown,newsource=expectations()
 else:
  before=load(ROOT/'audit/preservation_before.json');assert before['passed']
  known=before['files'];unknown=[];newsource=before['selected_new_module_source_references']
 started=time.monotonic();errors=[];bytes_read=0;checked=0;symlinks={};same_inode=[];writebits=[]
 for p,expected in list(known.items())+[(p,None)for p in unknown]:
  try:
   path=Path(p);bytes_read+=path.stat().st_size;actual=sha(path)
   if expected is None:known[p]=actual
   elif actual!=expected:errors.append({'path':p,'reason':'SHA256 mismatch'})
   if path.is_symlink():symlinks[p]=os.readlink(path)
  except Exception as e:errors.append({'path':p,'reason':type(e).__name__+': '+str(e)})
  checked+=1
  if checked%100000==0:print(json.dumps({'preservation':mode,'prior_checked':checked,'errors':len(errors)}),flush=True)
 for p,expected in newsource.items():
  try:
   path=Path(p);bytes_read+=path.stat().st_size
   if sha(path)!=expected:errors.append({'path':p,'reason':'selected new module source SHA256 mismatch'})
   old=PREVIOUS/'cache/go-mod'/path.relative_to(ROOT/'cache/go-mod')
   if path.stat().st_ino==old.stat().st_ino:same_inode.append(p)
   if path.stat().st_mode&0o222:writebits.append(p)
  except Exception as e:errors.append({'path':p,'reason':type(e).__name__+': '+str(e)})
 if mode=='after':
  for p,target in before.get('prior_symlink_targets',{}).items():
   try:
    if not Path(p).is_symlink()or os.readlink(p)!=target:errors.append({'path':p,'reason':'prior symlink target changed'})
   except OSError as e:errors.append({'path':p,'reason':str(e)})
 row={'passed':not errors and not same_inode and not writebits,'mode':mode,'checked':checked,'selected_new_module_source_checked':len(newsource),
  'errors':errors,'hardlink_collisions':same_inode,'selected_source_write_bits':writebits,'bytes_read':bytes_read,
  'seconds':time.monotonic()-started,'recorded_at':time.time(),'prior_path_writes':0,'previous_seal_sha256':sha(PREVIOUS/'completed_result_seal.json'),
  'scope':'Inherited prior expectations + v1 sealed artifacts + v1 entire cache/log and diagnostic-state snapshot. New selected module source protected; new build/cache outputs excluded.'}
 if mode=='before':row.update(files=known,prior_symlink_targets=symlinks,selected_new_module_source_references=newsource,additional_prior_state_snapshot_files=len(unknown))
 else:row['before_sha256']=sha(ROOT/'audit/preservation_before.json')
 put(out,row);assert row['passed'],errors[:3]or same_inode[:3]or writebits[:3]
 print(json.dumps({k:v for k,v in row.items()if k not in('files','prior_symlink_targets','selected_new_module_source_references')}),flush=True);return row

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=('before','after'));a=p.parse_args();check(a.mode)
