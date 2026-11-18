"""Read-only prior-result SHA expectations; mutable runtime caches are isolated."""
from pathlib import Path
import argparse,hashlib,json,time
from common import ROOT,PROJECT,PREVIOUS,load,sha,write_new

def stream_sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb')as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()

def check(mode):
 dest=ROOT/'audit'/f'preservation_{mode}.json'
 if dest.exists():
  row=load(dest);assert row['passed'],'A cached failed preservation audit cannot be accepted.';return row
 if mode=='before':
  prior=load(PREVIOUS/'audit/preservation_before.json');assert prior['passed']
  expected=dict(prior['files']);seal=load(PREVIOUS/'completed_result_seal.json');assert seal['status']=='complete'
  assert sha(PREVIOUS/'completed_result_seal.json')=='7d0b5a6061a04fff4847fd746a2febedca1a3ac1d6040a071b273b1f75a2382a'
  for rel,h in seal['files'].items():
   p=str(PREVIOUS/rel)
   if p in expected:assert expected[p]==h
   expected[p]=h
  for p in [PREVIOUS/'completed_result_seal.json',PREVIOUS/'audit/preservation_before.json',PROJECT/'paper/REVIEWBENCH48_ABLATION_F1_20261103_KO.md',PROJECT/'paper/REVIEWBENCH48_ABLATION_COMPLETION_20261103.json',PROJECT/'paper/LSP_INCREMENT_NOVELTY_RECHECK_20261103_KO.md']:
   expected[str(p)]=sha(p)
 else:expected=load(ROOT/'audit/preservation_before.json')['files']
 start=time.monotonic();errors=[];count=0;total=0
 for p,h in expected.items():
  try:
   total+=Path(p).stat().st_size
   if stream_sha(p)!=h:errors.append({'path':p,'reason':'SHA256 mismatch'})
  except Exception as e:errors.append({'path':p,'reason':type(e).__name__+': '+str(e)})
  count+=1
  if count%100000==0:print(json.dumps({'preservation':mode,'checked':count,'errors':len(errors)}),flush=True)
 row={'passed':not errors,'mode':mode,'checked':count,'errors':errors,'bytes_read':total,'seconds':time.monotonic()-start,'recorded_at':time.time(),'prior_path_writes':0}
 if mode=='before':row['files']=expected
 else:row['before_sha256']=sha(ROOT/'audit/preservation_before.json')
 write_new(dest,row);assert not errors
 print(json.dumps({k:v for k,v in row.items()if k!='files'}),flush=True);return row

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=('before','after'));a=p.parse_args();check(a.mode)
