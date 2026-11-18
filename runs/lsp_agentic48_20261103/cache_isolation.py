"""APFS clone-only writable caches and a no-old-project-write native boundary.

No model calls, network install, hardlinks, or ordinary-copy fallback.  Source
cache bytes are verified against the completed prior seal before cloning; the
clone is then checked against the same identities.  Mutable clone bytes are
never used to rewrite the prior authorities or frozen initial contexts.
"""
from pathlib import Path
import ctypes,hashlib,json,os,shutil,subprocess,time

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[1]
PREVIOUS=PROJECT/'runs/reviewbench48_ablation_20261103'
CACHE=ROOT/'cache'
FLOOR_BYTES=12*1024**3
MAX_CLONE_PHYSICAL_DELTA=2*1024**3

def load(p):return json.loads(Path(p).read_text())
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb')as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def put(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')
def free():return shutil.disk_usage(ROOT).free

def sandbox_profile():
 p=CACHE/'native-no-old-writes.sb'
 text='(version 1)\n(allow default)\n(deny file-write* (subpath '+json.dumps(str(PROJECT))+'))\n(allow file-write* (subpath '+json.dumps(str(ROOT))+'))\n'
 if p.exists():assert p.read_text()==text
 else:p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
 return p

def sandbox_command(command):
 p=sandbox_profile()
 return ['/usr/bin/sandbox-exec','-f',str(p),*map(str,command)]

def isolation_environment():
 assert (ROOT/'audit/cache_clone.json').is_file() and load(ROOT/'audit/cache_clone.json')['complete'],'Isolated cache clone is not ready'
 paths={
  'GOCACHE':CACHE/'go-build','GOMODCACHE':CACHE/'go-mod','GOPATH':CACHE/'gopath',
  'GOPLSCACHE':CACHE/'gopls','XDG_CACHE_HOME':CACHE/'xdg','XDG_CONFIG_HOME':CACHE/'config',
  'GOTMPDIR':CACHE/'tmp/go','TMPDIR':CACHE/'tmp','PYTHONPYCACHEPREFIX':CACHE/'python-bytecode',
 }
 for p in paths.values():p.mkdir(parents=True,exist_ok=True)
 return {**{k:str(p)for k,p in paths.items()},'PYTHONDONTWRITEBYTECODE':'1','GOENV':'off',
  'GOFLAGS':'-mod=readonly','GOTOOLCHAIN':'local','GOPROXY':'off','GOSUMDB':'off','GOTELEMETRY':'off'}

def probe_write_boundary(python):
 """Open an existing old file WITHOUT truncation/writing; expect denial."""
 old=PREVIOUS/'freeze.json';new=CACHE/'native-write-probe.json'
 code="import json,os,sys;old,new=sys.argv[1:];blocked=False\ntry:\n f=os.open(old,os.O_WRONLY);os.close(f)\nexcept PermissionError:blocked=True\nwith open(new,'x')as f:json.dump({'old_write_open_denied':blocked,'new_write_allowed':True},f)\nprint(json.dumps({'old_write_open_denied':blocked,'new_write_allowed':True}));sys.exit(0 if blocked else 9)"
 p=subprocess.run(sandbox_command([python,'-B','-c',code,str(old),str(new)]),text=True,capture_output=True,env={**os.environ,**isolation_environment()},timeout=20)
 result={'command':sandbox_command([python,'-B','-c',code,str(old),str(new)]),'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr,'old_file_sha256':sha(old),'model_calls':0}
 put(ROOT/'audit/cache_write_boundary.json',result)
 assert p.returncode==0 and json.loads(p.stdout)['old_write_open_denied']
 return result

def prepare():
 """Clone the exact sealed cache once. Safe resume reads completed receipts."""
 final=ROOT/'audit/cache_clone.json'
 if final.exists():return load(final)
 CACHE.mkdir(parents=True,exist_ok=True)
 seal=PREVIOUS/'completed_result_seal.json';all_expected=load(seal)['files']
 expected={p:h for p,h in all_expected.items()if p.startswith('native_validation/cache/go-build/')or p.startswith('native_validation/cache/go-mod/')}
 before=free();assert before>=FLOOR_BYTES
 # clonefile(2) is required and checked independently; cp is not allowed to
 # silently substitute byte copies or hardlinks when the volume cannot clone.
 test_source=PREVIOUS/'freeze.json';test_dest=CACHE/'apfs-clone-probe'
 if not test_dest.exists():
  libc=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True)
  if libc.clonefile(os.fsencode(test_source),os.fsencode(test_dest),0)!=0:raise OSError(ctypes.get_errno(),'APFS clonefile unavailable; ordinary copying is forbidden')
 assert sha(test_source)==sha(test_dest)and test_source.stat().st_ino!=test_dest.stat().st_ino
 start=time.monotonic();bad=[];source_bytes=0
 for rel,h in expected.items():
  p=PREVIOUS/rel;source_bytes+=p.stat().st_size
  if sha(p)!=h:bad.append(rel)
 pre={'complete':True,'seal_reference':{'path':str(seal),'sha256':sha(seal)},'expected_files':len(expected),'source_bytes':source_bytes,'errors':bad,'seconds':time.monotonic()-start,'free_bytes':free(),'model_calls':0}
 put(ROOT/'audit/cache_source_before.json',pre);assert not bad
 receipts=[]
 for name in ('go-build','go-mod'):
  source=PREVIOUS/'native_validation/cache'/name;target=CACHE/name
  receipt=ROOT/'audit'/('cache_clone_'+name+'.json')
  if receipt.exists():
   r=load(receipt);assert r['returncode']==0;receipts.append(r);continue
  assert not target.exists(),('Unreceipted cache destination; no overwrite/resume',str(target))
  cmd=['/bin/cp','-cRp',str(source),str(target)];started=time.monotonic();free_before=free()
  proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  stopped=None
  while proc.poll()is None:
   current=free()
   if current<FLOOR_BYTES or before-current>MAX_CLONE_PHYSICAL_DELTA:
    stopped='disk_floor_or_clone_delta';proc.terminate();break
   if time.monotonic()-started>1200:stopped='clone_timeout';proc.terminate();break
   time.sleep(.25)
  out,err=proc.communicate(timeout=20)
  r={'command':cmd,'source':str(source),'destination':str(target),'returncode':proc.returncode,'stdout':out,'stderr':err,'stop_reason':stopped,'seconds':time.monotonic()-started,'free_bytes_before':free_before,'free_bytes_after':free(),'clonefile_only':True,'ordinary_copy_fallback':False,'model_calls':0}
  put(receipt,r);receipts.append(r)
  print(json.dumps({'cache_clone':name,'returncode':r['returncode'],'seconds':r['seconds'],'free_bytes':r['free_bytes_after']}),flush=True)
  assert r['returncode']==0 and stopped is None
 started=time.monotonic();clone_bad=[];same_inode=[]
 for rel,h in expected.items():
  p=PREVIOUS/rel;dest=CACHE/str(Path(rel).relative_to('native_validation/cache'))
  if not dest.is_file()or sha(dest)!=h:clone_bad.append(str(dest))
  elif p.stat().st_ino==dest.stat().st_ino:same_inode.append(str(dest))
 final_data={'complete':not clone_bad and not same_inode,'method':'APFS cp -cRp clonefile, verified against the prior completed seal; no hardlinks or normal byte-copy fallback','seal_reference':pre['seal_reference'],'expected_files':len(expected),'source_bytes':source_bytes,'source_before_reference':{'path':str(ROOT/'audit/cache_source_before.json'),'sha256':sha(ROOT/'audit/cache_source_before.json')},'clones':receipts,'clone_verification_seconds':time.monotonic()-started,'clone_mismatches':clone_bad,'hardlink_collisions':same_inode,'free_bytes_before':before,'free_bytes_after':free(),'observed_volume_free_space_delta_bytes':before-free(),'maximum_admitted_physical_delta':MAX_CLONE_PHYSICAL_DELTA,'disk_floor_bytes':FLOOR_BYTES,'cache_mapping':{str(PREVIOUS/'native_validation/cache'/n):str(CACHE/n)for n in('go-build','go-mod')},'baseline_identity_digest':digest(expected),'model_calls':0}
 put(final,final_data);assert final_data['complete'];return final_data

if __name__=='__main__':print(json.dumps(prepare()))
