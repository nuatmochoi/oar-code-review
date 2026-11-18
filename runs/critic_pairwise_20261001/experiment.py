"""Frozen source-identical staged review/critic/pairwise mechanism comparison."""
from __future__ import annotations
import ast,copy,json,sys,re,random,difflib,importlib.util,concurrent.futures
from pathlib import Path
from types import SimpleNamespace as NS
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import FINAL_PROMPT,FINAL_SCHEMA,digest,token_count
from ace_review.context_adaptive_run import common
from ace_review.isolated_review import call_packet
spec=importlib.util.spec_from_file_location('ledger_prior',ROOT/'runs/grounded_scope_20261001/experiment.py');prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
MODEL='gpt-5.6-sol';CAP=4000
CRITIC='''Audit and revise the INITIAL REVIEW using only the same supplied evidence. Restrict the audit to actual receiver/configuration binding, actual declared input, exercised branch, and observable base/head difference. Do not invent another deployment, substitute a query suffix for request.path, or infer a receiver from an unused import. A missing binding/result requires uncertainty, not automatic safety. Preserve established in-scope defects, even if other inputs are safe. Remove a warning contradicted by actual inputs or guards. You may add an omitted defect only when these four facts are established in the visible sources. Output concise factual checks and the final review; do not provide hidden reasoning. No tools.\n'''
PAIR='''Compare the two anonymized code review candidates using only the patch, declared scope and supplied source evidence. Prefer the candidate whose reported defects have an established receiver/input, reached branch and introduced base/head behavior difference. Unsupported extra warnings make a candidate worse. Prefer justified no_supported_issue over invented defects, and justified insufficient_evidence over guessed safety. An assertion in a candidate ledger is not source evidence. Select LEFT or RIGHT, tie if equally supported, insufficient if neither can yield a supported verdict. Do not rewrite candidates or introduce new issues. Give a short factual comparison and cite only E-* IDs/DIFF. No tools. JSON only.\n'''
PAIR_SCHEMA={'type':'object','additionalProperties':False,'required':['choice','comparison','evidence_ids'],'properties':{'choice':{'type':'string','enum':['LEFT','RIGHT','tie','insufficient']},'comparison':{'type':'string'},'evidence_ids':{'type':'array','items':{'type':'string'}}}}
CONFIGS=[
('J01','StringConcatenate','delimiter.join((string_a, string_b))','delimiter.join((string_b, string_a))',{'string_a':'ab','string_b':'ab','delimiter':'-'},{'string_a':'ab','string_b':'cd','delimiter':'-'},'Concatenate string_a then string_b using delimiter.'),
('J02','StringLength','io.NodeOutput(len(string))','io.NodeOutput(len(string.strip()))',{'string':'abc'},{'string':' abc '},'Return length of the supplied string, including whitespace.'),
('J03','RegexMatch','match = re.search(regex_pattern, string, flags)','match = re.match(regex_pattern, string, flags)',{'string':'abc','regex_pattern':'a','case_insensitive':False,'multiline':False,'dotall':False},{'string':'abc','regex_pattern':'b','case_insensitive':False,'multiline':False,'dotall':False},'Find a regex match anywhere in the input string; selected flags apply.'),
('J04','RegexReplace','count=count, flags=flags','count=0, flags=flags',{'string':'a a','regex_pattern':'a','replace':'X','count':0},{'string':'a a','regex_pattern':'a','replace':'X','count':1},'Regex replacement must honor count; count zero means all matches.'),
('J05','CaseConverter','result = string.capitalize()','result = string.lower()',{'string':'123','mode':'Capitalize'},{'string':'hELLO','mode':'Capitalize'},'Honor selected Capitalize mode.'),
('J06','StringCompare','io.NodeOutput(a.endswith(b))','io.NodeOutput(a.startswith(b))',{'string_a':'aba','string_b':'a','mode':'Ends With','case_sensitive':True},{'string_a':'abc','string_b':'c','mode':'Ends With','case_sensitive':True},'Honor selected Ends With mode and case sensitivity.'),
]
def read(p):return json.loads(p.read_text())
def save(p,obj):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(obj,f,ensure_ascii=False,indent=2)
def getnode(src,name,cls=None):
 tree=ast.parse(src)
 if cls:tree=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
 nodes=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name]
 assert len(nodes)==1,(cls,name,len(nodes));return nodes[0]
def evidence(sections):
 units=[];text=[]
 for i,(file,rev,src,start,end) in enumerate(sections,1):
  eid=f'E-{i:03d}';excerpt='\n'.join(f'{j}: {line}' for j,line in enumerate(src.splitlines(),1) if start<=j<=end)
  text.append(f'[{eid}] {rev} {file}:{start}-{end}\n'+excerpt)
  units.append(dict(id=eid,file=file,version=rev,start=start,end=end,source_sha256=digest(src)))
 rendered='\n\n'.join(text);assert token_count(rendered)<=CAP,token_count(rendered)
 return rendered,units

def prepare():
 assert not (R/'freeze.json').exists()
 for name in ['raw','inputs','packets','sources']:(R/name).mkdir(exist_ok=False)
 rng=random.Random(20261008);labels={};witnesses=[];pairs=[]
 file='comfy_extras/nodes_string.py';src=(ROOT/'runs/context_fresh6_20260929/snapshots/9560'/file).read_text()
 for cid,cls,before,after,safe,unsafe,contract in CONFIGS:
  target=getnode(src,'execute',cls);segment='\n'.join(src.splitlines()[target.lineno-1:target.end_lineno]);assert segment.count(before)==1,(cid,before);head=src.replace(segment,segment.replace(before,after),1);diff=''.join(difflib.unified_diff(src.splitlines(True),head.splitlines(True),fromfile='a/'+file,tofile='b/'+file));mapping=['safe','unsafe'];rng.shuffle(mapping);labels[cid]={'stage':'fresh_mutation_same_repository','worlds':dict(zip(['a','b'],mapping)),'target_file':file,'target_changed_line':next(i for i,(old,new) in enumerate(zip(src.splitlines(),head.splitlines()),1) if old!=new),'class':cls,'contract':contract}
  for world,label in labels[cid]['worlds'].items():
   args=safe if label=='safe' else unsafe;dep=f'from comfy_extras.nodes_string import {cls}\n\ndef arguments():\n    return {args!r}\n\ndef entry():\n    return {cls}().execute(**arguments())\n'
   sections=[];outs={}
   for rev,body in [('base',src),('head',head)]:
    node=getnode(body,'execute',cls);sections.append((file,rev,body,node.lineno,node.end_lineno))
    executable=copy.deepcopy(node);executable.decorator_list=[];module=ast.Module(body=[executable],type_ignores=[]);ast.fix_missing_locations(module);env={'io':NS(NodeOutput=lambda *values:values),'re':re};exec(compile(module,file,'exec'),env)
    for repeat in range(2):
     out=env['execute'](None,**copy.deepcopy(args))[0];outs[rev]=out;witnesses.append(dict(case_id=cid,world=world,revision=rev,repeat=repeat,input=args,actual=out,source_span={'file':file,'start':node.lineno,'end':node.end_lineno},boundary='io.NodeOutput tuple passthrough; actual execute AST; no ComfyUI server'))
   assert (outs['base']!=outs['head'])==(label=='unsafe'),(cid,world,outs)
   sections.append(('deployment.py','head',dep,1,len(dep.splitlines())));text,units=evidence(sections)
   scope=f'Only deployment.entry() with fixed arguments() is in scope. {contract} The unchanged entry binds the named class directly. io.NodeOutput exposes supplied output values without transforming them. Other UI/graph inputs are outside this controlled experiment.'
   packet=dict(case_id=cid,world=world,stage=labels[cid]['stage'],common=common({'repository':'ComfyUI pinned snapshot 9560','scope':scope,'diff':diff}),evidence=text,units=units,source_tokens=token_count(text))
   save(R/'packets'/f'{cid}_{world}.json',packet);save(R/'sources'/f'{cid}_{world}.json',dict(file=file,base=src,head=head,deployment=dep,scope=scope));pairs.append(packet)
 # Diagnostics use actual source chains established before this experiment, not prior synthetic deployment.py.
 for cid,origin in [('D04','F04'),('D08','F08')]:
  old=read(ROOT/f'runs/context_fresh6_20260929/experiment_v1/cases/{origin}.json');versions=old['worlds']['a']['versions']
  labels[cid]={'stage':'previously_observed_actual_entry_diagnostic','worlds':{'a':'unsafe','b':'safe'} if cid=='D04' else {'a':'safe','b':'safe'},'target_file':'rag/utils/infinity_conn.py' if cid=='D04' else 'middleware/cache_middleware.py','target_changed_line':588 if cid=='D04' else 43}
  for world in ['a','b']:
   sections=[]
   if cid=='D04':
    file='rag/utils/infinity_conn.py'
    for rev in ['base','head']:
     body=versions[rev][file]
     for name in ['field_keyword','update']:
      n=getnode(body,name);sections.append((file,rev,body,n.lineno,n.end_lineno))
    body=versions['head']['api/apps/kb_app.py'];n=getnode(body,'update');sections.append(('api/apps/kb_app.py','head',body,n.lineno,n.end_lineno))
    body=versions['head']['api/settings.py'];sections.append(('api/settings.py','head',body,133,141))
    body=versions['head']['rag/settings.py'];sections.append(('rag/settings.py','head',body,59,59))
    val=0 if world=='a' else 2
    scope=f'Actual api.apps.kb_app.update function body under declared DOC_ENGINE=infinity, initialized using api.settings.init_settings backend-selection branch. Request JSON kb_id=kb-1, name=fixture, description=fixture, parser_id=naive, pagerank={val}; existing kb.name=fixture, kb.pagerank=1, kb.id=kb-1, kb.tenant_id=tenant-1. Authentication, ownership and ORM lookups/update all succeed. Target table has columns id and pagerank_fea, with pagerank_fea default1.0; remove is a control directive, not a table column. Index naming is non-target. Review only changed behavior for this request; actual production environment is not asserted.'
   else:
    file='middleware/cache_middleware.py'
    for rev in ['base','head']:
     body=versions[rev][file];sections.append((file,rev,body,1,len(body.splitlines())))
    body=versions['head']['server.py'];n=getnode(body,'view_image');sections.append(('server.py','head',body,416,n.end_lineno));sections.append(('server.py','head',body,162,173))
    q="filename=missing.png" if world=='a' else 'no filename query'
    scope=f'Actual registered /view image route, request.path=/view, query {q}, no other query parameters. When filename is supplied, annotated_filepath returns that filename and an existing empty output folder; missing.png does not exist. web.Response defaults to empty response headers. Only cache_control behavior around the actual view_image body is reviewed; other middleware/browser caching is outside scope. Do not treat filename query suffix as request.path.'
   text,units=evidence(sections);packet=dict(case_id=cid,world=world,stage=labels[cid]['stage'],common=common(dict(repository=old['repository'],scope=scope,diff=old['diff'])),evidence=text,units=units,source_tokens=token_count(text));save(R/'packets'/f'{cid}_{world}.json',packet);pairs.append(packet)
 order={}
 for p in sorted(pairs,key=lambda p:(p['case_id'],p['world'])):
  order[p['case_id']+'_'+p['world']]=['baseline','alternate'] if rng.random()<.5 else ['alternate','baseline']
 save(R/'labels.json',labels);save(R/'witnesses.json',witnesses);save(R/'order.json',order);save(R/'schemas.json',{'review':prior.LEDGER_SCHEMA,'pair':PAIR_SCHEMA})
 files=[p for d in ['packets','sources'] for p in (R/d).glob('*.json')]+[R/'experiment.py',R/'PROTOCOL_KO.md',R/'labels.json',R/'witnesses.json',R/'order.json',R/'schemas.json',ROOT/'runs/grounded_scope_20261001/experiment.py',ROOT/'runs/real_entry_provenance_20261001/observations.json',ROOT/'ace_review/context_adaptive.py',ROOT/'ace_review/isolated_review.py',ROOT/'ace_review/reviewer.py',ROOT/'ace_review/packet_review.py']
 save(R/'freeze.json',{'model':MODEL,'effort':'low','planned_calls':68,'source_cap':CAP,'witness_count':len(witnesses),'diagnostic_witness_reference':'runs/real_entry_provenance_20261001/observations.json','files':{str(p.relative_to(ROOT)):digest(p.read_text()) for p in files},'swapped_cases':['J01','J02'],'retry':False,'workers':4,'pair_tie_policy':'baseline','pair_insufficient_policy':'abstain','prompt_hashes':{'review':digest(prior.LEDGER_PROMPT+'\n'+FINAL_PROMPT),'critic':digest(CRITIC),'pair':digest(PAIR)}})
 print(json.dumps({'prepared_worlds':len(pairs),'fresh_witnesses':len(witnesses),'max_source_tokens':max(p['source_tokens'] for p in pairs),'planned_calls':68}),flush=True)

def run(stage):
 f=read(R/'freeze.json')
 for p,h in f['files'].items():assert digest((ROOT/p).read_text())==h,p
 schemas=read(R/'schemas.json');order=read(R/'order.json');packets=[read(p) for p in sorted((R/'packets').glob('*.json'))];random.Random(20261009).shuffle(packets)
 def ask(p,phase,prompt,schema):
  key=f"{p['case_id']}_{p['world']}_{phase}";dest=R/'raw'/f'{key}.json'
  if dest.exists():return read(dest)
  assert not (R/'inputs'/f'{key}.json').exists(),f'Pending input without raw: manual audit needed {key}'
  row=dict(run_id=key,case_id=p['case_id'],world=p['world'],stage=p['stage'],phase=phase,prompt=prompt,prompt_sha256=digest(prompt),prompt_reference_tokens=token_count(prompt));save(R/'inputs'/f'{key}.json',row)
  return call_packet(row,R/'raw',MODEL,schema)
 def worker(p):
  context=p['common']+'\n[EVIDENCE]\n'+p['evidence'];base=prior.LEDGER_PROMPT+'\n'+FINAL_PROMPT+context
  initial=ask(p,'baseline',base,schemas['review'])
  if stage=='critic':
   if initial['status']=='ok':ask(p,'critic',CRITIC+'\n'+prior.LEDGER_PROMPT+'\n'+FINAL_PROMPT+context+'\n[INITIAL REVIEW]\n'+json.dumps(initial['response'],ensure_ascii=False),schemas['review'])
  else:
   alternate=ask(p,'alternate',base,schemas['review'])
   if initial['status']!='ok' or alternate['status']!='ok':return
   candidates={'baseline':initial['response'],'alternate':alternate['response']};shown=order[p['case_id']+'_'+p['world']]
   def choose(names,phase):
    prompt=PAIR+context+'\n[LEFT CANDIDATE]\n'+json.dumps(candidates[names[0]],ensure_ascii=False)+'\n[RIGHT CANDIDATE]\n'+json.dumps(candidates[names[1]],ensure_ascii=False)
    ask(p,phase,prompt,schemas['pair'])
   choose(shown,'pair')
   if p['case_id'] in f['swapped_cases']:choose(shown[::-1],'pair_swapped')
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for task in concurrent.futures.as_completed([pool.submit(worker,p) for p in packets]):task.result()
 print('Completed stage '+stage,flush=True)
if __name__=='__main__':prepare() if sys.argv[1]=='prepare' else run(sys.argv[1])
