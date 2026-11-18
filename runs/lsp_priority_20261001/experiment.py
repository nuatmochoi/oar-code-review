from __future__ import annotations
import ast,concurrent.futures,hashlib,importlib.util,json,random,re,sys,time
from functools import lru_cache
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];PRIOR=ROOT/'runs/lsp_expansion_20261001';sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'runs/lsp_backend_20261001'));sys.path.insert(0,str(R))
import policy
from lsp_client import Client,SERVER
from ace_review.context_adaptive import Corpus,digest,token_count,FINAL_PROMPT
from ace_review.isolated_review import call_packet
spec=importlib.util.spec_from_file_location('expanded',PRIOR/'experiment.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
read=lambda p:json.loads(p.read_text())
def save(p,o):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(o,f,ensure_ascii=False,indent=2)
def obs(jumps):
 lines=[]
 for i,j in enumerate(jumps,1):
  targets='; '.join(p+'::'+n for p,n in j['targets']) or 'unresolved in this analysis'
  # C adds native non-callable source location IDs; label them as static source declarations.
  units=j.get('unit_targets',[])
  line=f'[E-NAV-{i:02d}] head {j["source_file"]}:{j["line"]}, UTF16 column {j["utf16_character"]}, symbol {j["called"]}: static definition candidates -> {targets}.'
  if units and j['kind'] in ['receiver_provenance','global_or_import_reference']:line+=' Native declaration source units: '+', '.join(units)+'.'
  if token_count('\n'.join(lines+[line]))>400:break
  lines.append(line)
 return '\n'.join(lines)
def prompt(p,arm):return old.ledger.LEDGER_PROMPT+'\n'+FINAL_PROMPT+p['common']+'\n[EVIDENCE]\n'+p[arm]+'\n[STATIC DEFINITION OBSERVATIONS — NOT RUNTIME PROOF]\n'+p[arm+'_observations']

def prep_one(job):
 cid,world=job;start=time.monotonic();labels=read(PRIOR/'labels.json');label=labels[cid];iid=label['instance_id'];row=next(r for r in read(PRIOR/'selected.json') if r['instance_id']==iid);prior=read(PRIOR/'packets'/f'{cid}_{world}.json')
 allsrc={rev:old.full_sources(PRIOR/'checkouts'/iid/rev,row['repo']) for rev in ['original','fixed']};changed=[p for p in allsrc['original'] if allsrc['original'][p]!=allsrc['fixed'].get(p)];selected=old.closure(allsrc['original'],changed)|old.closure(allsrc['fixed'],changed);base,head=('original','fixed') if label['directions'][world]=='forward' else ('fixed','original');versions={v:{p:allsrc[rev][p] for p in sorted(selected)} for v,rev in [('base',base),('head',head)]}
 assert {v:{p:digest(s) for p,s in src.items()} for v,src in versions.items()}==prior['source_sha256']
 diff=prior['common'].split('\n[DIFF]\n',1)[1];scope=prior['common'].split('\nDeployment scope: ',1)[1].split('\n[DIFF]\n',1)[0];case={'scope':scope,'diff':diff};corpus=Corpus(versions,{v:{'nodes':{},'edges':[]} for v in versions},diff);resolver=policy.ref.old.Bindings(corpus,versions['head']);root=PRIOR/'workspaces'/f'{cid}_{world}'
 p={'case_id':cid,'world':world,'repository':row['repo'],'instance_id':iid,'common':prior['common'],'source_sha256':corpus.source_hashes,'A':prior['LSP'],'A_observations':prior['LSP_observations'],'A_audit':prior['LSP_audit'],'A_trace_reference':str((PRIOR/'lsp_traces'/f'{cid}_{world}.json').relative_to(ROOT))}
 for arm in ['B','C']:
  client=Client(root)
  try:state,audit=policy.packet(corpus,case,resolver,client,arm)
  finally:client.close()
  p[arm]=state.render();p[arm+'_observations']=obs(audit['jumps']);p[arm+'_audit']=audit
  save(R/'lsp_traces'/f'{cid}_{world}_{arm}.json',{'trace':client.trace,'initialization':client.initialization,'startup_seconds':client.startup_seconds,'stderr':client.errors})
 save(R/'packets'/f'{cid}_{world}.json',p);print(json.dumps({'prepared':cid+'_'+world,'AB_changed':prompt(p,'A')!=prompt(p,'B'),'BC_changed':prompt(p,'B')!=prompt(p,'C'),'seconds':round(time.monotonic()-start,2)}),flush=True)

def prepare():
 assert not (R/'freeze.json').exists()
 for d in ['packets','lsp_traces','inputs','raw']:(R/d).mkdir(exist_ok=False)
 ast._splitlines_no_ff=lru_cache(maxsize=128)(ast._splitlines_no_ff)
 jobs=[(cid,w) for cid in sorted(read(PRIOR/'labels.json')) if cid!='E09' for w in ['a','b']]
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(prep_one,jobs))
 shared={};jobs=[]
 for repeat in [1,2,3]:
  for pp in sorted((R/'packets').glob('*.json')):
   p=read(pp);seen={}
   for arm in ['A','B','C']:
    rid=f'{pp.stem}_{arm}_r{repeat}';h=digest(prompt(p,arm))
    if h in seen:shared[rid]=seen[h]
    else:seen[h]=rid;jobs.append({'run_id':rid,'case_id':p['case_id'],'world':p['world'],'arm':arm,'repeat':repeat})
 files=[R/x for x in ['experiment.py','policy.py','PROTOCOL_KO.md','check_policy.py','policy_tests.json']]+[p for d in ['packets','lsp_traces'] for p in (R/d).glob('*.json')]+[PRIOR/x for x in ['experiment.py','freeze.json','labels.json','schema.json','selected.json','witnesses.json']]+[ROOT/x for x in ['runs/lsp_backend_20261001/backend.py','runs/lsp_backend_20261001/lsp_client.py','runs/binding_context_20261001/binding.py','runs/icml_priority_20261001/policy.py','runs/grounded_scope_20261001/experiment.py','ace_review/context_adaptive.py','ace_review/isolated_review.py','ace_review/reviewer.py','ace_review/packet_review.py','tools/pyright-lsp-20261001/package-lock.json']]+[SERVER]
 files+=list((PRIOR/'packets').glob('*.json'))+list((PRIOR/'workspace_manifests').glob('*.json'))+list((PRIOR/'lsp_traces').glob('*.json'))
 save(R/'freeze.json',{'model':'gpt-5.6-sol','effort':'low','source_cap':1600,'observation_cap':400,'logical_reviews':198,'physical_calls':len(jobs),'jobs':jobs,'shared_identical_inputs':shared,'repeats':3,'provider_seed_control':False,'stage_order':['A+B','C'],'files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})
 print('Frozen',len(jobs),'physical calls.',flush=True)

def run(stage):
 f=read(R/'freeze.json');assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items())
 for pp in (R/'packets').glob('*.json'):
  m=read(PRIOR/'workspace_manifests'/pp.name);assert all(digest((PRIOR/'workspaces'/pp.stem/p).read_text())==h for p,h in m['source_sha256'].items())
 jobs=[j for j in f['jobs'] if j['arm'] in (['A','B'] if stage=='AB' else ['C'])];random.Random(20261005 if stage=='AB' else 20261006).shuffle(jobs)
 def worker(j):
  rid=j['run_id']
  if (R/'raw'/f'{rid}.json').exists():return
  p=read(R/'packets'/f'{j["case_id"]}_{j["world"]}.json');text=prompt(p,j['arm']);row=dict(j,prompt=text,prompt_sha256=digest(text),prompt_reference_tokens=token_count(text));save(R/'inputs'/f'{rid}.json',row);call_packet(row,R/'raw','gpt-5.6-sol',read(PRIOR/'schema.json'))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(worker,jobs))
 print('Stage',stage,'complete.',flush=True)
if __name__=='__main__':prepare() if sys.argv[1]=='prepare' else run(sys.argv[2])
