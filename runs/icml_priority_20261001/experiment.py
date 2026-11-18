"""Frozen four-arm inference ablation; old development and new near-holdout separate."""
import sys,json,random,subprocess,concurrent.futures,re
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import Corpus,FINAL_PROMPT,FINAL_SCHEMA,digest,token_count
from ace_review.context_adaptive_run import common
from ace_review.isolated_review import call_packet
from policy import lexical,definition_packet,Definitions,SELF_PROMPT,CRITIC_PROMPT,CRITIC_SCHEMA,REVISION_PROMPT
MODEL='gpt-5.6-sol'
def save(p,o):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(o,f,ensure_ascii=False,indent=2)
def prepare():
 # Resolver contract: exact imports/known constructors; unknown instance rejected.
 src={'a.py':'from b import C\ndef entry():\n x=C()\n return x.f()\n','b.py':'class C:\n def f(self):\n  return 1\n','c.py':'def unknown(obj):\n return obj.f()\n'}
 d=Definitions(None,src);assert d.resolve('a.py','C.f')==[('b.py','C.f')];assert any(t==[('b.py','C.f')] for _,t,_ in d.calls('a.py','entry'));assert d.calls('c.py','unknown')[0][1]==[];assert d.calls('b.py','C')==[]
 witnesses=[]
 for cid in ['H01','H02','H03','H04']:
  for world in ['safe','unsafe']:
   for rev in ['base','head']:
    for repeat in range(2):
     p=R/'holdout'/cid/'snapshots'/f'{world}_{rev}';r=subprocess.run([sys.executable,str(R/'witness_holdout.py'),cid,str(p)],capture_output=True,text=True,timeout=15)
     expected=10 if world=='unsafe' and rev=='head' else 0;assert r.returncode==expected,(cid,world,rev,r.stdout,r.stderr)
     witnesses.append(dict(case_id=cid,world=world,revision=rev,repeat=repeat,expected_exit=expected,actual_exit=r.returncode,output=json.loads(r.stdout)))
 save(R/'witnesses.json',witnesses)
 inputs=[]
 paths=[('development',p) for p in sorted((ROOT/'runs/context_fresh6_20260929/experiment_v1/cases').glob('F*.json'))]+[('near_holdout',R/'holdout'/f'{c}.json') for c in ['H01','H02','H03','H04']]
 for stage,p in paths:
  case=json.loads(p.read_text());cid=case['case_id']
  for world,spec in case['worlds'].items():
   spec=dict(spec,graphs={v:{'nodes':{},'edges':[]} for v in ['base','head']});local=dict(case,world_spec=spec);corpus=Corpus(spec['versions'],spec['graphs'],case['diff'])
   l=lexical(corpus,local);d,audit=definition_packet(corpus,local)
   packet={'case_id':cid,'world':world,'stage':stage,'common':common(case),'L':l.render(),'D':d.render(),'L_audit':l.audit(),'D_audit':audit,'source_sha256':corpus.source_hashes}
   save(R/'packets'/f'{cid}_{world}.json',packet);inputs.append(f'packets/{cid}_{world}.json')
   print(json.dumps({'prepared':cid+'_'+world,'stage':stage,'L_tokens':l.tokens,'D_tokens':d.tokens}),flush=True)
 hashes={str(p.relative_to(ROOT)):digest(p.read_text()) for p in [R/'policy.py',R/'experiment.py',R/'witness_holdout.py',R/'adjudication_protocol.json',ROOT/'ace_review/context_adaptive.py',ROOT/'ace_review/isolated_review.py',ROOT/'ace_review/auto_context.py']}
 hashes.update({str((R/p).relative_to(ROOT)):digest((R/p).read_text()) for p in inputs})
 save(R/'freeze.json',{'model':MODEL,'effort':'low','source_and_input_sha256':hashes,'arms':['L','D','SELF2','CRITIC'],'physical_calls_per_world':6,'planned_calls':len(inputs)*6,'max_concurrent_calls':4,'timeout_seconds':240,'retry':False,'source_token_cap':1600,'definition_prefix_cap':800,'definition_calls_max':12,'definition_depth_max':3,'self_rechecks':2,'critic_calls':1,'revision_calls':1,'scope':'Inference-only mechanism ablation, not reproduction of trained paper models. Same call counts for SELF2 and CRITIC, token counts not equal. Same repositories previously visible; near-holdout injected changes, not natural PRs. No V1 arm. No policy adaptation after development.','witness_sha256':digest((R/'witnesses.json').read_text()),'schemas':{'final':FINAL_SCHEMA,'critic':CRITIC_SCHEMA}})

def run(stage):
 freeze=json.loads((R/'freeze.json').read_text())
 for p,h in freeze['source_and_input_sha256'].items():assert digest((ROOT/p).read_text())==h,p
 packets=[json.loads(p.read_text()) for p in sorted((R/'packets').glob('*.json'))];packets=[p for p in packets if p['stage']==stage];random.Random(20261001).shuffle(packets)
 def ask(p,phase,prompt,schema):
  key=p['case_id']+'_'+p['world']+'_'+phase;path=R/'raw'/f'{key}.json'
  if path.exists():return json.loads(path.read_text())
  row=dict(run_id=key,case_id=p['case_id'],world=p['world'],stage=stage,phase=phase,prompt=prompt,prompt_sha256=digest(prompt),prompt_reference_tokens=token_count(prompt));save(R/'inputs'/f'{key}.json',row)
  result=call_packet(row,R/'raw',MODEL,schema)
  if result['status']=='ok':
   response=result['response'];ids=set(re.findall(r'E-[A-Za-z0-9_-]+',p['D'] if phase=='D' else p['L']))|{'DIFF'}
   errors=[]
   for item in response.get('issues',response.get('findings',[])):
    if not set(item['evidence_ids']).issubset(ids):errors.append('unknown evidence ID')
   if phase!='critic' and bool(response['issues'])!=(response['status']=='reported_issues'):errors.append('status/list mismatch')
   if errors:save(R/'invalid'/f'{key}.json',errors);result=dict(result,status='invalid')
  return result
 def world(p):
  base=FINAL_PROMPT+p['common']+'\n[EVIDENCE]\n'+p['L'];l=ask(p,'L',base,FINAL_SCHEMA);d=ask(p,'D',FINAL_PROMPT+p['common']+'\n[EVIDENCE]\n'+p['D'],FINAL_SCHEMA)
  if l['status']!='ok':return
  initial=json.dumps(l['response'],ensure_ascii=False)
  s1=ask(p,'self1',SELF_PROMPT+'\n'+base+'\n[PREVIOUS REVIEW]\n'+initial,FINAL_SCHEMA)
  if s1['status']=='ok':ask(p,'SELF2',SELF_PROMPT+'\n'+base+'\n[PREVIOUS REVIEW]\n'+json.dumps(s1['response'],ensure_ascii=False),FINAL_SCHEMA)
  c=ask(p,'critic',CRITIC_PROMPT+'\n'+p['common']+'\n[EVIDENCE]\n'+p['L']+'\n[INITIAL REVIEW]\n'+initial,CRITIC_SCHEMA)
  if c['status']=='ok':
   indices=[x['issue_index'] for x in c['response']['findings']]
   if sorted(indices)!=list(range(len(l['response']['issues']))):save(R/'invalid'/f"{p['case_id']}_{p['world']}_critic_coverage.json",indices);return
   ask(p,'critic_final',REVISION_PROMPT+'\n'+base+'\n[INITIAL REVIEW]\n'+initial+'\n[CRITIC AUDIT]\n'+json.dumps(c['response'],ensure_ascii=False),FINAL_SCHEMA)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for f in concurrent.futures.as_completed([pool.submit(world,p) for p in packets]):f.result()
 print('Stage completed:',stage,flush=True)
if __name__=='__main__':
 if sys.argv[1]=='prepare':prepare()
 else:run(sys.argv[1])
