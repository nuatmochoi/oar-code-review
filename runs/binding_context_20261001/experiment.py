import ast,json,sys,random,importlib.util,concurrent.futures
from pathlib import Path
from functools import lru_cache
R=Path(__file__).resolve().parent;ROOT=R.parents[1];PRIOR=ROOT/'runs/grounded_scope_20261001';ICML=ROOT/'runs/icml_priority_20261001';sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import Corpus,digest,token_count,FINAL_PROMPT
from ace_review.isolated_review import call_packet
from binding import packet
sp=importlib.util.spec_from_file_location('previous_experiment',PRIOR/'experiment.py');previous=importlib.util.module_from_spec(sp);sp.loader.exec_module(previous)
def read(p):return json.loads(p.read_text())
def save(p,o):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(o,f,ensure_ascii=False,indent=2)
def prepare():
 (R/'raw').mkdir(exist_ok=False);(R/'inputs').mkdir(exist_ok=False)
 assert len({'definition'.casefold(),'binding'.casefold()})==2
 ast._splitlines_no_ff=lru_cache(maxsize=128)(ast._splitlines_no_ff)
 for p in sorted((PRIOR/'packets').glob('*.json')):
  original=read(p);cid=original['case_id'];world=original['world']
  if cid.startswith('F'):case=read(ROOT/'runs/context_fresh6_20260929/experiment_v1/cases'/f'{cid}.json');spec=case['worlds'][world]
  elif cid.startswith('H'):case=read(ICML/'holdout'/f'{cid}.json');spec=case['worlds'][world]
  else:
   new=read(PRIOR/'new_sources'/p.name);sources=dict(read(ICML/'holdout/H03.json')['worlds']['a']['versions']['base']);sources.pop('deployment.py');versions={}
   for rev in ['base','head']:
    s=dict(sources);s[new['changed_file']]=new[rev];s['deployment.py']=new['deployment'];versions[rev]=s
   spec={'versions':versions};case={'scope':new['scope'],'diff':new['diff']}
  graphs={v:{'nodes':{},'edges':[]} for v in ['base','head']};local=dict(case,world_spec=spec);corpus=Corpus(spec['versions'],graphs,case['diff']);assert corpus.source_hashes==original['source_sha256'],p.name
  b,a=packet(corpus,local);out={'case_id':cid,'world':world,'stage':'regression_'+cid[0],'common':original['common'],'definition':original['D'],'binding':b.render(),'definition_audit':original['D_audit'],'binding_audit':a,'source_sha256':corpus.source_hashes};save(R/'packets'/p.name,out);print(json.dumps({'prepared':p.stem,'tokens':b.tokens,'new_ids':list(set(a['state']['source_lines'])-set(original['D_audit']['state']['source_lines']))}),flush=True)
 save(R/'labels.json',read(PRIOR/'labels.json'));save(R/'schema.json',previous.LEDGER_SCHEMA)
 files=[R/'experiment.py',R/'binding.py',R/'test_binding.py',R/'resolver_tests.json',R/'PROTOCOL_KO.md',R/'labels.json',R/'schema.json',PRIOR/'experiment.py',ICML/'policy.py',ROOT/'ace_review/context_adaptive.py',ROOT/'ace_review/isolated_review.py',ROOT/'ace_review/reviewer.py',ROOT/'ace_review/packet_review.py',ROOT/'ace_review/auto_context.py']+list((R/'packets').glob('*.json'))
 save(R/'freeze.json',{'files':{str(p.relative_to(ROOT)):digest(p.read_text()) for p in files},'model':'gpt-5.6-sol','effort':'low','source_token_cap':1600,'max_candidate_edges':12,'depth':3,'planned_calls':56,'arms':['definition','binding'],'workers':4,'timeout_seconds':240,'retry':False,'scope':'Observed 14-family regression only; identical previous ledger prompt/schema, retrieval policy changes; no fresh holdout.'})
def run():
 f=read(R/'freeze.json')
 for p,h in f['files'].items():assert digest((ROOT/p).read_text())==h,p
 jobs=[(read(p),arm) for p in sorted((R/'packets').glob('*.json')) for arm in f['arms'] if not (arm=='binding' and read(p)['definition']==read(p)['binding'])];random.Random(20261004).shuffle(jobs)
 def job(j):
  p,arm=j;key=p['case_id']+'_'+p['world']+'_'+arm
  if (R/'raw'/f'{key}.json').exists():return
  prompt=previous.LEDGER_PROMPT+'\n'+FINAL_PROMPT+p['common']+'\n[EVIDENCE]\n'+p[arm];row={'run_id':key,'case_id':p['case_id'],'world':p['world'],'stage':p['stage'],'arm':arm,'prompt':prompt,'prompt_sha256':digest(prompt),'prompt_reference_tokens':token_count(prompt)};save(R/'inputs'/f'{key}.json',row);call_packet(row,R/'raw',f['model'],read(R/'schema.json'))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for future in concurrent.futures.as_completed([pool.submit(job,j) for j in jobs]):future.result()
 print('Completed',flush=True)
if __name__=='__main__':prepare() if sys.argv[1]=='prepare' else run()
