"""Same frozen D evidence: ordinary review vs short factual execution ledger."""
import ast,copy,json,sys,difflib,random,re,subprocess,importlib.util,concurrent.futures
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];OLD=ROOT/'runs/icml_priority_20261001';sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import Corpus,FINAL_PROMPT,FINAL_SCHEMA,digest,token_count
from ace_review.context_adaptive_run import common
from ace_review.isolated_review import call_packet
sp=importlib.util.spec_from_file_location('prior_policy',OLD/'policy.py');policy=importlib.util.module_from_spec(sp);sp.loader.exec_module(policy)
MODEL='gpt-5.6-sol'
LEDGER_PROMPT='''Before the review, produce a short factual evidence ledger, NOT hidden reasoning. For every candidate introduced regression summarize only: the actual visible deployed inputs, the branch exercised, and observable base/head results. Use unknown when a binding, value, external behavior or result is not established. Distinguish the fixed deployed inputs from the API's other possible inputs. A general counterexample does not prove a regression for the fixed deployment. Do not substitute hypothetical arguments for the visible configuration. Report only defects with an established in-scope changed observation; keep genuinely missing evidence as unresolved rather than silently assuming safety. An unchanged result on the actual inputs invalidates that candidate for this scope. Preserve a real regression when established; do not dismiss it because another input is safe. Cite supplied E-* IDs and/or DIFF. Static definition lookup is not runtime proof; visible direct entry code can establish the source binding for this controlled scope. Ledger entries must be concise factual summaries. Return checks and review JSON. No tools.'''
CHECK={'type':'object','additionalProperties':False,'required':['entrypoint','actual_inputs','exercised_branch','base_observation','head_observation','comparison','issue_indices','evidence_ids'],'properties':{'entrypoint':{'type':'string'},'actual_inputs':{'type':'array','maxItems':8,'items':{'type':'string'}},'exercised_branch':{'type':'string'},'base_observation':{'type':'string'},'head_observation':{'type':'string'},'comparison':{'type':'string','enum':['changed','unchanged','unknown']},'issue_indices':{'type':'array','items':{'type':'integer','minimum':0}},'evidence_ids':{'type':'array','items':{'type':'string'}}}}
LEDGER_SCHEMA={'type':'object','additionalProperties':False,'required':['checks','review'],'properties':{'checks':{'type':'array','minItems':1,'maxItems':5,'items':CHECK},'review':FINAL_SCHEMA}}
CONFIGS=[
 ('N01','StringTrim','result = string.strip()','result = string.lstrip()','trim_result','Remove leading and trailing whitespace when mode is Both; use the selected mode.','{"string": "  alpha", "mode": "Both"}','{"string": "  alpha  ", "mode": "Both"}'),
 ('N02','StringReplace','result = string.replace(find, replace)','result = string.replace(find, replace, 1)','replace_result','Replace all occurrences of find with replace.','{"string": "aba", "find": "b", "replace": "X"}','{"string": "baba", "find": "b", "replace": "X"}'),
 ('N03','CaseConverter','result = string.lower()','result = string.upper()','case_result','Honor the selected lowercase mode.','{"string": "123-!", "mode": "lowercase"}','{"string": "AbC", "mode": "lowercase"}'),
 ('N04','StringCompare','return a.startswith(b),','return a.endswith(b),','compare_result','Honor mode Starts With and case sensitivity.','{"string_a": "aba", "string_b": "a", "mode": "Starts With", "case_sensitive": true}','{"string_a": "abc", "string_b": "a", "mode": "Starts With", "case_sensitive": true}'),
]
def save(p,obj):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(obj,f,ensure_ascii=False,indent=2)
def prepare():
 (R/'raw').mkdir(exist_ok=False);(R/'inputs').mkdir(exist_ok=False)
 names=['baseline','grounded'];assert len(set(x.casefold() for x in names))==len(names)
 packets=[];labels={};witnesses=[];rng=random.Random(20261002)
 # Previously observed cases are regressions only, not fresh validation.
 previous=json.loads((OLD/'results.json').read_text());oldlabels={x['case_id']:{'worlds':{}} for x in previous['rows']}
 for x in previous['rows']:oldlabels[x['case_id']]['worlds'][x['world']]=x['label']
 for p in sorted((OLD/'packets').glob('*.json')):
  d=json.loads(p.read_text());item={k:d[k] for k in ['case_id','world','common','D','D_audit','source_sha256']};item['stage']='regression';save(R/'packets'/p.name,item);packets.append(p.name);labels[d['case_id']]=oldlabels[d['case_id']]
 file='comfy_extras/nodes_string.py';original=json.loads((OLD/'holdout/H03.json').read_text());src=dict(original['worlds']['a']['versions']['base']);src.pop('deployment.py');base=src[file]
 for cid,cls,before,after,entry,contract,safe,unsafe in CONFIGS:
  assert base.count(before)==1,(cid,before);head=base.replace(before,after);diff=''.join(difflib.unified_diff(base.splitlines(True),head.splitlines(True),fromfile='a/'+file,tofile='b/'+file));mapping=['safe','unsafe'];rng.shuffle(mapping);mapping=dict(zip(['a','b'],mapping));labels[cid]={'worlds':mapping,'class':cls,'mutation':{'before':before,'after':after},'contract':contract}
  for world,label in mapping.items():
   args=json.loads(safe if label=='safe' else unsafe);dep='from comfy_extras.nodes_string import '+cls+'\n\ndef arguments():\n    return '+repr(args)+'\n\ndef '+entry+'():\n    return '+cls+'().execute(**arguments())\n';versions={}
   for rev,body in [('base',base),('head',head)]:
    sources=dict(src);sources[file]=body;sources['deployment.py']=dep;versions[rev]=sources
    for repeat in range(2):
     tree=ast.parse(body);node=copy.deepcopy(next(m for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls for m in n.body if isinstance(m,ast.FunctionDef) and m.name=='execute'));node.decorator_list=[];module=ast.Module(body=[node],type_ignores=[]);ast.fix_missing_locations(module);env={};exec(compile(module,file,'exec'),env);actual=env['execute'](None,**args)[0]
     if cid=='N01':expected=args['string'].strip()
     elif cid=='N02':expected=args['string'].replace(args['find'],args['replace'])
     elif cid=='N03':expected=args['string'].lower()
     else:expected=args['string_a'].startswith(args['string_b'])
     expected_failure=label=='unsafe' and rev=='head';assert (actual!=expected)==expected_failure,(cid,label,rev,actual,expected)
     witnesses.append({'case_id':cid,'world':world,'label':label,'revision':rev,'repeat':repeat,'actual':actual,'expected':expected,'target_failure':actual!=expected,'span':{'file':file,'start':node.lineno,'end':node.end_lineno}})
   scope='Only deployment.'+entry+'() with its fixed arguments() is deployed. '+contract+' The unchanged arguments function defines the actual values, not a configurable public API for this experiment. ComfyUI UI/graph execution is outside scope.'
   spec={'versions':versions,'graphs':{v:{'nodes':{},'edges':[]} for v in ['base','head']}};case={'case_id':cid,'repository':original['repository'],'scope':scope,'diff':diff,'world_spec':spec};corpus=Corpus(spec['versions'],spec['graphs'],diff);d,a=policy.definition_packet(corpus,case);item={'case_id':cid,'world':world,'stage':'fresh_same_repository','common':common(case),'D':d.render(),'D_audit':a,'source_sha256':corpus.source_hashes};name=f'{cid}_{world}.json';save(R/'packets'/name,item);packets.append(name);save(R/'new_sources'/name,{'scope':scope,'diff':diff,'changed_file':file,'base':base,'head':head,'deployment':dep,'full_corpus_reference':str(OLD/'holdout/H03.json'),'note':'Other production files reused unchanged from prior pinned base corpus.'});print(json.dumps({'prepared':cid+'_'+world,'tokens':d.tokens}),flush=True)
 save(R/'labels.json',labels);save(R/'witnesses.json',witnesses);save(R/'schemas.json',{'baseline':FINAL_SCHEMA,'grounded':LEDGER_SCHEMA})
 files=[R/'experiment.py',R/'PROTOCOL_KO.md',R/'labels.json',R/'witnesses.json',R/'schemas.json',OLD/'policy.py',ROOT/'ace_review/context_adaptive.py',ROOT/'ace_review/isolated_review.py',ROOT/'ace_review/reviewer.py',ROOT/'ace_review/packet_review.py',ROOT/'ace_review/context_adaptive_run.py',ROOT/'ace_review/auto_context.py']+[R/'packets'/n for n in packets]+list((R/'new_sources').glob('*.json'))
 save(R/'freeze.json',{'files':{str(p.relative_to(ROOT)):digest(p.read_text()) for p in files},'model':MODEL,'effort':'low','arms':names,'planned_calls':len(packets)*2,'retries':False,'workers':4,'timeout_seconds':240,'evidence_tokens':1600,'fresh_families':4,'regression_families':10,'policy_changes_after_execution':False,'primary_evaluation':'Fresh 4 pairs separate from 10 previously observed regression pairs; author adjudication. Same D source, different prompt and output structure, one call each.'})
def run():
 f=json.loads((R/'freeze.json').read_text())
 for p,h in f['files'].items():assert digest((ROOT/p).read_text())==h,p
 assert (R/'raw').is_dir() and (R/'inputs').is_dir()
 jobs=[(json.loads(p.read_text()),arm) for p in sorted((R/'packets').glob('*.json')) for arm in ['baseline','grounded']];random.Random(20261003).shuffle(jobs);schemas=json.loads((R/'schemas.json').read_text())
 def job(j):
  p,arm=j;key=p['case_id']+'_'+p['world']+'_'+arm
  if (R/'raw'/f'{key}.json').exists():return
  prompt=(LEDGER_PROMPT+'\n' if arm=='grounded' else '')+FINAL_PROMPT+p['common']+'\n[EVIDENCE]\n'+p['D'];row={'run_id':key,'case_id':p['case_id'],'world':p['world'],'stage':p['stage'],'arm':arm,'prompt':prompt,'prompt_sha256':digest(prompt),'prompt_reference_tokens':token_count(prompt)};save(R/'inputs'/f'{key}.json',row);call_packet(row,R/'raw',MODEL,schemas[arm])
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for future in concurrent.futures.as_completed([pool.submit(job,j) for j in jobs]):future.result()
 print('Completed primary experiment',flush=True)
if __name__=='__main__':prepare() if sys.argv[1]=='prepare' else run()
