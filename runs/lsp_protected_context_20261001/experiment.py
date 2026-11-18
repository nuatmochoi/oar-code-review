from __future__ import annotations
import ast,concurrent.futures,hashlib,importlib.util,json,random,sys
from functools import lru_cache
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'runs/lsp_backend_20261001'));sys.path.insert(0,str(R))
import policy
from lsp_client import SERVER
Client=policy.Client
from ace_review.context_adaptive import Corpus,digest,token_count,FINAL_PROMPT
from ace_review.isolated_review import call_packet
spec=importlib.util.spec_from_file_location('expanded_branch',ROOT/'runs/lsp_expansion_20261001/experiment.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
read=lambda p:json.loads(p.read_text())
def save(p,o):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(o,f,ensure_ascii=False,indent=2)
def obs(jumps):
 lines=[]
 for i,j in enumerate(jumps,1):
  targets='; '.join(p+'::'+n for p,n in j['targets']) or 'unresolved in this analysis'
  line=f'[E-NAV-{i:02d}] head {j["source_file"]}:{j["line"]}, UTF16 column {j["utf16_character"]}, symbol {j["called"]}: static definition candidates -> {targets}.'
  if j.get('unit_targets') and j['kind'] in ['receiver_provenance','global_or_import_reference']:line+=' Native declaration source units: '+', '.join(j['unit_targets'])+'.'
  if token_count('\n'.join(lines+[line]))>400:break
  lines.append(line)
 return '\n'.join(lines)
def prompt(p,arm):return old.ledger.LEDGER_PROMPT+'\n'+FINAL_PROMPT+p['common']+'\n[EVIDENCE]\n'+p[arm]+'\n[STATIC DEFINITION OBSERVATIONS — NOT RUNTIME PROOF]\n'+p[arm+'_observations']
def prep_one(job):
 cid,w,group=job;prior=ROOT/'runs'/('lsp_priority_20261001' if group=='development' else 'lsp_priority_holdout_20261001');data=ROOT/'runs'/('lsp_expansion_20261001' if group=='development' else 'lsp_priority_holdout_20261001');label=read(data/'labels.json')[cid];iid=label['instance_id'];row=next(x for x in read(data/'selected.json') if x['instance_id']==iid);original=read(prior/'packets'/f'{cid}_{w}.json');allsrc={v:old.full_sources(data/'checkouts'/iid/v,row['repo']) for v in ['original','fixed']};changed=[p for p in allsrc['original'] if allsrc['original'][p]!=allsrc['fixed'].get(p)];selected=old.closure(allsrc['original'],changed)|old.closure(allsrc['fixed'],changed);base,head=('original','fixed') if label['directions'][w]=='forward' else ('fixed','original');versions={v:{p:allsrc[rev][p] for p in sorted(selected) if p in allsrc[rev]} for v,rev in [('base',base),('head',head)]};diff=original['common'].split('\n[DIFF]\n',1)[1];scope=original['common'].split('\nDeployment scope: ',1)[1].split('\n[DIFF]\n',1)[0];case={'scope':scope,'diff':diff};corpus=Corpus(versions,{v:{'nodes':{},'edges':[]} for v in versions},diff);assert corpus.source_hashes==original['source_sha256'];resolver=policy.ref.old.Bindings(corpus,versions['head']);root=data/'workspaces'/f'{cid}_{w}';p={k:original[k] for k in ['case_id','world','repository','instance_id','common','source_sha256','B','B_observations','B_audit']};p['group']=group;p['B_trace_reference']=str(prior.relative_to(ROOT))+'/lsp_traces/'+f'{cid}_{w}_B.json';p['workspace_reference']=str(root.relative_to(ROOT));p['workspace_manifest_reference']=str(data.relative_to(ROOT))+'/workspace_manifests/'+f'{cid}_{w}.json'
 for arm in ['B15','F']:
  client=Client(root)
  try:state,audit,observations=policy.packet(corpus,versions,case,resolver,client,original,arm)
  finally:client.close()
  p[arm]=state.render();p[arm+'_observations']=observations;p[arm+'_audit']=audit;save(R/'lsp_traces'/f'{cid}_{w}_{arm}.json',{'trace':client.trace,'initialization':client.initialization,'stderr':client.errors})
 save(R/'packets'/f'{cid}_{w}.json',p);print('Prepared '+cid+'_'+w,flush=True)
def prepare():
 for d in ['packets','lsp_traces','inputs','raw']:(R/d).mkdir(exist_ok=False)
 ast._splitlines_no_ff=lru_cache(maxsize=128)(ast._splitlines_no_ff);jobs=[];labels={}
 for group,data in [('development','lsp_expansion_20261001'),('confirmation','lsp_priority_holdout_20261001')]:
  for cid,label in read(ROOT/'runs'/data/'labels.json').items():
   if cid=='E09':continue
   labels[cid]=label|{'group':group};jobs.extend((cid,w,group) for w in ['a','b'])
 save(R/'labels.json',labels);save(R/'schema.json',old.ledger.LEDGER_SCHEMA)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(prep_one,jobs))
 jobs=[];shared={}
 for pp in sorted((R/'packets').glob('*.json')):
  p=read(pp)
  for rep in [1,2,3] if p['group']=='development' else [1]:
   seen={}
   for arm in ['B15','F']:
    rid=f'{pp.stem}_{arm}_r{rep}';h=digest(prompt(p,arm))
    if h in seen:shared[rid]=seen[h]
    else:seen[h]=rid;jobs.append({'run_id':rid,'case_id':p['case_id'],'world':p['world'],'arm':arm,'repeat':rep,'group':p['group']})
 files=[R/x for x in ['experiment.py','policy.py','PROTOCOL_KO.md','labels.json','schema.json','policy_tests.json','check_policy.py']]+[p for d in ['packets','lsp_traces'] for p in (R/d).glob('*.json')]
 for name in ['lsp_priority_20261001','lsp_priority_holdout_20261001']:
  f=ROOT/'runs'/name/'freeze.json';files+=[f]+[ROOT/p for p in read(f)['files']]
 files += [ROOT/'runs/lsp_branch_context_20261001/fragments.py',ROOT/'runs/lsp_branch_context_20261001/freeze.json']
 save(R/'freeze.json',{'model':'gpt-5.6-sol','effort':'low','logical_reviews':148,'physical_calls':len(jobs),'jobs':jobs,'shared_identical_inputs':shared,'repeats_development':3,'repeats_confirmation':1,'confirmation_previously_evaluated':True,'source_cap':1600,'observation_cap':400,'files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}});print('Frozen '+str(len(jobs))+' calls',flush=True)
def run():
 f=read(R/'freeze.json');assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items())
 for pp in (R/'packets').glob('*.json'):
  p=read(pp);m=read(ROOT/p['workspace_manifest_reference']);assert all(digest((ROOT/p['workspace_reference']/file).read_text())==h for file,h in m['source_sha256'].items())
 jobs=f['jobs'];random.Random(20261009).shuffle(jobs)
 def worker(j):
  rid=j['run_id'];p=read(R/'packets'/f'{j["case_id"]}_{j["world"]}.json');text=prompt(p,j['arm']);row=dict(j,prompt=text,prompt_sha256=digest(text),prompt_reference_tokens=token_count(text));save(R/'inputs'/f'{rid}.json',row);call_packet(row,R/'raw','gpt-5.6-sol',read(R/'schema.json'))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(worker,jobs))
 print('All branch-context reviews completed',flush=True)
if __name__=='__main__':prepare() if sys.argv[1]=='prepare' else run()
