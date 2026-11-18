"""Paired context-closure experiment; JSON-only model workers, no AST pickles.

prepare performs actual native retrieval only. freeze binds policy, packets and
job order before any fresh model call. run requires the explicit --launch flag.
"""
import argparse, ast, concurrent.futures, datetime, gc, hashlib, json, os, random
import statistics, sys, threading, time
from pathlib import Path

R=Path(__file__).resolve().parent; ROOT=R.parents[1]
OLD=ROOT/'runs/lsp_linked_adaptive_20261023'
sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import digest,token_count,FINAL_PROMPT
from ace_review.isolated_review import call_packet
ARMS=['C0','C1']; MODEL='gpt-5.6-sol'; PREP_WORKERS=2; MODEL_WORKERS=8

def read(p): return json.loads(Path(p).read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f: json.dump(x,f,ensure_ascii=False,indent=2)
def save_equal(p,x):
 p=Path(p)
 if p.exists(): assert read(p)==json.loads(json.dumps(x)),str(p)
 else: save(p,x)
def copy_equal(src,dst):
 src,dst=Path(src),Path(dst);dst.parent.mkdir(parents=True,exist_ok=True)
 if dst.exists(): assert dst.read_bytes()==src.read_bytes(),str(dst)
 else:
  with dst.open('xb') as f: f.write(src.read_bytes())
def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()

# This function is deliberately byte-for-byte identical to the old prompt
# builder; check() validates its AST and each C0 first-stage actual prompt.
def build_prompt(meta,nav,linked,first,prior=None):
 text=FINAL_PROMPT+'\nWrite concise checks linking the fixed input to the exercised branch, base versus head behavior, and cited supplied evidence. A narrow code-level causal proof is sufficient; do not require actual execution of the entire framework. Do not use an unrelated unknown to erase a supported local defect. A missing necessary binding remains unknown.\n'+meta['common']+'\n[EVIDENCE]\n'+nav.source()+'\n[STATIC OBSERVATIONS]\n'+nav.observations(linked)
 if first:text+='\nThis is the first review. Preserve concrete suspected defects as unresolved questions when evidence is missing. Also return context_requests for up to 6 useful native definition/references queries. Use ONLY visible head source locations, 1-based line and Unicode character column pointing at the identifier. Explain which missing connection each request would settle. Requests may be scheduled within the remaining query budget; do not pretend they executed. An empty request list is allowed.'
 else:text+='\n[PREVIOUS REVIEW: hypotheses, not authoritative evidence]\n'+json.dumps(prior,ensure_ascii=False)+'\nThis is the final review after bounded additional retrieval. Reassess previous concerns against the current supplied source. Preserve unresolved concerns explicitly; silence is not resolution. Do not invent execution or unseen source. No further retrieval is possible.'
 return text

class Packet:
 def __init__(self,data): self.data=data
 def source(self): return self.data['source']
 def observations(self,linked=False):
  assert not linked
  return self.data['plain']

def old_verify(p):
 p=Path(p);rel=str(p.relative_to(ROOT))
 known={**read(OLD/'freeze.json')['files'],**read(OLD/'algorithm_seal.json')['files']}
 assert rel in known and sha(p)==known[rel],('old frozen file mismatch',rel)
 return known[rel]

def copy_controls():
 for name in ['selected.json','scopes.json','labels.json','witness_validation.json','schema_first.json','schema_final.json']:
  old_verify(OLD/name);copy_equal(OLD/name,R/name)
 for cid in read(R/'labels.json'):
  for world in ['a','b']:
   stem=f'{cid}_{world}'
   old_verify(OLD/'cases'/f'{stem}.json');copy_equal(OLD/'cases'/f'{stem}.json',R/'cases'/f'{stem}.json')
   for kind in ['initial','fixed']:
    path=OLD/'packets'/f'{stem}_{kind}.json';old_verify(path);copy_equal(path,R/'packets'/'C0'/path.name)
   path=OLD/'workspace_manifests'/f'{stem}.json';old_verify(path)
   save_equal(R/'workspace_manifests'/path.name,{**read(path),'root':str(OLD/'workspaces'/stem),'read_only_reuse':True,'original_manifest_sha256':sha(path)})

def prepare_one(job):
 import context_policy as policy
 # This matches the old corpus selector. Neither labels nor witness output is
 # supplied to Navigator; directions are only used here to construct base/head.
 sys.path.insert(0,str(ROOT/'runs/lsp_callback_routes_20261002'))
 import shared
 cid,world,direction,iid=job;stem=f'{cid}_{world}'
 done=R/'preparation'/f'{stem}.json'
 if done.exists(): return read(done)
 started=time.monotonic();meta=read(R/'cases'/f'{stem}.json')
 b,h=('original','fixed') if direction=='forward' else ('fixed','original')
 base=shared.full_sources(OLD/'checkouts'/iid/b);head=shared.full_sources(OLD/'checkouts'/iid/h)
 manifest=read(R/'workspace_manifests'/f'{stem}.json');root=Path(manifest['root'])
 assert manifest['sources']=={f:digest(s) for f,s in head.items()},('head mismatch',stem)
 other_world='b' if world=='a' else 'a'
 base_manifest=read(R/'workspace_manifests'/f'{cid}_{other_world}.json')
 assert base_manifest['sources']=={f:digest(s) for f,s in base.items()},('base mismatch',stem)
 assert read(root/'pyrightconfig.json')==manifest['pyrightconfig']
 assert all(digest((root/f).read_text())==h for f,h in manifest['sources'].items()),('workspace mismatch',stem)
 scope=read(R/'scopes.json')[iid]['scope']
 nav=policy.Navigator(base,head,root,scope);initial_audit=nav.audit()
 okay=initial_audit['has_roots'] and initial_audit['root_coverage']
 assert okay,('root preparation failure',stem)
 client=policy.Client(root)
 try:
  nav.advance(client,policy.FIRST_QUERIES)
  first={'source':nav.source(),'plain':nav.observations(False),'audit':nav.audit()}
  first=json.loads(json.dumps(first)) # detach mutable audit lists, no whole-AST copy
  initial_native_count=len(client.trace)
  nav.advance(client,policy.QUERY_CAP)
  fixed={'source':nav.source(),'plain':nav.observations(False),'audit':nav.audit()}
 except Exception as exc:
  save(R/'preparation_errors'/f'{stem}.json',{'error':repr(exc),'trace':client.trace,'audit':nav.audit(),'elapsed_seconds':time.monotonic()-started})
  raise
 finally: client.close()
 save(R/'lsp_initial'/f'{stem}.json',{'trace':client.trace,'initialization':client.initialization,'stderr':client.errors,'initial_trace_entry_count':initial_native_count,'fixed_audit':fixed['audit'],'root':str(root),'native_retrieval_reused_across_repeats':True})
 for kind,packet in [('initial',first),('fixed',fixed)]:
  assert token_count(packet['source'])<=5200 and token_count(packet['plain'])<=800
  assert len(packet['audit']['queries'])<= (6 if kind=='initial' else 12)
  assert not any(q.get('error') for q in packet['audit']['queries']),('native query error',stem,kind)
  save(R/'packets'/'C1'/f'{stem}_{kind}.json',packet)
 result={'case_id':cid,'world':world,'preparation_ok':True,'elapsed_seconds':time.monotonic()-started,'initial_queries':len(first['audit']['queries']),'final_queries':len(fixed['audit']['queries']),'initial_source_tokens':token_count(first['source']),'final_source_tokens':token_count(fixed['source']),'actual_native_requests':sum(x.get('method') in ['textDocument/definition','textDocument/references'] for x in client.trace),'source_corpus':{'base':{f:digest(s) for f,s in base.items()},'head_manifest':str((R/'workspace_manifests'/f'{stem}.json').relative_to(R))}}
 save(done,result);print(json.dumps({'prepared':stem,**{k:v for k,v in result.items() if k not in ['source_corpus']}}),flush=True)
 return result

def prepare(workers=PREP_WORKERS):
 assert workers==PREP_WORKERS,'predeclared preparation concurrency is 2'
 assert not list((R/'raw').glob('*_s[12].json')),'cannot prepare after fresh model calls'
 copy_controls();labels=read(R/'labels.json');assert len(labels)==12
 assert read(R/'witness_validation.json')['all_contracts_verified']
 if (R/'preparation_summary.json').exists(): return check()
 jobs=[(cid,w,row['directions'][w],row['instance_id']) for cid,row in labels.items() for w in ['a','b']]
 gc.disable();started=time.monotonic();identifier=str(time.time_ns());started_at=utc()
 save(R/'preparation_runtime'/f'{identifier}.started.json',{'started_at':started_at,'workers':workers,'pid':os.getpid(),'worlds':24})
 try:
  with concurrent.futures.ThreadPoolExecutor(max_workers=workers,thread_name_prefix='native-prepare') as pool:
   results=[]
   for start in range(0,len(jobs),workers):
    results.extend(pool.map(prepare_one,jobs[start:start+workers]))
    # The old recursive AST indexer closes over itself. Collect only after a
    # complete two-world batch, when neither worker still owns a Navigator.
    # This avoids per-node GC stalls without retaining all 24 repository ASTs.
    gc.collect()
 except Exception as exc:
  save(R/'preparation_runtime'/f'{identifier}.failed.json',{'started_at':started_at,'ended_at':utc(),'elapsed_seconds':time.monotonic()-started,'error':repr(exc)})
  raise
 elapsed=time.monotonic()-started
 summary={'worlds':24,'workers':workers,'policy':'context_policy.py','cyclic_gc_disabled_during_navigation':True,'explicit_gc_between_two_world_batches':True,'started_at':started_at,'ended_at':utc(),'preparation_wall_seconds':elapsed,'results':[{'case_id':x['case_id'],'world':x['world'],'initial_queries':x['initial_queries'],'final_queries':x['final_queries'],'final_source_tokens':x['final_source_tokens']} for x in results]}
 save(R/'preparation_runtime'/f'{identifier}.finished.json',summary)
 save(R/'preparation_summary.json',summary)
 print(json.dumps({'preparation_wall_seconds':elapsed,'worlds':len(results)}),flush=True)
 return check()

def check():
 def function_ast(p):
  return ast.dump(next(n for n in ast.parse(p.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='build_prompt'),include_attributes=False)
 assert function_ast(R/'experiment.py')==function_ast(OLD/'experiment.py'),'prompt builder changed'
 old_verify(OLD/'experiment.py');labels=read(R/'labels.json');rows=[]
 for cid in labels:
  for w in ['a','b']:
   meta=read(R/'cases'/f'{cid}_{w}.json')
   for arm in ARMS:
    for kind,limit in [('initial',6),('fixed',12)]:
     p=R/'packets'/arm/f'{cid}_{w}_{kind}.json';x=read(p);a=x['audit']
     assert token_count(x['source'])<=5200 and token_count(x['plain'])<=800,(p,'tokens')
     assert len(a['queries'])<=limit,(p,'queries')
     assert a['root_coverage'] and a['has_roots'],(p,'roots')
     assert not any(q.get('error') for q in a['queries']),(p,'native errors')
     assert all(q['depth']<=3 for q in a['queries']),(p,'depth')
     if arm=='C0':
      old=OLD/'packets'/p.name;old_verify(old);assert p.read_bytes()==old.read_bytes(),(p,'baseline bytes')
     rows.append({'case_id':cid,'world':w,'arm':arm,'stage':kind,'packet_sha256':sha(p),'source_tokens':token_count(x['source']),'observation_tokens':token_count(x['plain']),'queries':len(a['queries'])})
   first=read(R/'packets'/'C0'/f'{cid}_{w}_initial.json');prompt=build_prompt(meta,Packet(first),False,True)
   old_first=read(OLD/'inputs'/f'{cid}_{w}_P0Q0_r1_s1.json')
   assert prompt==old_first['prompt'] and digest(prompt)==old_first['prompt_sha256'],(cid,w,'C0 first prompt')
   fixed=Packet(read(R/'packets'/'C0'/f'{cid}_{w}_fixed.json'))
   prior=read(OLD/'raw'/f'{cid}_{w}_P0Q0_r1_s1.json')['response'];prior={k:v for k,v in prior.items() if k!='context_requests'}
   assert build_prompt(meta,fixed,False,False,prior)==read(OLD/'inputs'/f'{cid}_{w}_P0Q0_r1_s2.json')['prompt'],(cid,w,'C0 final template')
 result={'all_checks_passed':True,'packets':len(rows),'baseline_packet_bytes_unchanged':True,'baseline_first_prompts_exact_old':24,'baseline_final_templates_exact_old_with_same_prior':24,'prompt_builder_ast_identical':True,'rows':rows,'fresh_model_calls':len(list((R/'raw').glob('*_s[12].json')))}
 if not (R/'freeze.json').exists(): save_equal(R/'preflight.json',result)
 print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)
 return result

def frozen_paths():
 paths=[]
 for folder in ['cases','packets','workspace_manifests','preparation','preparation_runtime','lsp_initial']:
  paths.extend(p for p in (R/folder).rglob('*.json') if p.is_file())
 paths.extend(R/name for name in ['selected.json','scopes.json','labels.json','witness_validation.json','schema_first.json','schema_final.json','preparation_summary.json','preflight.json','algorithm_seal.json','PROTOCOL_KO.md','grading_rules.json'])
 return sorted(paths)

def freeze():
 assert not list((R/'raw').glob('*_s[12].json')),'freeze must precede fresh model calls'
 check();seal=read(R/'algorithm_seal.json')
 assert all(sha(ROOT/p)==h for p,h in seal['files'].items()),'algorithm seal mismatch'
 labels=read(R/'labels.json');rng=random.Random(2026102302)
 blocks=[(cid,w,rep) for cid in sorted(labels) for w in ['a','b'] for rep in [1,2,3]];rng.shuffle(blocks);jobs=[]
 for block,(cid,w,rep) in enumerate(blocks):
  arms=list(ARMS);rng.shuffle(arms)
  jobs.extend({'run_id':f'{cid}_{w}_{arm}_r{rep}','case_id':cid,'world':w,'arm':arm,'repeat':rep,'block':block} for arm in arms)
 assert len(jobs)==144 and len({x['run_id'] for x in jobs})==144
 save(R/'freeze.json',{'created_at':utc(),'jobs':jobs,'logical_reviews':144,'max_physical_calls':288,'model':MODEL,'reasoning_effort':'low','repeats':3,'model_workers':MODEL_WORKERS,'prepare_workers':PREP_WORKERS,'source_tokens_cap':5200,'observation_tokens_cap':800,'first_queries_cap':6,'total_queries_cap':12,'depth_cap':3,'first_and_final_prompt_builder':'identical to prior run','arm_schedule':'randomized blocks of (PR, world, repeat), randomized two-arm order within each block','schedule_seed':2026102302,'source_context_policy':'C0 old sealed packets; C1 repaired native context closure','model_gap_requests':'recorded but ignored in both fixed-schedule arms','retry_policy':'no automatic retries; all attempted trajectories retain denominator','old_freeze_sha256':sha(OLD/'freeze.json'),'files':{str(p.relative_to(ROOT)):sha(p) for p in frozen_paths()},'model_calls_for_this_comparison_before_freeze':0,'prior_case_exposure':'same previously evaluated 12 PRs; intervention informed by old error analysis; not new holdout'})
 print(json.dumps({'frozen_trajectories':144,'fresh_calls_planned':288,'model_workers':8}),flush=True)

_lock=threading.Lock();_active=0;_max_active=0;_completed=0;_started=0
def call_once(inp,schema):
 global _active,_max_active,_completed,_started
 key=inp['run_id'];started=time.time()
 with _lock:
  _active+=1;_started+=1;_max_active=max(_max_active,_active);active=_active;ordinal=_started
 try:
  save(R/'call_metrics'/f'{key}.started.json',{'run_id':key,'started_unix':started,'started_at':utc(),'worker':threading.current_thread().name,'in_flight_at_start':active,'attempt_ordinal':ordinal,'input_sha256':sha(R/'inputs'/f'{key}.json'),'prompt_sha256':inp['prompt_sha256']})
  result=call_packet(inp,R/'raw',MODEL,schema)
  save(R/'call_metrics'/f'{key}.finished.json',{'run_id':key,'started_unix':started,'ended_unix':time.time(),'elapsed_seconds':time.time()-started,'status':result['status'],'input_sha256':sha(R/'inputs'/f'{key}.json'),'raw_record_sha256':sha(R/'raw'/f'{key}.json'),'trace_sha256':sha(R/'raw'/f'{key}.trace.json'),'response_sha256':digest(json.dumps(result.get('response'),ensure_ascii=False,sort_keys=True))})
  return result
 finally:
  with _lock:
   _active-=1;_completed+=1
   print(json.dumps({'progress_calls_finished':_completed,'calls_started':_started,'active_calls':_active,'max_concurrent_calls':_max_active}),flush=True)

def one(job):
 rid=job['run_id'];out=R/'trajectories'/f'{rid}.json'
 if out.exists(): return read(out)
 # A started incomplete job is never replayed: its outcome remains an error.
 claim=R/'claims'/f'{rid}.json'
 if claim.exists():
  record={**job,'status':'interrupted_attempt_not_retried','physical_calls':sum((R/'call_metrics'/f'{rid}_s{i}.started.json').exists() for i in [1,2])};save(out,record);return record
 save(claim,{'run_id':rid,'started_at':utc(),'pid':os.getpid(),'worker':threading.current_thread().name})
 started=time.monotonic();cid,w,arm=job['case_id'],job['world'],job['arm'];meta=read(R/'cases'/f'{cid}_{w}.json');record=dict(job)
 first_path=R/'packets'/arm/f'{cid}_{w}_initial.json';fixed_path=R/'packets'/arm/f'{cid}_{w}_fixed.json'
 initial=read(first_path);fixed=read(fixed_path)
 def stage_input(stage,text,packet_path):
  inp={**job,'run_id':rid+f'_s{stage}','prompt':text,'prompt_sha256':digest(text),'prompt_tokens':token_count(text),'packet_sha256':sha(packet_path)}
  save(R/'inputs'/f'{rid}_s{stage}.json',inp);return inp
 inp=stage_input(1,build_prompt(meta,Packet(initial),False,True),first_path)
 first=call_once(inp,read(R/'schema_first.json'));record['physical_calls']=1
 if first['status']!='ok':
  record.update(status='first_call_error',elapsed_seconds=time.monotonic()-started);save(out,record);return record
 prior=first['response'];final_prior={k:v for k,v in prior.items() if k!='context_requests'}
 save(R/'retrieval'/f'{rid}.json',{'source':fixed['source'],'observations':fixed['plain'],'audit':fixed['audit'],'actual_additional_native_trace':[],'fixed_navigation_reused':True,'packet_sha256':sha(fixed_path),'native_trace_reference':str((R/'lsp_initial'/f'{cid}_{w}.json').relative_to(R)) if arm=='C1' else str(OLD/'lsp_initial'/f'{cid}_{w}.json'),'context_requests_ignored':True})
 inp=stage_input(2,build_prompt(meta,Packet(fixed),False,False,final_prior),fixed_path)
 final=call_once(inp,read(R/'schema_final.json'))
 record.update(status='ok' if final['status']=='ok' else 'final_call_error',physical_calls=2,first_review=prior['review'],final_review=final.get('response',{}),request_count=len(prior['context_requests']),accepted_requests=0,elapsed_seconds=time.monotonic()-started,first_packet_sha256=sha(first_path),final_packet_sha256=sha(fixed_path))
 save(out,record);return record

def guarded_one(job):
 try: return one(job)
 except Exception as exc:
  out=R/'trajectories'/f'{job["run_id"]}.json'
  calls=sum((R/'call_metrics'/f'{job["run_id"]}_s{i}.started.json').exists() for i in [1,2])
  rec={**job,'status':'infrastructure_error','error':repr(exc),'physical_calls':calls}
  if not out.exists(): save(out,rec)
  print(json.dumps({'run_id':job['run_id'],'status':'infrastructure_error','error':repr(exc)}),flush=True)
  return rec

def run(workers=MODEL_WORKERS):
 f=read(R/'freeze.json');seal=read(R/'algorithm_seal.json')
 assert workers==f['model_workers']==MODEL_WORKERS,'workers must match frozen protocol'
 assert all(sha(ROOT/p)==h for p,h in {**f['files'],**seal['files']}.items()),'frozen artifact mismatch'
 for folder in ['inputs','raw','trajectories','retrieval','claims','call_metrics']:(R/folder).mkdir(exist_ok=True)
 # Process lock prevents two parent invocations from racing on the same jobs.
 lock_path=R/'runner.lock';lock_fd=os.open(lock_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
 os.write(lock_fd,str(os.getpid()).encode());os.close(lock_fd)
 started=time.time();identifier=str(time.time_ns());start={'started_at':utc(),'started_unix':started,'pid':os.getpid(),'workers':workers,'model':MODEL,'fresh_plan_calls':288,'AST_pickle_loads_in_model_loop':0}
 save(R/'runtime'/f'{identifier}.started.json',start);print(json.dumps(start),flush=True)
 try:
  with concurrent.futures.ThreadPoolExecutor(max_workers=workers,thread_name_prefix='review') as pool:
   outcomes=list(pool.map(guarded_one,f['jobs']))
  elapsed=time.time()-started
  result={**start,'ended_at':utc(),'ended_unix':time.time(),'elapsed_seconds':elapsed,'calls_started_this_process':_started,'calls_finished_this_process':_completed,'maximum_simultaneous_model_calls':_max_active,'completed_trajectories':len(outcomes),'status_counts':{k:sum(x['status']==k for x in outcomes) for k in sorted({x['status'] for x in outcomes})},'calls_per_minute':_completed*60/elapsed if elapsed else 0,'no_retry':True}
  save(R/'runtime'/f'{identifier}.finished.json',result);save(R/'run_summary.json',result);print(json.dumps(result),flush=True)
 finally: lock_path.unlink(missing_ok=True)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('command',choices=['prepare','check','freeze','run']);parser.add_argument('--workers',type=int);parser.add_argument('--launch',action='store_true');args=parser.parse_args()
 if args.command=='prepare':prepare(args.workers or PREP_WORKERS)
 elif args.command=='check':check()
 elif args.command=='freeze':freeze()
 else:
  assert args.launch,'run requires explicit --launch after parent authorizes frozen experiment'
  run(args.workers or MODEL_WORKERS)
