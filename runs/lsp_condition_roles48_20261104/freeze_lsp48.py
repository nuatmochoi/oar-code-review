"""Freeze reporting-only intervention and protect all referenced old artifacts."""
from pathlib import Path
import argparse,time,json
from common import ROOT,PREVIOUS_METHOD,ARMS,REPEATS,MODEL,BUDGETS,load,sha,write_new

def preservation(phase):
 oldseal=load(PREVIOUS_METHOD/'completed_result_seal.json');assert oldseal['status']=='complete'
 refs={str(PREVIOUS_METHOD/p):h for p,h in oldseal['files'].items()}
 refs[str(PREVIOUS_METHOD/'completed_result_seal.json')]=sha(PREVIOUS_METHOD/'completed_result_seal.json')
 refs.update(load(PREVIOUS_METHOD/'dependency_freeze.json')['files'])
 for p,h in refs.items():assert sha(p)==h,('Prior sealed/dependency file changed',p)
 result={'passed':True,'phase':phase,'checked':len(refs),'old_completed_seal_sha256':sha(PREVIOUS_METHOD/'completed_result_seal.json'),
  'model_calls':0,'new_native_calls':0,'scope':'Prior completed v2 sealed artifacts and its full declared dependency closure. No native tools, source writes, cache clones or environment modification in this reporting-only experiment.'}
 path=ROOT/'audit'/f'preservation_{phase}.json'
 if path.exists():assert load(path)==result
 else:write_new(path,result)
 return refs,result

def freeze():
 if(ROOT/'freeze.json').exists():return verify_freeze()
 assert not list((ROOT/'results').glob('**/initial.result.json'))and not list((ROOT/'evaluation').glob('**/initial.result.json'))
 assert load(ROOT/'audit/replay_preflight.json')['complete']
 assert load(ROOT/'audit/test_receipt.json')['passed']
 refs,_=preservation('before')
 write_new(ROOT/'dependency_freeze.json',{'files':refs,'recorded_at':time.time(),'scope':'Read-only prior completed artifact and dependency closure; presence is not new tool use.'})
 paths=list(ROOT.glob('*.py'))+[ROOT/'PROTOCOL_KO.md',ROOT/'dependency_freeze.json']
 for folder in('tests','post','prepared','private','contexts','audit'):
  paths.extend(p for p in(ROOT/folder).rglob('*')if p.is_file()and'__pycache__'not in p.parts and p.suffix in('.py','.json','.md'))
 files={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(paths))}
 write_new(ROOT/'freeze.json',{'recorded_at':time.time(),'files':files,'cases':48,'arms':ARMS,'repeats':REPEATS,'planned_reviews':480,
  'budgets':BUDGETS,'model':MODEL,'reviewer_model_calls_before_freeze':0,'judge_model_calls_before_freeze':0,
  'intervention':'Role-aware final reporting only, same actual old plans and source acquisitions. No new native/planner calls.','evaluation':'Unchanged legacy semantic criteria; fresh common source pool and exact-input grouping for all arms, no old semantic-label reuse.'})
 return verify_freeze()

def verify_freeze():
 f=load(ROOT/'freeze.json');assert f['planned_reviews']==480
 for p,h in f['files'].items():assert sha(ROOT/p)==h,('Frozen input changed',p)
 deps=load(ROOT/'dependency_freeze.json')['files']
 for p,h in deps.items():assert sha(p)==h,('Frozen prior dependency changed',p)
 return {'frozen_files':len(f['files']),'external_references':len(deps),'freeze_sha256':sha(ROOT/'freeze.json')}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=('freeze','verify'));a=p.parse_args();print(json.dumps(freeze()if a.action=='freeze'else verify_freeze()))
