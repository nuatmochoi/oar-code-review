"""New PR confirmation; B and case-independent native state-reference policy S."""
from __future__ import annotations
import ast,concurrent.futures,difflib,hashlib,importlib.util,json,random,sys
from functools import lru_cache
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(R))
import policy as statepolicy
policy=statepolicy.shared
sp=importlib.util.spec_from_file_location('fresh_shared_ex',ROOT/'runs/lsp_expansion_20261001/experiment.py');old=importlib.util.module_from_spec(sp);sp.loader.exec_module(old);old.R=R
from ace_review.context_adaptive import Corpus,digest,token_count,FINAL_PROMPT
from ace_review.isolated_review import call_packet
read=lambda p:json.loads(p.read_text())
def save(p,x):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2)
def prompt(p,a):return old.ledger.LEDGER_PROMPT+'\n'+FINAL_PROMPT+p['common']+'\n[EVIDENCE]\n'+p[a]+'\n[STATIC OBSERVATIONS — NOT RUNTIME PROOF]\n'+p[a+'_observations']
def prep_one(job):
 row,cid,w,d=job;iid=row['instance_id'];allsrc={v:old.full_sources(R/'checkouts'/iid/v,row['repo']) for v in ['original','fixed']};changed=sorted(p for p in allsrc['original'] if allsrc['original'][p]!=allsrc['fixed'].get(p));assert changed
 selected=old.closure(allsrc['original'],changed)|old.closure(allsrc['fixed'],changed);base,head=('original','fixed') if d=='forward' else ('fixed','original');vv={v:{p:allsrc[rev][p] for p in sorted(selected) if p in allsrc[rev]} for v,rev in [('base',base),('head',head)]}
 diff=''.join(''.join(difflib.unified_diff(vv['base'][p].splitlines(True),vv['head'][p].splitlines(True),fromfile='a/'+p,tofile='b/'+p)) for p in changed)
 scope='Review only newly introduced violations of this declared public API contract for the fixed reproduction inputs. A fix is not an introduced regression merely because behavior changes. Do not assume unstated runtime bindings or values.\n'+read(R/'scopes.json')[iid]['scope'];case={'scope':scope,'diff':diff};c=Corpus(vv,{v:{'nodes':{},'edges':[]} for v in vv},diff);resolver=policy.ref.old.Bindings(c,vv['head']);root,manifest=old.stage(allsrc[head],cid+'_'+w);save(R/'workspace_manifests'/f'{cid}_{w}.json',manifest)
 p={'case_id':cid,'world':w,'instance_id':iid,'repository':row['repo'],'group':'fresh_state_pr','common':old.common({'repository':row['repo'],'scope':scope,'diff':diff}),'source_sha256':c.source_hashes,'workspace_reference':str(root.relative_to(ROOT)),'source_selection':{'rule':'Changed production files + import two-hop union + ancestor initializers; tests excluded','corpus_files':len(selected),'full_workspace_files':len(allsrc[head]),'changed_files':changed}}
 client=policy.Client(root)
 try:bs,ba=policy.b.packet(c,case,resolver,client,'B')
 finally:client.close()
 p.update(B=bs.render(),B_audit=ba,B_observations=policy.observation(ba['jumps'],[]));save(R/'lsp_traces'/f'{cid}_{w}_B.json',{'trace':client.trace,'initialization':client.initialization,'stderr':client.errors})
 client=policy.Client(root)
 try:ss,sa,so=statepolicy.packet(c,vv,case,resolver,client,p)
 finally:client.close()
 p.update(S=ss.render(),S_audit=sa,S_observations=so);save(R/'lsp_traces'/f'{cid}_{w}_S.json',{'trace':client.trace,'initialization':client.initialization,'stderr':client.errors});save(R/'packets'/f'{cid}_{w}.json',p);print('Prepared '+cid+'_'+w,flush=True)
def prepare():
 assert read(R/'witness_validation.json')['all_contracts_verified']
 for d in ['packets','lsp_traces','workspace_manifests','inputs','raw']:(R/d).mkdir(exist_ok=False)
 ast._splitlines_no_ff=lru_cache(maxsize=128)(ast._splitlines_no_ff);labels={};jobs=[];rng=random.Random(20261021)
 for i,row in enumerate(read(R/'selected.json'),1):
  cid=f'J{i:02d}';dd=['forward','reverse'];rng.shuffle(dd);labels[cid]={'instance_id':row['instance_id'],'repository':row['repo'],'directions':dict(zip(['a','b'],dd)),'worlds':{w:'safe' if d=='forward' else 'unsafe' for w,d in zip(['a','b'],dd)}};jobs.extend((row,cid,w,d) for w,d in zip(['a','b'],dd))
 save(R/'labels.json',labels);save(R/'schema.json',old.ledger.LEDGER_SCHEMA)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(prep_one,jobs))
 jobs=[];shared={}
 for pp in sorted((R/'packets').glob('*.json')):
  p=read(pp)
  for rep in [1,2,3]:
   seen={}
   for a in ['B','S']:
    rid=f'{pp.stem}_{a}_r{rep}';text=prompt(p,a);h=digest(text);row={'run_id':rid,'case_id':p['case_id'],'world':p['world'],'group':p['group'],'arm':a,'repeat':rep,'prompt':text,'prompt_sha256':h,'prompt_reference_tokens':token_count(text)};save(R/'inputs'/f'{rid}.json',row)
    if h in seen:shared[rid]=seen[h]
    else:seen[h]=rid;jobs.append({k:row[k] for k in ['run_id','case_id','world','group','arm','repeat']})
 files=[p for p in R.glob('*') if p.is_file()]+[p for d in ['packets','lsp_traces','workspace_manifests','inputs'] for p in (R/d).glob('*.json')];prior=ROOT/'runs/lsp_protected_context_20261001/freeze.json';files+=[prior]+[ROOT/p for p in read(prior)['files']]+[ROOT/'runs/lsp_ablation_fresh_20261001/policy.py']
 save(R/'freeze.json',{'model':'gpt-5.6-sol','effort':'low','logical_reviews':48,'physical_calls':len(jobs),'jobs':jobs,'shared_identical_inputs':shared,'repeats':3,'source_cap':1600,'observation_cap':400,'query_cap':15,'common_definition_cap':12,'extra_reference_cap':3,'workers':4,'retry':False,'provider_seed_control':False,'model_calls_before_freeze':0,'primary_contrast':'S minus B on four previously unused PRs, case-independent native state-reference traversal; same repository, not file/repository holdout','files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}});print('Frozen 48 logical / '+str(len(jobs))+' physical future reviews; no model calls.',flush=True)
def run():
 f=read(R/'freeze.json');assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items())
 for m in (R/'workspace_manifests').glob('*.json'):
  assert all(digest((R/'workspaces'/m.stem/p).read_text())==h for p,h in read(m)['source_sha256'].items())
 jobs=f['jobs'];random.Random(20261022).shuffle(jobs)
 def one(j):
  rid=j['run_id'];assert not (R/'raw'/f'{rid}.json').exists();call_packet(read(R/'inputs'/f'{rid}.json'),R/'raw',f['model'],read(R/'schema.json'))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(one,jobs))
if __name__=='__main__':prepare() if sys.argv[1]=='prepare' else run()
