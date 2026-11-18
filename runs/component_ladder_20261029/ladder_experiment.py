#!/usr/bin/env python3
"""Fresh component-ladder execution; only explicit run/worker --launch-model calls a model."""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, json, os, random, re, subprocess, sys, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.pycache_prefix=str(HERE/'.pycache')
sys.path.insert(0,str(HERE))
import oar_core as oc
import cli_transport as ct
import oar_product_adapter as common
import oar_fixture as fx
import scheduler_support as rs
ARMS=('diff','ast','lsp','search')
DISPLAY={'diff':'공개 계약+diff','ast':'변경 파일 AST 문맥 추가','lsp':'고정 LSP 교차 파일 문맥 추가','search':'리뷰 요청에 따른 추가 코드 탐색'}
KEYS=tuple(f'N{i:02d}_{w}' for i in range(1,13) for w in ('a','b'))
SHUFFLE_SEED=2026102901
CODE=('oar_experiment.py','ladder_experiment.py','scheduler_support.py','packet_builder.py','ladder_adapter.py',
      'oar_core.py','cli_transport.py','oar_product_adapter.py','oar_controller.py','oar_retrieval.py','oar_fixture.py','fake_cli_child.py',
      'test_packet_builder.py','test_ladder_adapter.py','test_ladder_experiment.py','parent_grading/aggregate.py','parent_grading/test_aggregate.py')
DOCS=('PROTOCOL_KO.md','parent_audit/product_snapshot_20261026.json','parent_audit/lineage_start.json',
      'parent_audit/GRADING_BOUNDARIES.md','parent_audit/GRADING_EXECUTION_KO.md','parent_audit/GRADER_INSTRUCTIONS_KO.md',
      'parent_audit/preservation_before.json','parent_audit/preservation_before.md','parent_grading/analysis_spec.json')
TESTS=('test_packet_builder.py','test_ladder_adapter.py','test_ladder_experiment.py','parent_grading/test_aggregate.py')
class ExperimentError(RuntimeError):pass

def load_modules():
 import packet_builder as pb
 import ladder_adapter as la
 return pb,la

def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def new(p,value):ct.write_new(Path(p),value)
def code_hashes():
 missing=[p for p in CODE if not (HERE/p).is_file()]
 if missing:raise ExperimentError(f'missing code: {missing}')
 return {p:sha(HERE/p) for p in CODE}
def code_sha():return oc._sha(code_hashes())
def preparation_dependencies():
 return {'code':{p:sha(HERE/p) for p in ('packet_builder.py','oar_product_adapter.py','oar_core.py')},
         'algorithm_dependencies':rs.ace_dependency_files(),'product':common.verify_snapshot()}
def jobs():
 rng=random.Random(SHUFFLE_SEED);ks=list(KEYS);rng.shuffle(ks);out=[]
 for key in ks:
  aa=list(ARMS);rng.shuffle(aa)
  for arm in aa:
   case,world=key.split('_');out.append(dict(review_id=f'{key}_{arm}',key=key,case=case,world=world,arm=arm,order=len(out)))
 return out

def prepare_one(key):
 if key not in KEYS:raise ExperimentError('invalid key')
 if (HERE/'freeze.json').exists():raise ExperimentError('already frozen')
 pb,_=load_modules();packet=pb.prepare_packet(key)
 case,world=key.split('_');data=common.load_case(case,world)
 verified=common.verify_workspace(data['workspace_root'],data['workspace_sources'])
 record={'key':key,'authoritative':{**data['authoritative'],'workspace_tree_sha256':verified['tree_sha256']},'workspace':verified,'packet':packet,
         'packet_builder_sha256':sha(HERE/'packet_builder.py'),'preparation_dependencies':preparation_dependencies(),'prepared_at':time.time()}
 path=HERE/'prepared/inputs'/f'{key}.json';path.parent.mkdir(parents=True,exist_ok=True);new(path,record)
 return {'key':key,'path':str(path),'sha256':sha(path),'initial_queries':packet.get('initial_queries')}

def prepare():
 if (HERE/'freeze.json').exists():raise ExperimentError('already frozen')
 folder=HERE/'prepared';folder.mkdir(exist_ok=True);(folder/'inputs').mkdir(exist_ok=True)
 if (folder/'manifest.json').exists():raise ExperimentError('manifest already exists; inspect rather than overwrite')
 def one(key):
  path=folder/'inputs'/f'{key}.json'
  if path.exists():
   r=read(path)
   if r['packet_builder_sha256']!=sha(HERE/'packet_builder.py'):raise ExperimentError('old packet policy; archive preparation before rebuilding')
   if r.get('preparation_dependencies')!=preparation_dependencies():raise ExperimentError('preparation dependencies changed')
   return {'key':key,'reused_preparation_only':True,'returncode':0}
  cp=subprocess.run([sys.executable,'-B',str(HERE/'oar_experiment.py'),'prepare-one',key],cwd=HERE,capture_output=True,text=True,timeout=300,
                    env={**os.environ,'PYTHONPYCACHEPREFIX':str(HERE/'.pycache')})
  (folder/f'{key}.stdout.log').write_text(cp.stdout);(folder/f'{key}.stderr.log').write_text(cp.stderr)
  return {'key':key,'returncode':cp.returncode,'stderr_tail':cp.stderr[-1200:]}
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:out=list(pool.map(one,KEYS))
 if any(r['returncode'] for r in out):
  new(folder/f'prepare_failed_{int(time.time())}.json',out);raise ExperimentError('preparation failures; preserved logs')
 packets={k:read(folder/'inputs'/f'{k}.json') for k in KEYS}
 qtotal=sum(int(r['packet']['initial_queries']['count']) for r in packets.values())
 physical={}
 for output,field in {'physical_native_rpc_queries_total':'native_rpc_queries','native_resolver_operation_requests_total':'native_resolver_operation_requests',
                      'local_source_file_read_attempts_total':'local_source_file_read_attempts','local_source_file_reads_succeeded_total':'local_source_file_reads_succeeded',
                      'local_body_acquisitions_total':'local_body_acquisitions','local_body_acquisitions_supplied_total':'local_body_acquisitions_supplied'}.items():
  values=[r['packet']['audit']['physical_preparation'].get(field) for r in packets.values()]
  physical[output]=sum(values) if all(type(v) is int for v in values) else None
 manifest={'all_ok':True,'directions':{k:sha(folder/'inputs'/f'{k}.json') for k in KEYS},
           'preparation_normalized_queries_total':qtotal,**physical,'physical_preparation_note':'Each direction prepared once; lsp/search share this frozen packet. Allocated normalized attempts, actual native definition/reference RPC writes, resolver invocations and local acquisition reads are distinct. Local reads exclude AST seed preparation and didOpen.',
           'preparation_workers':2,'results':out,'policy_sha256':sha(HERE/'packet_builder.py')}
 new(folder/'jobs.json',{'jobs':jobs(),'shuffle_seed':SHUFFLE_SEED,'arm_order':list(ARMS),'display':DISPLAY})
 new(folder/'manifest.json',manifest);return manifest

def invariants(r):
 bad=[];q=r.get('queries',{});source=r.get('source_exposure',{})
 init=r.get('initial_preparation_queries_allocated');runtime=r.get('runtime_tool_queries')
 if not isinstance(init,int) or not 0<=init<=3:bad.append('initial query allocation')
 if not isinstance(runtime,int) or runtime<0:bad.append('runtime query count')
 if isinstance(init,int) and isinstance(runtime,int) and q.get('total')!=init+runtime:bad.append('total query reconciliation')
 if q.get('total',99)>6:bad.append('total query cap')
 for b in q.get('batches',[]):
  if b.get('count',0)>3:bad.append('batch cap')
 if r.get('model_calls',99)>3:bad.append('model call cap')
 if r.get('arm')!='search' and (runtime or r.get('model_calls')!=1):bad.append('static called tools/more than one judgement')
 if r.get('arm') in ('diff','ast') and init:bad.append('non-LSP initial query')
 if r.get('arm')=='diff' and source.get('union_reference_tokens',0):bad.append('diff received extra source')
 if source.get('union_reference_tokens',99999)>5200:bad.append('source cap')
 if source.get('protected_seed_ids_intact') is False:bad.append('protected source lost')
 if source.get('consistency_blocks'):bad.append('inconsistent source exposure')
 if r.get('metadata_allocation',{}).get('used',0)>800:bad.append('cumulative metadata cap')
 if r.get('ledger'):bad.append('unexpected ledger')
 for stage in r.get('stages',[]):
  s=stage.get('state_block',{})
  if s.get('tokens',0)+s.get('error_tokens',0)>1600:bad.append('state cap')
 if r.get('transport',{}).get('actual_tool_calls_total',0):bad.append('model direct tool use')
 return bad

def run_tests():
 digest=code_sha()[:12];folder=HERE/'verification';folder.mkdir(exist_ok=True);report=folder/f'tests_{digest}.json'
 if report.exists():return read(report)
 command=[sys.executable,'-m','pytest',*TESTS,'-q','-p','no:cacheprovider',f'--junitxml={folder/f"tests_{digest}.xml"}']
 begin=time.time();cp=subprocess.run(command,cwd=HERE,capture_output=True,text=True,timeout=1500,env={**os.environ,'PYTHONPYCACHEPREFIX':str(HERE/'.pycache')})
 rec={'passed':cp.returncode==0,'code_sha256_prefix':digest,'code_hashes':code_hashes(),'returncode':cp.returncode,'seconds':time.time()-begin,'stdout':cp.stdout,'stderr':cp.stderr,'command':command}
 new(report,rec);return {'passed':rec['passed'],'report':str(report),'stdout_tail':cp.stdout[-2000:],'stderr_tail':cp.stderr[-1000:]}

def fixture_script(stage,ctx):
 # The same scripted requests are given to every arm: static must ignore; search must execute within budget.
 match=re.search(r'changed_files=\[\s*[\"\']([^\"\']+)',ctx['prompt']);path=match.group(1) if match else ''
 requests=[] if stage else [fx.request(1,'grep_literal',path=path,query='def '),fx.request(2,'read_lines',path=path,start=1,end=3)]
 return fx.empty_response(context_requests=requests)

def smoke():
 _,la=load_modules();digest=code_sha()[:12];folder=HERE/'verification'/f'smoke_{digest}';folder.mkdir(parents=True,exist_ok=False);rows=[];initial={};parts={}
 for key in ('N01_a','N08_a'):
  prepared=read(HERE/'prepared/inputs'/f'{key}.json')
  for arm in ARMS:
   rid=f'{key}_{arm}';client=fx.FixtureClient(out_dir=folder/'reviews',review_id=rid,script=fixture_script)
   result=la.run_review(key,arm,client,prepared_packet=prepared['packet'],expected_inputs=prepared['authoritative'])
   result['transport']=rs.transport_summary(folder/'reviews'/rid)
   bad=invariants(result);new(folder/f'{rid}.json',result)
   if result['status']!='completed':bad.append('smoke final not completed')
   initial[rid]={k:result.get('initial_source',{}).get(k) for k in ('evidence_sha256','source_sha256','metadata_sha256')}
   raw=read(folder/'reviews'/rid/'initial.input.json')['raw']
   parts[rid]={k:raw[k] for k in ('system','shared','prompt')}
   parts[rid]['common_suffix']=raw['suffix'].split('<lookup_budget>')[0]
   rows.append({'review_id':rid,'status':result['status'],'violations':bad,'calls':result.get('model_calls'),'runtime_queries':result.get('runtime_tool_queries'),'initial_source':initial[rid]})
 equal={k:all(initial[k+'_lsp'].values()) and initial[k+'_lsp']==initial[k+'_search'] and parts[k+'_lsp']==parts[k+'_search'] for k in ('N01_a','N08_a')}
 exercised=any(r['runtime_queries'] for r in rows if r['review_id'].endswith('_search'))
 report={'passed':all(not r['violations'] for r in rows) and all(equal.values()) and exercised,'fixture_not_a_model':True,'code_sha256_prefix':digest,'rows':rows,'lsp_search_initial_equal':equal,'search_exercised':exercised}
 new(HERE/'verification'/f'smoke_{digest}.json',report);return report

def verification(kind):
 p=HERE/'verification'/f'{kind}_{code_sha()[:12]}.json'
 if not p.exists() or not read(p).get('passed'):raise ExperimentError(f'current {kind} not passed: {p}')
 return {'path':str(p.relative_to(HERE)),'sha256':sha(p)}

def freeze_state():
 _,la=load_modules();missing=[p for p in DOCS if not(HERE/p).is_file()]
 if missing:raise ExperimentError(f'missing protocol documents {missing}')
 manifest=read(HERE/'prepared/manifest.json');jobdoc=read(HERE/'prepared/jobs.json')
 if not manifest.get('all_ok') or jobdoc['jobs']!=jobs():raise ExperimentError('preparation incomplete or schedule differs from fixed jobs')
 dirs={k:sha(HERE/'prepared/inputs'/f'{k}.json') for k in KEYS}
 if dirs!=manifest['directions']:raise ExperimentError('prepared packet changed')
 dependencies=preparation_dependencies()
 for key in KEYS:
  d=read(HERE/'prepared/inputs'/f'{key}.json');case,world=key.split('_');common.load_case(case,world,d['authoritative'])
  if d.get('preparation_dependencies')!=dependencies:raise ExperimentError('prepared dependencies changed')
 return {'code':code_hashes(),'docs':{p:sha(HERE/p) for p in DOCS},'algorithm_dependencies':rs.ace_dependency_files(),
         'product':common.verify_snapshot(),'prepared_manifest_sha256':sha(HERE/'prepared/manifest.json'),'jobs_sha256':sha(HERE/'prepared/jobs.json'),
         'directions_sha256':dirs,'verification':{'tests':verification('tests'),'smoke':verification('smoke')},
         'settings':{'model':ct.MODEL,'reasoning_effort':'low','max_workers':2,'min_start_interval':ct.MIN_START_INTERVAL,'cli_timeout':300,'arms':DISPLAY,'shuffle_seed':SHUFFLE_SEED,'response_schema':la.response_schema(),'schema_sha256':oc._sha(la.response_schema()),'limits':common.LIMITS}}

def freeze():
 state=freeze_state();new(HERE/'freeze.json',{'frozen_at':time.time(),'state':state,'state_sha256':oc._sha(state)});return {'frozen':True,'state_sha256':oc._sha(state)}

def verify_freeze():
 p=HERE/'freeze.json'
 if not p.exists():raise ExperimentError('freeze missing')
 f=read(p);s=freeze_state()
 if oc._sha(s)!=f['state_sha256']:raise ExperimentError('frozen state changed')
 return f

def authorize(exp,*,launch_model,fixture):
 if not launch_model or fixture or not exp.official:raise ExperimentError('only explicit --launch-model on official root is allowed')
 verify_freeze()

def raw_fallback(folder):
 for stage in reversed(common.STAGES):
  p=folder/f'{stage}.raw.txt'
  if p.exists() or (folder/f'{stage}.claim.json').exists():
   try:
    v=json.loads(p.read_text());f=v.get('findings',[])
    if isinstance(f,list):return f,stage
   except (OSError,ValueError,AttributeError):pass
   return [],stage
 return [],None

def enforce_invariants(r):
 r['runtime_invariant_violations']=invariants(r) if 'queries' in r else ['worker had no complete runtime record']
 if r['runtime_invariant_violations']:
  r['status_before_runtime_validation']=r['status']
  r['status']='invalid_runtime_invariants'
  r['raw_findings_are_final_success']=False
  r['reportable_finding_indices']=[]
 return r

def worker(review_id,launch_model):
 exp=rs.Experiment(HERE);authorize(exp,launch_model=launch_model,fixture=False)
 job=next((j for j in exp.jobs() if j['review_id']==review_id),None)
 if job is None:raise ExperimentError('unknown review ID')
 slot,slot_fd=rs.acquire_worker_slot(exp);folder=exp.reviews/review_id;folder.mkdir(parents=True,exist_ok=True)
 new(folder/'review.started.json',{'review_id':review_id,'pid':os.getpid(),'at':time.time(),'slot':slot})
 try:
  _,la=load_modules();client=ct.CliCompletionClient(out_dir=exp.reviews,review_id=review_id,schema=la.response_schema(),gate=ct.AdmissionGate(exp.gate_dir),timeout=300)
  prepared=read(exp.prepared/'inputs'/f"{job['key']}.json")
  r=la.run_review(job['key'],job['arm'],client,prepared_packet=prepared['packet'],expected_inputs=prepared['authoritative'])
 except BaseException as exc:
  raw,stage=raw_fallback(folder);r={'review_id':review_id,'key':job['key'],'arm':job['arm'],'status':'operational_failure','end_reason':'worker_exception','error':{'type':type(exc).__name__,'message':str(exc)[:2000]},'findings_model_raw':raw,'findings_model_raw_stage':stage,'raw_findings_are_final_success':False}
 r.update(review_id=review_id,key=job['key'],arm=job['arm'],transport=rs.transport_summary(folder),worker_slot=slot)
 if r['transport']['actual_tool_calls_total']:r['status']='invalid_model_tool_use'
 enforce_invariants(r)
 new(folder/'review.result.json',r);os.close(slot_fd)
 return {'review_id':review_id,'status':r['status'],'invariants':r['runtime_invariant_violations']}

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',default=str(HERE));sub=parser.add_subparsers(dest='command',required=True)
 for n in ['preflight','prepare','test','smoke','freeze','status']:sub.add_parser(n)
 p=sub.add_parser('prepare-one');p.add_argument('key')
 p=sub.add_parser('run');p.add_argument('--launch-model',action='store_true');p.add_argument('--workers',type=int,default=2)
 p=sub.add_parser('worker');p.add_argument('--review-id',required=True);p.add_argument('--launch-model',action='store_true')
 args=parser.parse_args(argv)
 if Path(args.root).resolve()!=HERE:raise ExperimentError('only this official root')
 try:
  if args.command=='preflight':out=rs.preflight()
  elif args.command=='prepare-one':out=prepare_one(args.key)
  elif args.command=='prepare':out=prepare()
  elif args.command=='test':out=run_tests()
  elif args.command=='smoke':out=smoke()
  elif args.command=='freeze':out=freeze()
  elif args.command=='worker':out=worker(args.review_id,args.launch_model)
  elif args.command=='status':out=rs.status(rs.Experiment(HERE))
  else:
   rs.authorize=authorize;out=rs.run(rs.Experiment(HERE),launch_model=args.launch_model,fixture=False,workers=args.workers)
 except Exception as exc:
  print(json.dumps({'error':type(exc).__name__,'message':str(exc)},ensure_ascii=False),file=sys.stderr);return 2
 print(json.dumps(out,ensure_ascii=False,indent=2,default=str));return 0 if out.get('passed',out.get('ok',True)) else 1
if __name__=='__main__':raise SystemExit(main())
