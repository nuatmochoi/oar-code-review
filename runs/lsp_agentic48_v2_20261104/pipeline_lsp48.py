"""Sequential frozen phases with resumable terminals and no model retries.

Each model stage owns its immutable receipt/terminal checks. This orchestrator
never repairs or reissues an existing call; it resumes only missing phases.
"""
from contextlib import contextmanager
from pathlib import Path
import fcntl,json,os,subprocess,sys,time
from common import ROOT,PROJECT,ARMS,REPEATS,load,sha,write_new

PAPER_COMPLETION=PROJECT/'paper/LSP_AGENTIC48_V2_COMPLETION_20261104.json'

def expected_reviews(root=ROOT):
 cases=load(root/'prepared/cases.json');cases=cases['cases']if isinstance(cases,dict)else cases
 assert len(cases)==48 and len({c['id']for c in cases})==48
 return {f'{c["id"]}_{a}_r{n}':(c['id'],a,n)for c in cases for a in ARMS for n in range(1,REPEATS+1)}

def reviewer_panel_ready(root=ROOT,expected=None):
 expected=expected if expected is not None else expected_reviews(root);paths={p.parent.name:p for p in(root/'results/reviews').glob('*/review.result.json')}
 assert set(paths)<=set(expected),'Unplanned reviewer terminal IDs'
 if set(paths)!=set(expected):return False
 for rid,p in paths.items():
  value=load(p);assert(value['case_id'],value['arm'],value['repeat'])==expected[rid]and value['review_id']==rid
 return True

def annotation_panel_ready(root=ROOT,expected=None):
 expected=expected if expected is not None else expected_reviews(root)
 good={p.stem:p for p in(root/'grades/final').glob('*.json')};bad={p.stem:p for p in(root/'grades/final_failures').glob('*.json')}
 assert not set(good)&set(bad),'Conflicting grade and failure terminals'
 assert(set(good)|set(bad))<=set(expected),'Unplanned annotation terminal IDs'
 if set(good)|set(bad)!=set(expected):return False
 for rid,p in{**good,**bad}.items():
  value=load(p);assert value['review_id']==rid
  assert value['annotation_status']==('complete'if rid in good else value['annotation_status'])
  if rid in bad:assert value['annotation_status']in('judge_failed','unattempted')
 return True

def common_pool_ready(root=ROOT,verifier=None):
 path=root/'private/common_judge_manifest.json'
 if path.exists():
  if verifier is None:
   from evaluate_lsp48 import verify_common
   verifier=verify_common
  verifier();return True
 # Even mechanical empty-finding grades are completed evaluations; rebuilding
 # a missing common seal after any annotation could change comparison facts.
 any_grades=any((root/'grades/final').glob('*.json'))or any((root/'grades/final_failures').glob('*.json'))
 assert not any_grades and not any((root/'evaluation').glob('**/initial.result.json')),'Evaluation exists without common seal; never rebuild its fact pool.'
 return False

def aggregate_ready(root=ROOT):
 path=root/'post/aggregate.json'
 if not path.exists():return False
 value=load(path);assert value['complete_reviewer_panel']and value['planned_reviews']==480 and value['planned_cases']==48
 assert set(value['by_arm'])==set(ARMS)and all(value['by_arm'][a]['reviews']==96 for a in ARMS)
 return True

def usage_ready(root=ROOT):
 path=root/'post/usage.json'
 if not path.exists():return False
 value=load(path);assert set(value['logical_by_arm'])==set(ARMS)and all(value['logical_by_arm'][a]['reviews']==96 for a in ARMS)
 return True

def mechanism_ready(root=ROOT):
 path=root/'post/mechanism.json';report=root/'post/MECHANISM_KO.md'
 if not path.exists()or not report.exists():return False
 value=load(path);assert value['complete_reviewer_and_grade_panel']and value['planned_reviews']==480
 return True

def completion_ready(root=ROOT,paper_completion=PAPER_COMPLETION):
 sealpath=root/'completed_result_seal.json'
 if not sealpath.exists()or not paper_completion.exists():return False
 seal=load(sealpath);done=load(paper_completion)
 assert seal['status']=='complete'and seal['planned_reviews']==480 and seal['planned_cases']==48
 assert done['complete']and done['planned_reviews']==480 and done['seal_sha256']==sha(sealpath)
 assert sha(seal['report']['path'])==seal['report']['sha256']and sha(done['report'])==done['report_sha256']
 return True

def _event(root,value):
 path=root/'logs/pipeline_events.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('a')as out:out.write(json.dumps(value,ensure_ascii=False)+'\n')

def phase(name,args,done,*,root=ROOT,runner=None):
 """One subprocess attempt, or verified skip. Never an automatic retry loop."""
 if done():
  _event(root,{'phase':name,'status':'skipped_verified_complete','recorded_at':time.time()});return
 command=[sys.executable,'-B',*args];log=root/'logs'/f'{name}.log';log.parent.mkdir(parents=True,exist_ok=True)
 event={'phase':name,'status':'running','started_at':time.time(),'command':command};_event(root,event)
 try:
  env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
  with log.open('a')as out:result=(runner or subprocess.run)(command,cwd=root,stdout=out,stderr=subprocess.STDOUT,env=env)
 except BaseException as error:
  _event(root,{**event,'status':'interrupted_or_wrapper_failed','ended_at':time.time(),'error':type(error).__name__+': '+str(error),'automatic_retry':False});raise
 event.update(ended_at=time.time(),returncode=result.returncode,status='subprocess_succeeded'if result.returncode==0 else'subprocess_failed',automatic_retry=False);_event(root,event);print(json.dumps(event),flush=True)
 assert result.returncode==0,(name,'failed; raw phase log preserved',str(log))
 assert done(),(name,'returned without a complete terminal artifact; no automatic retry')

@contextmanager
def exclusive_pipeline(root=ROOT):
 path=root/'logs/pipeline.lock';path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('a+')as lock:
  try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise RuntimeError('Another pipeline controller holds this run lock; no second launch.')
  try:yield
  finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

def mark_pipeline_finished(root=ROOT):
 outputs=[root/'post/aggregate.json',root/'post/usage.json',root/'post/mechanism.json',root/'post/MECHANISM_KO.md',root/'post/warning_mechanism.json',root/'post/WARNING_TRACE_KO.md',root/'RESULTS_KO.md']
 path=root/'post/pipeline_finished.json';refs={str(p):sha(p)for p in outputs}
 if path.exists():
  value=load(path);assert value['complete']and value['reviews']==480 and value['output_references']==refs;return
 write_new(path,{'complete':True,'reviews':480,'cases':48,'recorded_at':time.time(),'output_references':refs,'requires_final_preservation_and_seal_at_creation':True,'scope':'Reviewer/judge/aggregation stages finished. completed_result_seal.json is the final completion authority.'})

def main():
 from freeze_lsp48 import verify_freeze
 verify_freeze();expected=expected_reviews();assert len(expected)==480
 with exclusive_pipeline():
  phase('reviewers',['experiment_lsp48.py','run'],lambda:reviewer_panel_ready(expected=expected))
  assert reviewer_panel_ready(expected=expected),'No evaluation before all480 reviewer terminals.'
  phase('common_pool',['evaluate_lsp48.py','prepare-common'],common_pool_ready)
  assert common_pool_ready()
  phase('judges',['evaluate_lsp48.py','judge-run'],lambda:annotation_panel_ready(expected=expected))
  assert annotation_panel_ready(expected=expected),'No F1 before all480 annotation terminals.'
  phase('aggregate',['post/aggregate_lsp.py'],aggregate_ready)
  phase('usage',['post/usage_lsp.py'],usage_ready)
  phase('mechanism',['post/mechanism_lsp.py'],mechanism_ready)
  phase('warning_trace',['post/warning_mechanism.py'],lambda:(ROOT/'post/warning_mechanism.json').exists()and(ROOT/'post/WARNING_TRACE_KO.md').exists())
  phase('report',['post/report_lsp.py','--usage','post/usage.json'],lambda:(ROOT/'RESULTS_KO.md').exists())
  mark_pipeline_finished()
  phase('completion',['post/complete_lsp.py'],completion_ready)
  assert completion_ready()

if __name__=='__main__':main()
