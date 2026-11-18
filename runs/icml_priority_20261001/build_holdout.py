"""Four function-disjoint near-holdout mutations from pinned production snapshots."""
import ast,copy,difflib,json,sys,random,hashlib,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import digest
from ace_review.paired_benchmark import validate_pair
R=Path(__file__).resolve().parent;OLD=ROOT/'runs/context_fresh6_20260929'
configs=[
 ('H01','F02','browser_use/llm/google/chat.py','ChatGoogle','_get_client_params','if v is not None','if v','client_options','client_options() must preserve every explicitly configured non-None client option, including a boolean False. External provider construction is not executed.',"{'api_key': 'local', 'vertexai': True, 'credentials': None, 'project': None, 'location': None, 'http_options': None}","{'api_key': 'local', 'vertexai': False, 'credentials': None, 'project': None, 'location': None, 'http_options': None}",'truthiness filter drops explicitly configured False'),
 ('H02','F02','browser_use/llm/google/chat.py','ChatGoogle','_get_usage','+ (response.usage_metadata.thoughts_token_count or 0)','+ 0','usage_report','usage_report() must count completion tokens as generated candidate tokens plus reasoning/thought tokens. The metadata boundary is supplied; no provider API is invoked.',"{'candidate_tokens': 5, 'thought_tokens': 0}","{'candidate_tokens': 5, 'thought_tokens': 7}",'reasoning token contribution discarded'),
 ('H03','F07','comfy_extras/nodes_string.py','StringContains','execute','contains = substring.lower() in string.lower()','contains = substring in string','contains_result','contains_result() must implement the configured case_sensitive flag. Only this deployed entry is used; ComfyUI UI/graph execution is outside scope.',"{'string': 'Alpha', 'substring': 'alpha', 'case_sensitive': True}","{'string': 'Alpha', 'substring': 'alpha', 'case_sensitive': False}",'case-insensitive branch loses normalization'),
 ('H04','F07','comfy_extras/nodes_string.py','StringSubstring','execute','return string[start:end],','return string[start:end + 1],','substring_result','substring_result() must honor Python slicing with an exclusive end index. Only this deployed entry is used; ComfyUI UI/graph execution is outside scope.',"{'string': 'abcd', 'start': 1, 'end': 10}","{'string': 'abcd', 'start': 1, 'end': 2}",'exclusive end becomes inclusive when in range'),
]
manifest=[];records=[];labels={};rng=random.Random(20261001)
for cid,origin,file,cls,method,before,after,entry,scope,safe,unsafe,mechanism in configs:
 original=json.loads((OLD/'experiment_v1/cases'/f'{origin}.json').read_text());src=dict(next(iter(original['worlds'].values()))['versions']['head']);src.pop('deployment.py',None)
 # Restores exact pinned upstream source rather than borrowing the earlier pair mutation.
 pr='3172' if origin=='F02' else '7952';src[file]=(OLD/'snapshots'/pr/file).read_text();src[file]+= '' if src[file].endswith('\n') else '\n'
 base=src[file];assert base.count(before)==1;head=base.replace(before,after)
 tree=ast.parse(head);node=next(m for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls for m in n.body if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef)) and m.name==method)
 root=R/'holdout'/cid;root.mkdir(parents=True,exist_ok=False)
 diff=''.join(difflib.unified_diff(base.splitlines(True),head.splitlines(True),fromfile='a/'+file,tofile='b/'+file));(root/'target.diff').write_text(diff)
 worlds={};sources_by_world={}
 for world,arg in [('safe',safe),('unsafe',unsafe)]:
  # Same entry/target across worlds; only arguments() changes.
  if cid=='H01':body=f"    return {cls}(**arguments()).{method}()\n"
  elif cid=='H02':body=f"    args = arguments()\n    response = metadata_response(args)\n    return {cls}().{method}(response)\n"
  else:body=f"    return {cls}().{method}(**arguments())\n"
  dep='from '+file.removesuffix('.py').replace('/','.')+' import '+cls+'\n\ndef arguments():\n    return '+arg+'\n\n'
  if cid=='H02':dep+='from types import SimpleNamespace\n\ndef metadata_response(args):\n    metadata = SimpleNamespace(prompt_tokens_details=None, prompt_token_count=0, candidates_token_count=args["candidate_tokens"], thoughts_token_count=args["thought_tokens"], total_token_count=args["candidate_tokens"] + args["thought_tokens"], cached_content_token_count=None)\n    return SimpleNamespace(usage_metadata=metadata)\n\n'
  dep+='def '+entry+'():\n'+body
  versions={}
  for rev in ['base','head']:
   sources=dict(src);sources[file]=base if rev=='base' else head;sources['deployment.py']=dep
   for p,s in sources.items():
    out=root/'snapshots'/f'{world}_{rev}'/p;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(s)
   versions[rev]=sources
  worlds[world]={'base_snapshot':f'snapshots/{world}_base','head_snapshot':f'snapshots/{world}_head','diff':'target.diff','expected':{'base':'pass','head':'target_failure' if world=='unsafe' else 'pass'}};sources_by_world[world]=versions
 gold={'mechanism':mechanism,'required_evidence_nodes':['target','arguments'],'oracle_nodes':[{'id':'target','role':'changed_consumer','file':file,'start_line':node.lineno,'end_line':node.end_lineno,'symbols':[cls+'.'+method]},{'id':'arguments','role':'world_condition','file':'deployment.py','start_line':3,'end_line':4,'symbols':['arguments']}],'oracle_edges':[{'source':'arguments','target':'target','type':'semantic_dependency'}],'oracle_paths':[{'nodes':['arguments','target'],'edge_types':['semantic_dependency']}],'oracle_note':'Author scoped contract; not natural PR'}
 pair={'schema_version':1,'family_id':cid,'repository':original['repository'],'language':'Python','origin':'derived','split':'test','stratum':'static_decidable','target':{'changed_files':[file],'actionable_span':{'file':file,'start_line':node.lineno,'end_line':node.end_lineno}},'worlds':worlds,'companion_paths':['deployment.py'],'gold':gold,'witness':{'argv':['{python}','-B','{pair_root}/../../witness_holdout.py',cid,'{snapshot}'],'timeout_seconds':15,'exit_codes':{'pass':0,'target_failure':10}},'scope':'Only deployment.'+scope,'source':{'prior_corpus':origin,'pinned_snapshot_receipt':str(OLD/f'snapshot_{pr}.json'),'upstream_target_sha256':digest(base),'mutation':{'before':before,'after':after},'note':'NEW injected target change in a previously unreviewed function; same repositories and files as development, near-holdout only'},'corpus_files':len(src)+1}
 (root/'pair.json').write_text(json.dumps(pair,ensure_ascii=False,indent=2));check=validate_pair(pair,root);assert check['eligible'],check['errors'];(root/'static_audit.json').write_text(json.dumps(check,indent=2))
 mapping=['safe','unsafe'];rng.shuffle(mapping);mapping=dict(zip(['a','b'],mapping));case={'case_id':cid,'repository':original['repository'],'scope':pair['scope'],'diff':diff,'origin':'function_disjoint_injected_near_holdout','worlds':{w:{'versions':sources_by_world[label],'graphs':{v:{'nodes':{},'edges':[]} for v in ['base','head']}} for w,label in mapping.items()}}
 (R/'holdout'/f'{cid}.json').write_text(json.dumps(case,ensure_ascii=False));labels[cid]={'worlds':mapping,'gold':gold,'source':pair['source']};manifest.append({'case_id':cid,'file':f'holdout/{cid}.json','pair':f'holdout/{cid}/pair.json','target_symbol':cls+'.'+method})
(R/'holdout/manifest.json').write_text(json.dumps(manifest,indent=2));(R/'holdout/labels.json').write_text(json.dumps(labels,indent=2));print('Built',len(manifest),'new function-disjoint paired mutations')
