"""Pre-call C refinement, based on source-only builtin-slot diagnostic."""
import concurrent.futures,hashlib,json,sys
from pathlib import Path
R=Path(__file__).resolve().parent;sys.path.insert(0,str(R));import experiment as ex
assert not list((R/'raw').glob('*.json'))
read=ex.read

def task(pp):
 p=read(pp);cid=p['case_id'];world=p['world'];label=read(ex.PRIOR/'labels.json')[cid];row=next(x for x in read(ex.PRIOR/'selected.json') if x['instance_id']==p['instance_id']);src={rev:ex.old.full_sources(ex.PRIOR/'checkouts'/p['instance_id']/rev,row['repo']) for rev in ['original','fixed']};changed=[f for f in src['original'] if src['original'][f]!=src['fixed'].get(f)];selected=ex.old.closure(src['original'],changed)|ex.old.closure(src['fixed'],changed);base,head=('original','fixed') if label['directions'][world]=='forward' else ('fixed','original');versions={v:{f:src[rev][f] for f in sorted(selected)} for v,rev in [('base',base),('head',head)]}
 diff=p['common'].split('\n[DIFF]\n')[1];scope=p['common'].split('\nDeployment scope: ')[1].split('\n[DIFF]\n')[0];corpus=ex.Corpus(versions,{v:{'nodes':{},'edges':[]} for v in versions},diff);resolver=ex.policy.ref.old.Bindings(corpus,versions['head']);client=ex.Client(ex.PRIOR/'workspaces'/pp.stem)
 try:state,audit=ex.policy.packet(corpus,{'diff':diff,'scope':scope},resolver,client,'C')
 finally:client.close()
 p['C']=state.render();p['C_observations']=ex.obs(audit['jumps']);p['C_audit']=audit
 pp.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n');(R/'lsp_traces'/f'{pp.stem}_C.json').write_text(json.dumps({'trace':client.trace,'initialization':client.initialization,'startup_seconds':client.startup_seconds,'stderr':client.errors},indent=2)+'\n');assert all(p[x]==read(R/'preflight_C0_packets'/pp.name)[x] for x in ['A','B','A_observations','B_observations']);print('C prepared',pp.stem,flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(task,sorted((R/'packets').glob('*.json'))))
f=read(R/'freeze.json');shared={};jobs=[]
for rep in [1,2,3]:
 for pp in sorted((R/'packets').glob('*.json')):
  p=read(pp);seen={}
  for arm in ['A','B','C']:
   rid=f'{pp.stem}_{arm}_r{rep}';h=ex.digest(ex.prompt(p,arm))
   if h in seen:shared[rid]=seen[h]
   else:seen[h]=rid;jobs.append({'run_id':rid,'case_id':p['case_id'],'world':p['world'],'arm':arm,'repeat':rep})
f['shared_identical_inputs']=shared;f['jobs']=jobs;f['physical_calls']=len(jobs);f['C_refinement_at_model_calls']=0
for p in [ex.ROOT/p for p in f['files']]+[R/'refine_C.py']+list((R/'policy_fixture').glob('*')):
 f['files'][str(p.relative_to(ex.ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
(R/'freeze.json').write_text(json.dumps(f,indent=2)+'\n');print('Final freeze',len(jobs),'calls.',flush=True)
