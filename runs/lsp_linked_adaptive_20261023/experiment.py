import concurrent.futures,copy,difflib,hashlib,json,pickle,random,sys,time
from pathlib import Path
import policy
from ace_review.context_adaptive import digest,token_count,FINAL_PROMPT
from ace_review.isolated_review import call_packet
R=Path(__file__).resolve().parent;ROOT=R.parents[1]
sys.path.insert(0,str(ROOT/'runs/lsp_callback_routes_20261002'));import shared
ARMS=['P0Q0','P1Q0','P0Q1','P1Q1'];MODEL='gpt-5.6-sol'
def read(p):return json.loads(p.read_text())
def save(p,x):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2)
def build_prompt(meta,nav,linked,first,prior=None):
 text=FINAL_PROMPT+'\nWrite concise checks linking the fixed input to the exercised branch, base versus head behavior, and cited supplied evidence. A narrow code-level causal proof is sufficient; do not require actual execution of the entire framework. Do not use an unrelated unknown to erase a supported local defect. A missing necessary binding remains unknown.\n'+meta['common']+'\n[EVIDENCE]\n'+nav.source()+'\n[STATIC OBSERVATIONS]\n'+nav.observations(linked)
 if first:text+='\nThis is the first review. Preserve concrete suspected defects as unresolved questions when evidence is missing. Also return context_requests for up to 6 useful native definition/references queries. Use ONLY visible head source locations, 1-based line and Unicode character column pointing at the identifier. Explain which missing connection each request would settle. Requests may be scheduled within the remaining query budget; do not pretend they executed. An empty request list is allowed.'
 else:text+='\n[PREVIOUS REVIEW: hypotheses, not authoritative evidence]\n'+json.dumps(prior,ensure_ascii=False)+'\nThis is the final review after bounded additional retrieval. Reassess previous concerns against the current supplied source. Preserve unresolved concerns explicitly; silence is not resolution. Do not invent execution or unseen source. No further retrieval is possible.'
 return text

def stage(src,stem):
 root=R/'workspaces'/stem;root.mkdir(parents=True,exist_ok=False)
 for f,s in src.items():
  p=root/f;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
 config={'include':['**/*.py'],'pythonVersion':'3.11','typeCheckingMode':'off','autoSearchPaths':False,'useLibraryCodeForTypes':False,'extraPaths':['.']};save(root/'pyrightconfig.json',config)
 save(R/'workspace_manifests'/f'{stem}.json',{'sources':{f:digest(s) for f,s in src.items()},'pyrightconfig':config})
 return root

def prepare_one(job):
 row,cid,world,direction=job;iid=row['instance_id'];sources={v:shared.full_sources(R/'checkouts'/iid/v) for v in ['original','fixed']};b,h=('original','fixed') if direction=='forward' else ('fixed','original');base,head=sources[b],sources[h]
 changed=sorted(f for f in set(base)|set(head) if base.get(f)!=head.get(f));diff=''.join(''.join(difflib.unified_diff(base.get(f,'').splitlines(True),head.get(f,'').splitlines(True),fromfile='a/'+f,tofile='b/'+f)) for f in changed)
 scope=read(R/'scopes.json')[iid]['scope'];meta={'case_id':cid,'world':world,'instance_id':iid,'repository':row['repo'],'common':'[REPOSITORY]\n'+row['repo']+'\n[SCOPE]\nReview only newly introduced violations of this declared public contract for the fixed inputs. A fix is not an introduced regression merely because behavior changes.\n'+scope+'\n[DIFF]\n'+diff,'changed_files':changed}
 root=stage(head,cid+'_'+world);nav=policy.Navigator(base,head,root,scope);a=nav.audit();meta['preparation_ok']=a['has_roots'] and a['root_coverage'];meta['root_audit']=a
 if meta['preparation_ok']:
  client=policy.Client(root)
  try:
   nav.advance(client,policy.FIRST_QUERIES);initial=copy.deepcopy(nav);nav.advance(client,policy.QUERY_CAP)
  finally:client.close()
  save(R/'lsp_initial'/f'{cid}_{world}.json',{'trace':client.trace,'initialization':client.initialization,'stderr':client.errors,'fixed_audit':nav.audit()})
  for kind,obj in [('initial',initial),('fixed',nav)]:
   p=R/'states'/f'{cid}_{world}_{kind}.pickle';p.parent.mkdir(exist_ok=True)
   with p.open('xb') as f:pickle.dump(obj,f)
   save(R/'packets'/f'{cid}_{world}_{kind}.json',{'source':obj.source(),'plain':obj.observations(False),'linked':obj.observations(True),'audit':obj.audit()})
 save(R/'cases'/f'{cid}_{world}.json',meta)
 print('prepared',cid,world,meta['preparation_ok'],flush=True)

def prepare():
 seal=read(R/'algorithm_seal.json');assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in seal['files'].items())
 rows=read(R/'selected.json');assert len(rows)==12;assert read(R/'witness_validation.json')['all_contracts_verified']
 jobs=[];labels={};rng=random.Random(20261023)
 for i,row in enumerate(rows,1):
  cid=f'N{i:02d}';directions=['forward','reverse'];rng.shuffle(directions);labels[cid]={'instance_id':row['instance_id'],'worlds':dict(zip(['a','b'],['safe' if d=='forward' else 'unsafe' for d in directions])),'directions':dict(zip(['a','b'],directions))}
  jobs.extend((row,cid,w,d) for w,d in zip(['a','b'],directions))
 save(R/'labels.json',labels)
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(prepare_one,jobs))
 calls=[{'run_id':f'{cid}_{w}_{a}_r{rep}','case_id':cid,'world':w,'arm':a,'repeat':rep} for cid in labels for w in ['a','b'] for a in ARMS for rep in [1,2,3]]
 random.Random(20261024).shuffle(calls)
 files=[p for p in R.rglob('*') if p.is_file() and any(str(p.relative_to(R)).startswith(d) for d in ['cases/','packets/','states/','workspace_manifests/'])]+[R/x for x in ['selected.json','scopes.json','labels.json','witness_validation.json','algorithm_seal.json']]
 save(R/'freeze.json',{'jobs':calls,'logical_reviews':288,'max_physical_calls':576,'model':MODEL,'repeats':3,'files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},'model_calls_for_new_PR_before_freeze':0})

def one(job):
 rid=job['run_id'];out=R/'trajectories'/f'{rid}.json'
 if out.exists():return
 cid,w,arm=job['case_id'],job['world'],job['arm'];meta=read(R/'cases'/f'{cid}_{w}.json');linked=arm[1]=='1';adaptive=arm[-1]=='1';record=dict(job)
 if not meta['preparation_ok']:
  record.update(status='preparation_failure',physical_calls=0);save(out,record);return
 with (R/'states'/f'{cid}_{w}_initial.pickle').open('rb') as f:nav=pickle.load(f)
 text=build_prompt(meta,nav,linked,True);inp={**job,'run_id':rid+'_s1','prompt':text,'prompt_sha256':digest(text),'prompt_tokens':token_count(text)};save(R/'inputs'/f'{rid}_s1.json',inp)
 first=call_packet(inp,R/'raw',MODEL,read(R/'schema_first.json'));record['physical_calls']=1
 if first['status']!='ok':record.update(status='first_call_error');save(out,record);return
 prior=first['response'];trace=[]
 if adaptive:
  nav.add_requests(prior['context_requests']);client=policy.Client(nav.root)
  try:nav.advance(client,policy.QUERY_CAP)
  finally:client.close()
  trace=client.trace
 else:
  with (R/'states'/f'{cid}_{w}_fixed.pickle').open('rb') as f:nav=pickle.load(f)
 save(R/'retrieval'/f'{rid}.json',{'source':nav.source(),'observations':nav.observations(linked),'audit':nav.audit(),'actual_additional_native_trace':trace,'fixed_navigation_reused':not adaptive})
 final_prior={k:v for k,v in prior.items() if k!='context_requests'}
 text=build_prompt(meta,nav,linked,False,final_prior);inp={**job,'run_id':rid+'_s2','prompt':text,'prompt_sha256':digest(text),'prompt_tokens':token_count(text)};save(R/'inputs'/f'{rid}_s2.json',inp)
 final=call_packet(inp,R/'raw',MODEL,read(R/'schema_final.json'));record.update(status='ok' if final['status']=='ok' else 'final_call_error',physical_calls=2,first_review=prior['review'],final_review=final.get('response',{}),request_count=len(prior['context_requests']),accepted_requests=sum(x.get('accepted',False) for x in nav.requests));save(out,record)

def guarded_one(job):
 try:one(job)
 except Exception as exc:
  out=R/'trajectories'/f'{job["run_id"]}.json'
  calls=sum((R/'raw'/f'{job["run_id"]}_s{i}.json').exists() for i in [1,2])
  if not out.exists():save(out,{**job,"status":"infrastructure_error","error":repr(exc),"physical_calls":calls})
  print(job["run_id"],"infrastructure_error",repr(exc),flush=True)

def run():
 f=read(R/'freeze.json');seal=read(R/'algorithm_seal.json')
 assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in {**f['files'],**seal['files']}.items())
 for folder in ['inputs','raw','trajectories','retrieval']:(R/folder).mkdir(exist_ok=True)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for x in pool.map(guarded_one,f['jobs']):pass
if __name__=='__main__':
 if sys.argv[1]=='prepare':prepare()
 else:run()
