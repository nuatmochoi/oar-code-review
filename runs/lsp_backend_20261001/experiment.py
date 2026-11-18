"""Actual LSP source retrieval, frozen paired model experiment; no RL claims."""
from __future__ import annotations
import ast,copy,json,sys,os,difflib,random,importlib.util,concurrent.futures,time,subprocess
from pathlib import Path
from functools import lru_cache
from types import SimpleNamespace as NS
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import Corpus,digest,token_count,FINAL_PROMPT
from ace_review.context_adaptive_run import common
from ace_review.isolated_review import call_packet
from lsp_client import Client,SERVER
from backend import old,ASTBackend,LSPBackend,packet,position
spec=importlib.util.spec_from_file_location('ledger_prior',ROOT/'runs/grounded_scope_20261001/experiment.py');prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
MODEL='gpt-5.6-sol'
def read(p):return json.loads(p.read_text())
def save(p,o):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(o,f,ensure_ascii=False,indent=2)
def materialize(sources,key):
 root=R/'workspaces'/key;root.mkdir(parents=True,exist_ok=False);pool=R/'source_pool';pool.mkdir(exist_ok=True);hashes={}
 for file,src in sorted(sources.items()):
  assert not Path(file).is_absolute() and '..' not in Path(file).parts
  h=digest(src);hashes[file]=h;shared=pool/(h+'.py')
  if not shared.exists():shared.write_text(src)
  assert digest(shared.read_text())==h
  dest=root/file;dest.parent.mkdir(parents=True,exist_ok=True);os.link(shared,dest)
 config={'include':['**/*.py'],'pythonVersion':'3.11','typeCheckingMode':'off','autoSearchPaths':False,'useLibraryCodeForTypes':False,'extraPaths':['.']}
 (root/'pyrightconfig.json').write_text(json.dumps(config,indent=2)+'\n')
 assert all(digest((root/f).read_text())==h for f,h in hashes.items())
 return root,{'source_sha256':hashes,'pyrightconfig':config}
def prepare():
 assert not (R/'freeze.json').exists()
 for d in ['raw','inputs','packets','sources','lsp_traces','workspace_manifests']:(R/d).mkdir(exist_ok=False)
 ast._splitlines_no_ff=lru_cache(maxsize=128)(ast._splitlines_no_ff)
 # Same previously frozen source cases, with no label/witness input to either resolver.
 previous=ROOT/'runs/grounded_scope_20261001';icml=ROOT/'runs/icml_priority_20261001';labels=read(previous/'labels.json');labels['P08']=copy.deepcopy(labels['F08']);cases=[];witnesses=[]
 for pp in sorted((ROOT/'runs/binding_context_20261001/packets').glob('*.json')):
  original=read(pp);cid=original['case_id'];world=original['world']
  if cid.startswith('F') or cid=='P08':
   case=read(ROOT/f'runs/context_fresh6_20260929/experiment_v1/cases/{"F08" if cid=="P08" else cid}.json');versions=case['worlds'][world]['versions']
  elif cid.startswith('H'):
   case=read(icml/'holdout'/f'{cid}.json');versions=case['worlds'][world]['versions']
  else:
   src=read(previous/'new_sources'/pp.name);base=dict(read(icml/'holdout/H03.json')['worlds']['a']['versions']['base']);base.pop('deployment.py');versions={}
   for rev in ['base','head']:versions[rev]=dict(base,**{src['changed_file']:src[rev],'deployment.py':src['deployment']})
   case={'scope':src['scope'],'diff':src['diff']}
  cases.append(dict(case_id=cid,world=world,stage='observed_regression' if cid!='P08' else 'observed_png_diagnostic',scope=case['scope'],diff=case['diff'],common=original['common'],versions=versions))
 original=read(ROOT/'runs/context_fresh6_20260929/experiment_v1/cases/F08.json');base_sources=dict(original['worlds']['a']['versions']['base']);base_sources.pop('deployment.py');file='comfy_extras/nodes_string.py';src=base_sources[file]
 configs=[('W01','StringConcatenate','delimiter.join((string_a, string_b))','delimiter.join((string_b, string_a))',{'string_a':'ab','string_b':'ab','delimiter':'-'},{'string_a':'ab','string_b':'cd','delimiter':'-'},'Concatenate string_a then string_b using delimiter.','conditional_factory'),('W02','RegexMatch','match = re.search(regex_pattern, string, flags)','match = re.match(regex_pattern, string, flags)',{'string':'abc','regex_pattern':'a','case_insensitive':False,'multiline':False,'dotall':False},{'string':'abc','regex_pattern':'b','case_insensitive':False,'multiline':False,'dotall':False},'Find regex matches anywhere in the supplied string.','inheritance'),('W03','StringLength','io.NodeOutput(len(string))','io.NodeOutput(len(string.strip()))',{'string':'abc'},{'string':' abc '},'Return supplied string length including whitespace.','reexport'),('W04','RegexReplace','count=count, flags=flags','count=0, flags=flags',{'string':'a a','regex_pattern':'a','replace':'X','count':0},{'string':'a a','regex_pattern':'a','replace':'X','count':1},'Honor regex replacement count; zero means all matches.','property')]
 rng=random.Random(20261011)
 for cid,cls,before,after,safe,unsafe,contract,pattern in configs:
  n=next(m for c in ast.parse(src).body if isinstance(c,ast.ClassDef) and c.name==cls for m in c.body if isinstance(m,ast.FunctionDef) and m.name=='execute');segment='\n'.join(src.splitlines()[n.lineno-1:n.end_lineno]);assert segment.count(before)==1;head=src.replace(segment,segment.replace(before,after),1);diff=''.join(difflib.unified_diff(src.splitlines(True),head.splitlines(True),fromfile='a/'+file,tofile='b/'+file));mapping=['safe','unsafe'];rng.shuffle(mapping);labels[cid]={'worlds':dict(zip(['a','b'],mapping)),'class':cls,'pattern':pattern,'contract':contract}
  for world,label in labels[cid]['worlds'].items():
   args=safe if label=='safe' else unsafe;helper=f'def arguments():\n    return {args!r}\n';extra={}
   if pattern=='conditional_factory':
    dep=f'from comfy_extras.nodes_string import {cls}\n\n{helper}\ndef factory(flag: bool) -> {cls}:\n    if flag:\n        return {cls}()\n    return {cls}()\n\ndef entry():\n    return factory(True).execute(**arguments())\n'
   elif pattern=='inheritance':
    extra['bridge.py']=f'from comfy_extras.nodes_string import {cls}\n\nclass Adapter({cls}):\n    pass\n';dep=f'from bridge import Adapter\n\n{helper}\ndef entry():\n    return Adapter().execute(**arguments())\n'
   elif pattern=='reexport':
    extra['public_api.py']=f'from comfy_extras.nodes_string import {cls} as ReviewService\n';dep=f'from public_api import ReviewService\n\n{helper}\ndef entry():\n    return ReviewService().execute(**arguments())\n'
   else:
    extra['bridge.py']=f'from comfy_extras.nodes_string import {cls}\n\nclass Wrapper:\n    @property\n    def engine(self) -> {cls}:\n        return {cls}()\n';dep=f'from bridge import Wrapper\n\n{helper}\ndef entry():\n    return Wrapper().engine.execute(**arguments())\n'
   versions={};outs={}
   for rev,body in [('base',src),('head',head)]:
    versions[rev]=dict(base_sources,**{file:body,'deployment.py':dep},**extra)
    node=copy.deepcopy(next(m for c in ast.parse(body).body if isinstance(c,ast.ClassDef) and c.name==cls for m in c.body if isinstance(m,ast.FunctionDef) and m.name=='execute'));node.decorator_list=[];module=ast.Module(body=[node],type_ignores=[]);ast.fix_missing_locations(module);env={'re':__import__('re'),'io':NS(NodeOutput=lambda *v:v)};exec(compile(module,file,'exec'),env)
    for repeat in range(2):
     actual=env['execute'](None,**copy.deepcopy(args))[0];outs[rev]=actual;witnesses.append(dict(case_id=cid,world=world,revision=rev,repeat=repeat,args=args,actual=actual,boundary='Actual execute body; NodeOutput tuple passthrough; wrapper/framework not executed.'))
   assert (outs['base']!=outs['head'])==(label=='unsafe')
   scope=f'Only deployment.entry() with its fixed arguments() is deployed. {contract} The visible wrapper defines the receiver; io.NodeOutput exposes supplied values unchanged. Other UI/graph and other API inputs are outside scope. Analyze this declared source binding, not actual production traffic.'
   cases.append(dict(case_id=cid,world=world,stage='structural_control_same_repository',scope=scope,diff=diff,common=common(dict(repository='ComfyUI9560 derived structural control',scope=scope,diff=diff)),versions=versions));save(R/'sources'/f'{cid}_{world}.json',{'changed_file':file,'base':src,'head':head,'deployment':dep,'extras':extra,'scope':scope,'full_source_reference':'runs/context_fresh6_20260929/experiment_v1/cases/F08.json'})
 for case in cases:
  start=time.monotonic();cid=case['case_id'];world=case['world'];key=cid+'_'+world;versions=case['versions'];graphs={v:{'nodes':{},'edges':[]} for v in ['base','head']};corpus=Corpus(versions,graphs,case['diff']);resolver=old.Bindings(corpus,versions['head']);local=dict(case,world_spec={'versions':versions});root,manifest=materialize(versions['head'],key);save(R/'workspace_manifests'/f'{key}.json',manifest)
  a,aa=packet(corpus,local,resolver,ASTBackend(resolver));client=Client(root)
  try:b,ba=packet(corpus,local,resolver,LSPBackend(resolver,client))
  finally:client.close()
  save(R/'lsp_traces'/f'{key}.json',{'initialization':client.initialization,'startup_seconds':client.startup_seconds,'trace':client.trace,'stderr':client.errors,'definition_requests':sum(x.get('method')=='textDocument/definition' for x in client.trace)})
  assert all(digest((root/f).read_text())==h for f,h in manifest['source_sha256'].items())
  save(R/'packets'/f'{key}.json',dict(case_id=cid,world=world,stage=case['stage'],common=case['common'],AST=a.render(),LSP=b.render(),AST_audit=aa,LSP_audit=ba,source_sha256=corpus.source_hashes,prepare_seconds=time.monotonic()-start))
  print(json.dumps({'prepared':key,'different':a.render()!=b.render(),'AST_tokens':a.tokens,'LSP_tokens':b.tokens,'definition_requests':sum(x.get('method')=='textDocument/definition' for x in client.trace)}),flush=True)
 save(R/'labels.json',labels);save(R/'witnesses.json',witnesses);save(R/'schema.json',prior.LEDGER_SCHEMA)
 packets=[read(p) for p in (R/'packets').glob('*.json')];shared={p['case_id']+'_'+p['world']+'_lsp':p['case_id']+'_'+p['world']+'_ast' for p in packets if p['AST']==p['LSP']};planned=len(packets)*2-len(shared)
 files=[R/f for f in ['experiment.py','backend.py','lsp_client.py','check_backends.py','resolver_tests.json','resolver_trace.json','PROTOCOL_KO.md','labels.json','witnesses.json','schema.json']]+[p for d in ['packets','sources','lsp_traces','workspace_manifests'] for p in (R/d).glob('*.json')]+list((R/'resolver_workspace').glob('*.py'))+[ROOT/'runs/binding_context_20261001/binding.py',ROOT/'runs/icml_priority_20261001/policy.py',ROOT/'runs/grounded_scope_20261001/experiment.py',ROOT/'ace_review/context_adaptive.py',ROOT/'ace_review/isolated_review.py',ROOT/'ace_review/reviewer.py',ROOT/'ace_review/packet_review.py',ROOT/'tools/pyright-lsp-20261001/package-lock.json',SERVER]
 save(R/'freeze.json',{'model':MODEL,'effort':'low','source_cap':1600,'logical_reviews':len(packets)*2,'planned_physical_calls':planned,'shared_identical_inputs':shared,'pyright_version':read(SERVER.parent.parent/'package.json')['version'],'node_version':subprocess.check_output(['/opt/homebrew/bin/node','--version'],text=True).strip(),'python_version':sys.version,'files':{str(p.relative_to(ROOT)):digest(p.read_text()) for p in files},'retry':False,'workers':4,'limits':{'prefix':800,'depth':3,'queries':12},'RL':False,'backend_only':True})
 print(json.dumps({'freeze':'complete','logical_reviews':len(packets)*2,'planned_calls':planned,'identical_worlds':len(shared),'new_witnesses':len(witnesses)}),flush=True)
def run():
 f=read(R/'freeze.json')
 for p,h in f['files'].items():assert digest((ROOT/p).read_text())==h,p
 # Check full source snapshot identity before calling reviewers.
 for pp in (R/'workspace_manifests').glob('*.json'):
  m=read(pp);assert all(digest((R/'workspaces'/pp.stem/file).read_text())==h for file,h in m['source_sha256'].items()),pp.name
 jobs=[(read(p),arm) for p in sorted((R/'packets').glob('*.json')) for arm in ['ast','lsp'] if p.stem+'_'+arm not in f['shared_identical_inputs']];random.Random(20261012).shuffle(jobs)
 def worker(job):
  p,arm=job;key=p['case_id']+'_'+p['world']+'_'+arm
  if (R/'raw'/f'{key}.json').exists():return
  prompt=prior.LEDGER_PROMPT+'\n'+FINAL_PROMPT+p['common']+'\n[EVIDENCE]\n'+p[arm.upper()]+'\n[STATIC DEFINITION OBSERVATIONS — NOT RUNTIME PROOF]\n'+p[arm.upper()+'_observations'];row=dict(run_id=key,case_id=p['case_id'],world=p['world'],stage=p['stage'],arm=arm,prompt=prompt,prompt_sha256=digest(prompt),prompt_reference_tokens=token_count(prompt));save(R/'inputs'/f'{key}.json',row);call_packet(row,R/'raw',MODEL,read(R/'schema.json'))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for t in concurrent.futures.as_completed([pool.submit(worker,j) for j in jobs]):t.result()
 print('Completed reviewer comparison.',flush=True)
if __name__=='__main__':prepare() if sys.argv[1]=='prepare' else run()
