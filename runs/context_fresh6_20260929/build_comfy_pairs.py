import ast,difflib,json,sys
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT))
from ace_review.paired_benchmark import validate_pair
meta={s['githubPrUrl'].split('/')[-1]:s for s in json.loads((R/'candidate_metadata.json').read_text())}
manifest=json.loads((R/'manifest.json').read_text());(R/'manifest_before_repo_overlap_audit.json').write_text(json.dumps(manifest,indent=2))
manifest=[m for m in manifest if m['case_id'] not in ('F05','F06')]
for cid,pr,target,symbol in [('F07','7952','comfy_extras/nodes_string.py','execute'),('F08','9560','middleware/cache_middleware.py','cache_control')]:
 snap=R/'snapshots'/pr;sources={};excluded=[]
 for p in sorted(snap.rglob('*.py')):
  rel=p.relative_to(snap)
  if any(x.startswith('test') or x in ('examples','script_examples','__pycache__') for x in rel.parts):continue
  s=p.read_text()
  try:ast.parse(s)
  except SyntaxError as e:excluded.append({'file':str(rel),'reason':str(e)});continue
  sources[str(rel)]=s if s.endswith('\n') else s+'\n'
 head=sources[target]
 if cid=='F07':
  before='results.append(match.group(group_index) or "")';after='results.append(match.group(group_index))';assert head.count(after)==1;base=head.replace(after,before)
  scope='Only deployment.extract is used. Extracting all regex groups must return a string; non-participating optional groups are represented by empty strings. The witness uses Python re and the original RegexExtract method; ComfyUI graph/UI execution is out of scope.'
  mechanism='RegexExtract appends None for a nonparticipating optional group to results, and the subsequent string join raises TypeError in the deployed mixed-match input.'
  deployments={w:"from comfy_extras.nodes_string import RegexExtract\n\ndef arguments():\n    return {'string': "+repr(s)+", 'regex_pattern': '(a)?b', 'mode': 'All Groups', 'case_insensitive': False, 'multiline': False, 'dotall': False, 'group_index': 1}\n\ndef extract():\n    return RegexExtract().execute(**arguments())\n" for w,s in [('safe','ab ab'),('unsafe','ab b')]}
  changes=['Base is a constructed local repair normalizing an unmatched optional group to empty string; head is the pinned PR-head function.','Deployed regex is identical; string input differs in whether every optional group participates.']
 else:
  after='response.headers.setdefault("Cache-Control", f"public, max-age={ONE_HOUR}")';before='response.headers.setdefault("Cache-Control", "no-cache")';assert head.count(after)==1;base=head.replace(after,before)
  scope='Only deployment.poll_image is used for an image that is not generated yet. For this deployment, a not-found polling response must require revalidation so that a subsequently generated image is visible immediately; a positive max-age on the missing-image response violates this contract. Existing producer Cache-Control must be honored. Witness scope is the original middleware and response headers, not a browser cache integration.'
  mechanism='The 404 branch supplies public max-age=3600 when the deployed response producer has no Cache-Control. A missing generated image can remain cached contrary to the polling contract; a preexisting no-cache header is preserved by setdefault.'
  deployments={w:"from aiohttp import web\nfrom middleware.cache_middleware import cache_control\n\nasync def missing_image(request):\n    return web.Response(status=404, headers="+v+")\n\nasync def poll_image(request):\n    return await cache_control(request, missing_image)\n" for w,v in [('safe',"{'Cache-Control': 'no-cache'}"),('unsafe','{}')]}
  changes=['Base is a constructed local repair using no-cache in the 404 branch; head is pinned PR-head code.','Both worlds return 404; only the unchanged producer header differs. This is a deployment-contract regression, not a claim that all HTTP negative caching is invalid.']
 root=R/'pairs'/cid;root.mkdir(exist_ok=False)
 diff=''.join(difflib.unified_diff(base.splitlines(True),head.splitlines(True),fromfile='a/'+target,tofile='b/'+target));(root/'target.diff').write_text(diff)
 for w in ('safe','unsafe'):
  for rev in ('base','head'):
   content=dict(sources);content[target]=base if rev=='base' else head;content['deployment.py']=deployments[w]
   for name,s in content.items():
    ast.parse(s);p=root/'snapshots'/f'{w}_{rev}'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
 n=[n for n in ast.walk(ast.parse(head)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==symbol][-1]
 nodes=[{'id':'target','role':'changed_consumer','file':target,'start_line':n.lineno,'end_line':n.end_lineno,'symbols':[symbol]}, {'id':'companion','role':'world_condition','file':'deployment.py','start_line':1,'end_line':len(deployments['unsafe'].splitlines()),'symbols':[]}]
 pair={'schema_version':1,'family_id':cid,'repository':'comfyanonymous/ComfyUI','language':'Python','origin':'derived','split':'validation','stratum':'static_decidable','target':{'changed_files':[target],'actionable_span':{'file':target,'start_line':n.lineno,'end_line':n.end_lineno}},'worlds':{w:{'base_snapshot':f'snapshots/{w}_base','head_snapshot':f'snapshots/{w}_head','diff':'target.diff','expected':{'base':'pass','head':'target_failure' if w=='unsafe' else 'pass'}} for w in ('safe','unsafe')},'companion_paths':['deployment.py'],'gold':{'mechanism':mechanism,'required_evidence_nodes':['target','companion'],'oracle_nodes':nodes,'oracle_edges':[{'source':'companion','target':'target','type':'semantic_dependency'}],'oracle_paths':[{'nodes':['companion','target'],'edge_types':['semantic_dependency']}],'oracle_note':'Author semantic dependency, not runtime call proof'},'witness':{'argv':['{python}','-B','{pair_root}/../../witness.py',cid,'{snapshot}'],'timeout_seconds':15,'exit_codes':{'pass':0,'target_failure':10}},'scope':scope,'source':{'pr_url':meta[pr]['githubPrUrl'],'upstream_head_commit':meta[pr]['target_commit'],'aacr_unavailable_head_commit':meta[pr]['aacr_target_commit'],'upstream_base_commit':meta[pr]['source_commit'],'derivation':changes,'snapshot_note':'AACR recorded head unavailable (404); exact current PR-head SHA obtained via GitHub API and pinned BEFORE any reviewer output.'},'corpus':{'python_files':len(sources)+1,'excluded_parser_files':excluded,'production_roots':['all repository Python except test*/examples/script_examples']}}
 (root/'pair.json').write_text(json.dumps(pair,ensure_ascii=False,indent=2));audit=validate_pair(pair,root);assert audit['eligible'],audit['errors'];(root/'static_audit.json').write_text(json.dumps(audit,indent=2))
 manifest.append({'case_id':cid,'repository':pair['repository'],'pr':pr,'pair':str(root.relative_to(R)/'pair.json'),'scope':scope,'corpus_files':len(sources)+1,'excluded_parser_files':excluded,'static_gate':True});print(json.dumps(manifest[-1]),flush=True)
(R/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
