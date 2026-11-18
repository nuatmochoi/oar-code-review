from pathlib import Path
import hashlib,json,subprocess,time
ROOT=Path(__file__).resolve().parent

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def selected():
 paths=[*ROOT.glob('*.py'),ROOT/'PROTOCOL_KO.md',ROOT/'judge-config/models.json',ROOT/'audit/preservation.json',ROOT/'audit/source_provenance.json']
 paths+=list((ROOT/'prefixes').glob('*.json'))
 paths+=list((ROOT/'adapter').rglob('*.py'))+list((ROOT/'adapter').rglob('*.go'))+list((ROOT/'adapter').glob('Dockerfile*'))
 up=ROOT/'upstream'
 paths+=[up/'package-lock.json',up/'corpus/test/test.json',up/'corpus/manifest.json',up/'scripts/try-agent.sh']
 paths+=[ROOT/'audit/parser-tests.final.log',ROOT/'audit/budget-tests.final-preflight.log',ROOT/'audit/pi-transport-test.json',ROOT/'audit/toolchain-receipt.json',ROOT/'audit/native-readiness.json',ROOT/'audit/image-source-check.json',ROOT/'audit/guarded-tests.log',ROOT/'audit/budget-tests.current.log']
 paths+=list((up/'scripts/eval').rglob('*.ts'))+list((up/'scripts/golden').rglob('*.ts'))+list((up/'scripts/classifier').rglob('*.ts'))+list((up/'scripts/lib').rglob('*.ts'))
 paths+=list((up/'golden').rglob('*.json'))
 return sorted({p.resolve()for p in paths if p.is_file()})
def verify():
 f=json.loads((ROOT/'freeze.json').read_text())
 for p,h in f['files'].items():assert sha(Path(p))==h,('Frozen file changed',p)
 for attempt in range(3):
  try:
   image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',f['image_tag']],stderr=subprocess.DEVNULL).decode().strip();break
  except subprocess.CalledProcessError:
   if attempt==2:raise
   time.sleep(.2)
 assert image==f['image_id']
 return f
if __name__=='__main__':
 import sys
 if '--verify'in sys.argv:print(json.dumps({'valid':True,'files':len(verify()['files'])}))
 else:
  assert not(ROOT/'freeze.json').exists();assert not list((ROOT/'api').glob('*/request.json')),'Models were already called'
  value=dict(frozen_at=time.time(),image_tag='research-reviewbench-guarded:20261105',image_id=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}','research-reviewbench-guarded:20261105']).decode().strip(),upstream_commit=subprocess.check_output(['git','-C',str(ROOT/'upstream'),'rev-parse','HEAD']).decode().strip(),files={str(p):sha(p)for p in selected()},model='gpt-5.6-sol',reasoning='low',reviewer_calls_per_PR=1,model_output_cap=16384,hard_total_upper_usd=50,review_subcap_upper_usd=45,workers=3,PR_count=25,repeats=1,human_review=False,official_portal_submission=False)
  with (ROOT/'freeze.json').open('x')as f:json.dump(value,f,indent=2)
  print(json.dumps({'frozen':True,'files':len(value['files']),'image_id':value['image_id']}))
