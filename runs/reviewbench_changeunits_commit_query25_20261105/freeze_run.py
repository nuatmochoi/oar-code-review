from pathlib import Path
import hashlib,json,subprocess,time
ROOT=Path(__file__).resolve().parent
IMAGE='sha256:5ca2d36ae91443d988bbfe29edd47c71c67a80485dca9332ee2ecfaee26f817a'
CACHE={}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def verify():
 f=json.loads((ROOT/'freeze.json').read_text())
 for p,h in f['files'].items():
  x=Path(p);st=x.stat();stamp=(st.st_size,st.st_mtime_ns,st.st_ctime_ns)
  if CACHE.get(p)!=stamp:
   assert sha(x)==h,('Frozen file changed',p);CACHE[p]=stamp
 return f
if __name__=='__main__':
 import sys
 if '--verify'in sys.argv:print(json.dumps(dict(valid=True,files=len(verify()['files']))))
 else:
  assert not(ROOT/'freeze.json').exists();assert not list((ROOT/'api').glob('*/request.json'))
  actual=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',IMAGE]).decode().strip();assert actual==IMAGE
  paths=[*ROOT.glob('*.py'),ROOT/'PROTOCOL_KO.md',ROOT/'judge-config/models.json',*list((ROOT/'public').glob('*.json')),*list((ROOT/'vendor').rglob('*.py'))]
  paths+=list((ROOT/'upstream/scripts').rglob('*.ts'))+list((ROOT/'upstream/golden').glob('*.json'))+[ROOT/'upstream/package-lock.json',ROOT/'upstream/corpus/test/test.json',ROOT/'audit/preservation.json',ROOT/'audit/preflight.json']
  f=dict(frozen_at=time.time(),files={str(p):sha(p)for p in sorted(set(paths))if p.is_file()},native_image=IMAGE,PRs=25,arms=['A','B','C','S'],repeats=1,model='gpt-5.6-sol',reasoning='low',logical_model_token_cap=750000,logical_calls=2,source_context_cap=12000,hard_total_upper_usd=70,workers=3,human_review=0)
  with (ROOT/'freeze.json').open('x')as out:json.dump(f,out,indent=2)
  print(json.dumps(dict(frozen=True,files=len(f['files']))))
