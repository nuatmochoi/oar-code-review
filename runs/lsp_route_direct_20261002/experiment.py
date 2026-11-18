"""Frozen old automatic policy A0 versus reserved A1; 12 new + 12 reused calls."""
import concurrent.futures,hashlib,importlib.util,json,random
from pathlib import Path
import shared
from ace_review.context_adaptive import Corpus,digest,token_count
from ace_review.isolated_review import call_packet
R=Path(__file__).resolve().parent;ROOT=shared.ROOT
OLD=ROOT/'runs/lsp_auto_routes_20261002';PRIOR=ROOT/'runs/lsp_reserved_routes_20261002'
read,save,prompt=shared.read,shared.save,shared.prompt
sp=importlib.util.spec_from_file_location('direct_frozen_A0',OLD/'policy.py');policy=importlib.util.module_from_spec(sp);sp.loader.exec_module(policy)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def verify_prior():
 for d in [OLD,PRIOR]:
  seal=read(d/'result_seal.json');assert all(sha(d/p)==h for p,h in seal['files'].items())
 assert read(PRIOR/'witness_validation.json')['all_contracts_verified']
def prepare_one(path):
 old=read(path);stem=path.stem;head={file:(ROOT/old['workspace_reference']/file).read_text() for file in read(PRIOR/'workspace_manifests'/f'{stem}.json')['source_sha256']}
 root=R/'workspaces'/stem;root.mkdir()
 for file,source in head.items():
  p=root/file;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(source)
 config=read(PRIOR/'workspace_manifests'/f'{stem}.json')['pyrightconfig'];save(root/'pyrightconfig.json',config);save(R/'workspace_manifests'/f'{stem}.json',{'source_sha256':{f:digest(s) for f,s in head.items()},'pyrightconfig':config})
 diff=old['common'].split('[DIFF]\n',1)[1];c=Corpus({'base':head,'head':head},{v:{'nodes':{},'edges':[]} for v in ['base','head']},diff);resolver=shared.native_policy.ref.old.Bindings(c,head);client=shared.native_policy.Client(root)
 try:
  native=shared.native_policy.ref.LSPBackend(resolver,client);source,obs,audit=policy.packet(head,diff,old['common'].split('[DIFF]',1)[0],resolver,native,client,shared.native_policy.b.edited_lines)
 finally:client.close()
 save(R/'lsp_traces'/f'{stem}_A0.json',{'trace':client.trace,'initialization':client.initialization,'stderr':client.errors})
 p={k:old[k] for k in ['case_id','world','instance_id','repository','common','source_sha256','source_selection']};p.update(group='direct_route_policy',workspace_reference=str(root.relative_to(ROOT)),A0=source,A0_observations=obs,A0_audit=audit,A1=old['A'],A1_observations=old['A_observations'],A1_audit=old['A_audit'],A1_original_packet=str(path.relative_to(ROOT)))
 save(R/'packets'/f'{stem}.json',p);print(f'Prepared {stem}: A0={token_count(source)}, A1={token_count(old["A"])}',flush=True)
def prepare():
 verify_prior()
 for name in ['packets','inputs','raw','lsp_traces','workspaces','workspace_manifests']:(R/name).mkdir(exist_ok=False)
 save(R/'labels.json',read(PRIOR/'labels.json'));save(R/'schema.json',read(PRIOR/'schema.json'))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(prepare_one,sorted((PRIOR/'packets').glob('*.json'))))
 jobs=[];reused={};grades={};deps=[]
 prior_annotations=read(PRIOR/'annotations_all.json')
 for path in sorted((R/'packets').glob('*.json')):
  p=read(path)
  for arm in ['A0','A1']:
   for rep in [1,2,3]:
    rid=f'{path.stem}_{arm}_r{rep}';text=prompt(p,arm);inp={'run_id':rid,'case_id':p['case_id'],'world':p['world'],'group':p['group'],'arm':arm,'repeat':rep,'prompt':text,'prompt_sha256':digest(text),'prompt_reference_tokens':token_count(text)};save(R/'inputs'/f'{rid}.json',inp)
    if arm=='A0':jobs.append({k:inp[k] for k in ['run_id','case_id','world','group','arm','repeat']})
    else:
     previous=f'{path.stem}_A_r{rep}';raw=PRIOR/'raw'/f'{previous}.json';oldinput=PRIOR/'inputs'/f'{previous}.json';assert read(oldinput)['prompt']==text and read(raw)['prompt_sha256']==digest(text)
     reused[rid]={'raw_path':str(raw.relative_to(ROOT)),'input_path':str(oldinput.relative_to(ROOT)),'original_run_id':previous,'annotation_path':str((PRIOR/'annotations_all.json').relative_to(ROOT))};grades[rid]=prior_annotations[previous];deps.extend([raw,oldinput,PRIOR/'packets'/f'{path.stem}.json',PRIOR/'lsp_traces'/f'{path.stem}_A.json'])
 save(R/'reused_annotations.json',grades)
 files=[p for p in R.glob('*') if p.is_file()]+[p for d in ['packets','inputs','lsp_traces','workspace_manifests'] for p in (R/d).glob('*.json')]+deps
 files += [OLD/'policy.py',OLD/'algorithm_seal.json',OLD/'result_seal.json',PRIOR/'policy.py',PRIOR/'algorithm_seal.json',PRIOR/'result_seal.json',PRIOR/'annotations_all.json',PRIOR/'results_all.json',PRIOR/'witnesses.json',PRIOR/'witness_validation.json',PRIOR/'GRADING_KO.md']
 # Preserve transitive runtime/prompt dependency hashes from prior frozen run.
 files += [ROOT/p for p in read(PRIOR/'freeze.json')['files']]
 save(R/'freeze.json',{'model':'gpt-5.6-sol','effort':'low','logical_reviews':24,'new_physical_calls':12,'reused_physical_calls':12,'unique_physical_calls_in_comparison':24,'jobs':jobs,'reused':reused,'source_cap':2600,'observation_cap':400,'native_query_cap_per_arm':12,'repeats':3,'workers':4,'retry':False,'provider_seed_control':False,'new_witness_calls':0,'new_native_queries':48,'policy_A0_unchanged':True,'A1_grading_locked_before_A0_calls':True,'contrast':'Same two PR/direction/common/schema/corpus/max budgets. Policy bundle differs, actual source lengths differ. Later A0 batch versus reused A1 batch; no paired seed/concurrent evaluation or new PR generalization. Both policies sealed before first selection of these PR IDs.','files':{str(p.relative_to(ROOT)):sha(p) for p in files}})
 print('Frozen 12 new A0 calls and 12 reused A1 calls',flush=True)
def run():
 f=read(R/'freeze.json');assert all(sha(ROOT/p)==h for p,h in f['files'].items())
 for m in (R/'workspace_manifests').glob('*.json'):assert all(digest((R/'workspaces'/m.stem/p).read_text())==h for p,h in read(m)['source_sha256'].items())
 jobs=list(f['jobs']);random.Random(20261030).shuffle(jobs)
 def one(job):
  rid=job['run_id'];assert not (R/'raw'/f'{rid}.json').exists();call_packet(read(R/'inputs'/f'{rid}.json'),R/'raw',f['model'],read(R/'schema.json'))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(one,jobs))
if __name__=='__main__':
 import sys
 prepare() if sys.argv[1]=='prepare' else run()
