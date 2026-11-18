"""Synthetic transport only: concurrency, uniqueness, failure, prompt identity.
Never calls a model or changes any frozen prior artifact.
"""
import ast,concurrent.futures,importlib.util,json,tempfile,threading,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('closure_harness_check',HERE/'experiment.py');e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
def ast_builder(path):return ast.dump(next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='build_prompt'),include_attributes=False)
assert ast_builder(HERE/'experiment.py')==ast_builder(e.OLD/'experiment.py')
original_root=e.R;calls=[];mutex=threading.Lock()
old_first=e.read(e.OLD/'raw'/'N01_a_P0Q0_r1_s1.json')['response']
old_final=e.read(e.OLD/'raw'/'N01_a_P0Q0_r1_s2.json')['response']
with tempfile.TemporaryDirectory(prefix='.harness-selfcheck-',dir=HERE) as td:
 e.R=Path(td)
 for name in ['schema_first.json','schema_final.json']:e.copy_equal(e.OLD/name,e.R/name)
 e.copy_equal(e.OLD/'cases'/'N01_a.json',e.R/'cases'/'N01_a.json')
 for arm in e.ARMS:
  for stage in ['initial','fixed']:e.copy_equal(e.OLD/'packets'/f'N01_a_{stage}.json',e.R/'packets'/arm/f'N01_a_{stage}.json')
 def fake_call(inp,root,model,schema):
  with mutex:calls.append(inp['run_id'])
  time.sleep(.04)
  result={'response':old_first if inp['run_id'].endswith('_s1') else old_final,'status':'ok','prompt_sha256':inp['prompt_sha256'],'model':model,'tool_audit':{'tool_call_count':0},'duration_seconds':.04,'synthetic_selfcheck':True}
  e.save(root/(inp['run_id']+'.trace.json'),{'synthetic_selfcheck':True})
  e.save(root/(inp['run_id']+'.json'),result)
  return result
 e.call_packet=fake_call
 jobs=[{'run_id':f'N01_a_{arm}_r{rep}','case_id':'N01','world':'a','arm':arm,'repeat':rep} for arm in e.ARMS for rep in range(1,5)]
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:records=list(pool.map(e.guarded_one,jobs))
 assert all(r['status']=='ok' and r['physical_calls']==2 for r in records)
 assert len(calls)==len(set(calls))==16 and e._max_active==8 and e._active==0
 assert e.one(jobs[0])['status']=='ok' and len(calls)==16,'duplicate trajectory executed'
 def throw(*args,**kwargs):raise RuntimeError('synthetic transport exception')
 e.call_packet=throw
 bad={'run_id':'N01_a_C0_r99','case_id':'N01','world':'a','arm':'C0','repeat':99}
 failed=e.guarded_one(bad)
 assert failed['status']=='infrastructure_error' and failed['physical_calls']==1 and e._active==0
 e.one(bad);assert len(calls)==16,'failed trajectory retried'
 orphan={'run_id':'N01_a_C1_r99','case_id':'N01','world':'a','arm':'C1','repeat':99}
 e.save(e.R/'claims'/(orphan['run_id']+'.json'),{'synthetic':True})
 assert e.one(orphan)['status']=='interrupted_attempt_not_retried' and len(calls)==16
e.R=original_root
result={'prompt_builder_ast_identical':True,'synthetic_completed_trajectories':8,'synthetic_transport_calls':16,'max_simultaneous_synthetic_calls':8,'no_duplicate_stages':True,'completed_jobs_not_replayed':True,'failed_jobs_not_retried':True,'incomplete_claim_not_retried':True,'exception_counter_released':True,'actual_model_calls':0,'temporary_artifacts_removed':True}
e.save(HERE/'HARNESS_SELF_CHECK.json',result);print(json.dumps(result,indent=2))
