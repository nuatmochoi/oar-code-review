from __future__ import annotations
import ast,concurrent.futures,difflib,hashlib,importlib.util,json,random,sys,time
from functools import lru_cache
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];PRIOR=R
spec=importlib.util.spec_from_file_location('priority_ex',ROOT/'runs/lsp_priority_20261001/experiment.py');main=importlib.util.module_from_spec(spec);spec.loader.exec_module(main)
old=main.old;old.R=R;policy=main.policy;Client=main.Client;Corpus=main.Corpus;digest=main.digest;token_count=main.token_count;prompt=main.prompt;read=main.read;save=main.save;SERVER=main.SERVER

def prep_one(job):
 row,w,d,cid=job;iid=row['instance_id'];allsrc={v:old.full_sources(R/'checkouts'/iid/v,row['repo']) for v in ['original','fixed']};changed=[p for p in allsrc['original'] if allsrc['original'][p]!=allsrc['fixed'].get(p)];selected=old.closure(allsrc['original'],changed)|old.closure(allsrc['fixed'],changed);base,head=('original','fixed') if d=='forward' else ('fixed','original');versions={v:{p:allsrc[rev][p] for p in sorted(selected) if p in allsrc[rev]} for v,rev in [('base',base),('head',head)]};diff=''.join(''.join(difflib.unified_diff(versions['base'][p].splitlines(True),versions['head'][p].splitlines(True),fromfile='a/'+p,tofile='b/'+p)) for p in changed)
 scope='Review only newly introduced violations of the following public API contract, including the public issue reproduction inputs. A bug already present in base is not an introduced regression. A fix is not an introduced regression merely because it changes behavior. Do not assume unstated runtime bindings or values.\n'+row['problem_statement'][:5000];case={'scope':scope,'diff':diff};corpus=Corpus(versions,{v:{'nodes':{},'edges':[]} for v in versions},diff);resolver=policy.ref.old.Bindings(corpus,versions['head']);root,manifest=old.stage(allsrc[head],cid+'_'+w);save(R/'workspace_manifests'/f'{cid}_{w}.json',manifest)
 p={'case_id':cid,'world':w,'repository':row['repo'],'instance_id':iid,'common':old.common({'repository':row['repo'],'scope':scope,'diff':diff}),'source_sha256':corpus.source_hashes}
 for arm in ['A','B','C']:
  client=Client(root)
  try:
   state,audit=policy.ref.packet(corpus,case,resolver,policy.ref.LSPBackend(resolver,client)) if arm=='A' else policy.packet(corpus,case,resolver,client,arm)
  finally:client.close()
  p[arm]=state.render();p[arm+'_audit']=audit;p[arm+'_observations']=main.obs(audit['jumps']);save(R/'lsp_traces'/f'{cid}_{w}_{arm}.json',{'trace':client.trace,'initialization':client.initialization,'stderr':client.errors})
 save(R/'packets'/f'{cid}_{w}.json',p);print('Prepared '+cid+'_'+w,flush=True)

def prepare():
 for d in ['packets','lsp_traces','workspace_manifests','inputs','raw']:(R/d).mkdir(exist_ok=False)
 ast._splitlines_no_ff=lru_cache(maxsize=128)(ast._splitlines_no_ff);jobs=[];labels={};rng=random.Random(20261007)
 for idx,row in enumerate(read(R/'selected.json'),1):
  cid=f'H{idx:02d}';dirs=['forward','reverse'];rng.shuffle(dirs);labels[cid]={'instance_id':row['instance_id'],'repository':row['repo'],'directions':dict(zip(['a','b'],dirs)),'worlds':{w:'safe' if d=='forward' else 'unsafe' for w,d in zip(['a','b'],dirs)}};jobs.extend((row,w,d,cid) for w,d in zip(['a','b'],dirs))
 save(R/'labels.json',labels);save(R/'schema.json',old.ledger.LEDGER_SCHEMA)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(prep_one,jobs))
 jobs=[];shared={}
 for pp in sorted((R/'packets').glob('*.json')):
  p=read(pp);seen={}
  for arm in ['A','B','C']:
   rid=f'{pp.stem}_{arm}_r1';h=digest(prompt(p,arm))
   if h in seen:shared[rid]=seen[h]
   else:seen[h]=rid;jobs.append({'run_id':rid,'case_id':p['case_id'],'world':p['world'],'arm':arm,'repeat':1})
 files=[R/x for x in ['experiment.py','PROTOCOL_KO.md','selected.json','selection_revision.json','provenance.json','labels.json','schema.json','witness_one.py','witnesses.py','witnesses.json']]+[p for d in ['packets','lsp_traces','workspace_manifests'] for p in (R/d).glob('*.json')]+[ROOT/'runs/lsp_priority_20261001/freeze.json']+[ROOT/p for p in read(ROOT/'runs/lsp_priority_20261001/freeze.json')['files']]+[SERVER]
 save(R/'freeze.json',{'model':'gpt-5.6-sol','effort':'low','source_cap':1600,'observation_cap':400,'logical_reviews':24,'physical_calls':len(jobs),'jobs':jobs,'shared_identical_inputs':shared,'repeats':1,'provider_seed_control':False,'files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}});print('Frozen '+str(len(jobs))+' calls',flush=True)

def run():
 f=read(R/'freeze.json');assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items())
 for pp in (R/'workspace_manifests').glob('*.json'):
  m=read(pp);assert all(digest((R/'workspaces'/pp.stem/p).read_text())==h for p,h in m['source_sha256'].items())
 jobs=f['jobs'];random.Random(20261008).shuffle(jobs)
 def worker(j):
  rid=j['run_id'];p=read(R/'packets'/f'{j["case_id"]}_{j["world"]}.json');text=prompt(p,j['arm']);row=dict(j,prompt=text,prompt_sha256=digest(text),prompt_reference_tokens=token_count(text));save(R/'inputs'/f'{rid}.json',row);main.call_packet(row,R/'raw','gpt-5.6-sol',read(R/'schema.json'))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(worker,jobs))
 print('Holdout complete',flush=True)
if __name__=='__main__':prepare() if sys.argv[1]=='prepare' else run()
