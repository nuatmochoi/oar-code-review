from pathlib import Path
import copy,json,os,subprocess,time,unittest
from experiment import *

class ContractTests(unittest.TestCase):
 def test_whole_public_packets_and_bounds(self):
  for row in load(ROOT/'public/manifest.json'):
   p=load(row['path']);u=unit_packet(p);c=commit_packet(p)
   self.assertLessEqual(tokens(u),3500);self.assertLessEqual(tokens(c),2000)
   self.assertEqual(len(u['supplied'])+u['omitted_unit_count'],len(p['units']))
   self.assertEqual(len(c['supplied'])+c['omitted_commit_count'],len(p['commits']))
   self.assertTrue(all(v in p['commits']for v in c['supplied']))
 def test_only_one_scope_change(self):
  self.assertNotEqual(SYSTEM,SCOPE_SYSTEM)
  self.assertEqual(SYSTEM.split('Do not assume')[1],SCOPE_SYSTEM.split('Do not assume')[1])
  self.assertIn('zero to six',SCOPE_SYSTEM)
 def test_question_schema_and_ids(self):
  q=dict(id='Q1',unit_id='U0001',required_fact='real implementation',snapshot='HEAD',file='x.py',line=1,column=1,symbol='f',query='definition',kind='implementation_body',reason='inspect behavior')
  jsonschema.validate(dict(unit_assessments=[],questions=[q],reasoning='test'),PLAN)
  bad=copy.deepcopy(q);bad['query']='runtime_probe'
  with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(unit_assessments=[],questions=[bad],reasoning='test'),PLAN)
 def test_durable_cost_and_unknown_reservation(self):
  import budget_proxy,tempfile
  with tempfile.TemporaryDirectory()as folder:
   b=budget_proxy.Budget(Path(folder),cap=70)
   body=request_body('test','public',PLAN,6144)
   row,where=b.admit(body,'review');before=b.totals()[0]
   b.settle(row,where,None,'usage_unknown','HTTP 504')
   self.assertEqual(before,b.totals()[0])
   b2=budget_proxy.Budget(Path(folder),cap=70);self.assertEqual(before,b2.totals()[0])
  v=budget_proxy.estimated_cost(dict(input_tokens=1000,output_tokens=100,input_tokens_details=dict(cached_tokens=200,cache_write_tokens=300)))
  self.assertAlmostEqual(v['usd_list_rate_estimate'],.00558)
 def test_metadata_only_inventory(self):
  for row in load(ROOT/'public/manifest.json'):
   p=load(row['path']);self.assertEqual(set(p['case']['changed_files']),{u['path']for u in p['units']})

def native_smoke(index):
 packet=load(ROOT/'public'/f'{index:02d}.json');dest=ROOT/'audit'/f'native-smoke-{index:02d}';dest.mkdir(exist_ok=False)
 old=Path(packet['original_repo']).parents[1]/'out'/packet['key']/'agent/context'
 candidates=[]
 for file in sorted(old.glob('query-*.json')):
  q=load(file)['question']
  if q['snapshot']=='HEAD':
   q={**q,'unit_id':packet['units'][0]['id'],'required_fact':'public preflight static resolution'};candidates.append(q);break
 assert candidates
 write(dest/'input.json',dict(packet=packet,questions=candidates,source_cap=10000))
 repo=ROOT/'native-repos'/packet['pr']['nwo'].replace('/','_')
 cmd=['docker','run','--rm','--platform','linux/amd64','-v',str(repo)+':/work/repo','-v',str(dest)+':/work/out/agent','-v',str(ROOT/'native_collect.py')+':/experiment/native_collect.py:ro','-e','REVIEW_RUN_ROOT=/work/out/agent','--entrypoint','python',IMAGE,'/experiment/native_collect.py']
 with (dest/'docker.log').open('x')as f:r=subprocess.run(cmd,stdout=f,stderr=f,timeout=1000)
 assert r.returncode==0,(index,(dest/'docker.log').read_text()[-2500:])
 return load(dest/'complete.json')
if __name__=='__main__':
 suite=unittest.defaultTestLoader.loadTestsFromTestCase(ContractTests);r=unittest.TextTestRunner(verbosity=2).run(suite)
 if not r.wasSuccessful():raise SystemExit(1)
 admissions=[]
 for ref in load(ROOT/'public/manifest.json'):
  p=load(ref['path']);raw,schema,_,_=reviewer_input(p['case'],p['prefix']);body=request_body(raw['system'],raw['prompt']+'\n'+json.dumps(unit_packet(p))+'\n'+json.dumps(commit_packet(p)),schema,16384)
  estimate=int(tokens(body)*1.2)+16384+4096+45000
  admissions.append(dict(index=p['index'],reference=tokens(body),with_planner45k_margin=estimate,admitted=estimate<=TOKEN_CAP))
 assert all(x['admitted']for x in admissions),admissions
 smoke=[native_smoke(i)for i in (18,8)]
 value=dict(tests_passed=r.testsRun,paid_model_calls=0,all_25_preflight_admitted=True,admissions=admissions,native_smoke=smoke,old_files_preserved=all(sha(Path(p))==h for p,h in load(ROOT/'audit/preservation.json')['files'].items()))
 assert value['old_files_preserved'];write(ROOT/'audit/preflight.json',value)
 print(json.dumps(dict(tests_passed=r.testsRun,maximum_estimated_tokens=max(x['with_planner45k_margin']for x in admissions),native_smoke_passed=2,old_files_preserved=True)),flush=True)
