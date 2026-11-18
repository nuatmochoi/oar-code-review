"""Operational recovery cohort; imports the unchanged sealed review harness.
All144jobs are newly sampled, not selected retries. Policy/prompts/packets stay
identical. Two model workers; globally spaced actual CLI starts; first explicit
provider/resource limit halts new admissions and drains accepted operations.
"""
import argparse,collections,concurrent.futures,hashlib,importlib.util,json,os,sys,threading,time
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];PRIOR=ROOT/'runs/lsp_agentic_preserved_20261023'
sys.path.insert(0,str(PRIOR));sys.path.insert(0,str(R));sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('sealed_preserved_harness',PRIOR/'experiment.py');e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
e.R=R;e.MODEL_WORKERS=2
read,save,sha=e.read,e.save,e.sha
PREVIOUS=e.PREVIOUS;packet_digest=e.packet_digest;build_prompt=e.build_prompt
START_INTERVAL_SECONDS=3.0
_transport=e.call_packet;_start_lock=threading.Lock();_last_start=None
def paced_call(row,root,model,schema):
 global _last_start
 with _start_lock:
  if _last_start is not None:
   delay=_last_start+START_INTERVAL_SECONDS-time.monotonic()
   if delay>0:time.sleep(delay)
  now=time.monotonic();_last_start=now
  save(e.R/'actual_cli_starts'/f'{row["run_id"]}.json',{'run_id':row['run_id'],'started_unix':time.time(),'started_monotonic':now,'minimum_start_interval_seconds':START_INTERVAL_SECONDS})
 return _transport(row,root,model,schema)
e.call_packet=paced_call

def prepare():
 old=read(PRIOR/'freeze.json');seal=read(PRIOR/'algorithm_seal.json')
 assert all(sha(ROOT/p)==h for p,h in {**old['files'],**seal['files']}.items())
 paths=[]
 for folder in ['cases','workspace_manifests','packets','lsp_fixed']:
  for src in sorted((PRIOR/folder).rglob('*.json')):
   dst=R/src.relative_to(PRIOR);e.copy_equal(src,dst);paths.append({'prior':str(src.relative_to(ROOT)),'recovery':str(dst.relative_to(ROOT)),'sha256':sha(src)})
 for name in ['selected.json','scopes.json','labels.json','witness_validation.json','schema_first.json','schema_final.json','grading_rules.json','agentic_policy.py']:
  src=PRIOR/name;e.copy_equal(src,R/name);paths.append({'prior':str(src.relative_to(ROOT)),'recovery':str((R/name).relative_to(ROOT)),'sha256':sha(src)})
 e.save_equal(R/'recovery_provenance.json',{'failed_predecessor':str(PRIOR),'predecessor_freeze_sha256':sha(PRIOR/'freeze.json'),'predecessor_result_seal_sha256':sha(PRIOR/'result_seal.json'),'predecessor_model_errors':read(PRIOR/'run_summary.json')['status_counts'],'all144_trajectories_resampled':True,'prior_model_responses_reused':False,'seed_and_fixed_packets_reused_byte_for_byte':True,'fixed_preparation_native144_reused_not_fresh':True,'fresh_recovery_preparation_native_calls':0,'files':paths})
 e.save_equal(R/'preparation_summary.json',{'worlds':24,'actual_new_native_requests':0,'cached_prior_fixed_native_requests':144,'reused_predecessor_seed_and_fixed_packets':True})
 return e.check()

def freeze():
 assert not list((R/'raw').glob('*_s[12].json'));e.check();seal=read(R/'algorithm_seal.json');assert all(sha(ROOT/p)==h for p,h in seal['files'].items())
 old=read(PRIOR/'freeze.json');files=[p for folder in ['cases','workspace_manifests','packets','lsp_fixed'] for p in (R/folder).rglob('*.json')]
 files.extend(R/name for name in ['selected.json','scopes.json','labels.json','witness_validation.json','schema_first.json','schema_final.json','grading_rules.json','PROTOCOL_KO.md','preflight.json','preparation_summary.json','recovery_provenance.json','algorithm_seal.json'])
 f={k:v for k,v in old.items() if k not in ['files','created_at','resource_circuit','model_admission','retry_policy']}
 f.update(created_at=e.utc(),model_workers=2,native_workers=2,minimum_global_CLI_start_interval_seconds=3.0,model_admission='same144pairedjobs; alternate available F/G; two model slots, at least3seconds between actual CLI invocation starts',resource_circuit='first explicit429 or OS resource-exhaustion stops all new model/native admissions; accepted operations drain; every unfinished trajectory becomes aborted_resource; no retry',retry_policy='no retries; entire144jobcohort is fresh, not selected failed-case retries',predecessor=str(PRIOR),fresh_fixed_preparation_native_calls=0,files={str(p.relative_to(ROOT)):sha(p) for p in sorted(files)})
 save(R/'freeze.json',f);print(json.dumps({'frozen_trajectories':144,'model_workers':2,'native_workers':2,'actual_start_interval_seconds':3}),flush=True)

def run_scheduler(jobs,model_workers=2):
 if list((e.R/'circuit_events').glob('*.json')):raise RuntimeError('This recovery cohort was halted by a resource limit; restarting admissions is prohibited.')
 ready={a:collections.deque() for a in ['F','G']};pending=collections.deque();states={};finished=[];mf={};nf={};halted=False;events=[];admissions=[];last='G' if not jobs or jobs[0]['arm']=='F' else 'F';next_admission=0.0
 def complete(job,status,error=None):
  rid=job['run_id'];state=states[rid];rec={**job,'status':status,'physical_calls':sum((e.R/'actual_cli_starts'/f'{rid}_s{s}.json').exists() for s in [1,2]),'elapsed_seconds':time.time()-state.get('started_unix',time.time())}
  if state.get('first'):rec.update(first_review=state['first']['response']['review'],request_count=len(state['first']['response']['context_requests']))
  if state.get('final'):rec['final_review']=state['final'].get('response',{})
  if error:rec['error']=str(error)
  if (e.R/'retrieval'/f'{rid}.json').exists():
   a=read(e.R/'retrieval'/f'{rid}.json')['audit'];rec.update(accepted_requests=sum(x.get('outcome')=='queued' for x in a.get('requests',[])),request_outcomes=dict(collections.Counter(x.get('outcome') for x in a.get('requests',[]))),declared_gap_outcomes=dict(collections.Counter(x.get('outcome') for x in a.get('declared_gaps',[]))))
  save(e.R/'trajectories'/f'{rid}.json',rec);finished.append(rec)
 def halt(signal,job,stage):
  nonlocal halted
  if signal and not halted:
   halted=True;event={'at':e.utc(),'unix':time.time(),'trigger_run_id':job['run_id'],'stage':stage,'explicit_signal':signal,'model_in_flight':len(mf),'native_in_flight':len(nf),'action':'stop new admissions; drain accepted operations; abort remaining full-cohort trajectories'};events.append(event);save(e.R/'circuit_events'/'001.json',event)
   for q in ready.values():q.clear()
   pending.clear();print(json.dumps({'recovery_halt':event}),flush=True)
 for job in jobs:
  rid=job['run_id'];states[rid]={}
  if (e.R/'trajectories'/f'{rid}.json').exists():finished.append(read(e.R/'trajectories'/f'{rid}.json'));continue
  if (e.R/'claims'/f'{rid}.json').exists():complete(job,'interrupted_attempt_not_retried');continue
  save(e.R/'claims'/f'{rid}.json',{'run_id':rid,'pid':os.getpid(),'enqueued_at':e.utc()});ready[job['arm']].append((job,1,None,None))
 with concurrent.futures.ThreadPoolExecutor(max_workers=2,thread_name_prefix='recovery-model') as models,concurrent.futures.ThreadPoolExecutor(max_workers=2,thread_name_prefix='recovery-native') as natives:
  while any(ready.values()) or pending or mf or nf:
   while not halted and pending and len(nf)<2:
    job,first,queued=pending.popleft();nf[natives.submit(e.agentic_retrieval,job,first,queued)]=job
   if not halted and len(mf)<2 and any(ready.values()) and time.monotonic()>=next_admission:
    preferred='F' if last=='G' else 'G';arm=preferred if ready[preferred] else last;last=arm;job,stage,packet,prior=ready[arm].popleft();rid=job['run_id']
    if stage==1:states[rid]['started_unix']=time.time();packet=read(e.R/'packets'/'seed'/f'{job["case_id"]}_{job["world"]}_initial.json')
    admissions.append({'run_id':rid,'stage':stage,'arm':arm,'unix':time.time(),'in_flight_before':len(mf),'admission_limit':2});mf[models.submit(e.model_stage,job,stage,packet,prior)]=(job,stage);next_admission=time.monotonic()+START_INTERVAL_SECONDS
   if not mf and not nf:
    if halted:break
    time.sleep(min(.05,max(.001,next_admission-time.monotonic())));continue
   done,_=concurrent.futures.wait(list(mf)+list(nf),timeout=.1,return_when=concurrent.futures.FIRST_COMPLETED)
   for future in done:
    if future in nf:
     job=nf.pop(future);rid=job['run_id']
     try:packet=future.result()
     except Exception as exc:halt(e.resource_signal(exception=exc),job,'native');complete(job,'native_retrieval_error',repr(exc));continue
     if not halted:ready['G'].appendleft((job,2,packet,states[rid]['first']['response']))
     continue
    job,stage=mf.pop(future);rid=job['run_id']
    try:record=future.result()
    except Exception as exc:halt(e.resource_signal(exception=exc),job,f's{stage}');complete(job,'infrastructure_error',repr(exc));continue
    if record['status']!='ok':halt(e.resource_signal(record=record,trace_path=e.R/'raw'/f'{rid}_s{stage}.trace.json'),job,f's{stage}');complete(job,'first_call_error' if stage==1 else 'final_call_error');continue
    if stage==2:states[rid]['final']=record;complete(job,'ok');continue
    states[rid]['first']=record
    if halted:continue
    first=record['response']
    if job['arm']=='G':pending.append((job,first,time.time()))
    else:
     packet=read(e.R/'packets'/'F'/f'{job["case_id"]}_{job["world"]}_fixed.json');e.store_retrieval(job,packet,[],True,{'native_trace_reference':str((e.R/'lsp_fixed'/f'{job["case_id"]}_{job["world"]}.json').relative_to(e.R)),'fixed_native_preparation_reused_from_predecessor':True});ready['F'].appendleft((job,2,packet,first))
 if halted:
  for job in jobs:
   if not (e.R/'trajectories'/f'{job["run_id"]}.json').exists():complete(job,'aborted_resource','First explicit provider/resource limit; no further stage admitted, retained in full denominator.')
 save(e.R/'scheduler_admissions.json',{'rows':admissions,'circuits':events,'ending_model_admission':0 if halted else 2,'model_slots_include_only_model_stages':True,'native_worker_limit':2,'global_actual_CLI_start_interval_seconds':START_INTERVAL_SECONDS,'aborted_jobs_retained':True})
 return finished,events,0 if halted else 2
e.run_scheduler=run_scheduler
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','check','freeze','run']);p.add_argument('--launch',action='store_true');a=p.parse_args()
 if a.command=='prepare':prepare()
 elif a.command=='check':e.check()
 elif a.command=='freeze':freeze()
 else:
  assert a.launch,'parent launch authorization required after seal/freeze'
  e.run()
