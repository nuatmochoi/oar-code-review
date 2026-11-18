"""Separate native LSP outcomes from callable-filtered retrieval outcomes."""
import json,sys,ast
from pathlib import Path
from collections import Counter
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import digest,token_count
read=lambda p:json.loads(p.read_text())
f=read(R/'freeze.json');rows=[];native=Counter();coverage=[]
for path in sorted((R/'packets').glob('*.json')):
 p=read(path);manifest=read(R/'workspace_manifests'/path.name);sources={file:(R/'workspaces'/path.stem/file).read_text() for file in manifest['source_sha256']}
 for x in p['LSP_audit']['jumps']:
  result=x['raw_result'];locs=[result] if isinstance(result,dict) else result or []
  if x.get('error'):native['request_error']+=1
  elif not locs:native['native_empty_result']+=1
  elif x['targets']:native['in_corpus_callable']+=1
  else:native['native_nonempty_filtered']+=1
  native['outside_locations']+=sum(s.startswith('outside_source_corpus:') for s in x.get('excluded',[]));native['non_callable_or_unindexed_locations']+=sum(s.startswith('not_indexed_callable_declaration:') for s in x.get('excluded',[]))
 for arm in ['AST','LSP']:
  obs=p[arm+'_observations'];a=p[arm+'_observation_audit'];assert token_count(obs)==a['reference_tokens']<=f['observation_cap'];assert len(a['visible_query_ids'])==len(obs.splitlines())
  need=[]
  if p['case_id'].startswith('W'):
   for file in ['deployment.py','bridge.py','public_api.py']:
    if file not in sources:continue
    tree=ast.parse(sources[file])
    for n in ast.walk(tree):
     if isinstance(n,ast.FunctionDef) and n.name in ['entry','arguments','factory','engine']:
      need.append({'file':file,'symbol':n.name,'start':n.lineno,'end':n.end_lineno})
    if file=='public_api.py':need.append({'file':file,'symbol':'reexport_import','start':1,'end':1})
    if file=='bridge.py' and 'class Adapter' in sources[file]:
     node=next(n for n in tree.body if isinstance(n,ast.ClassDef));need.append({'file':file,'symbol':'Adapter_header','start':node.lineno,'end':node.lineno})
   observed={}
   for eid,lines in p[arm+'_audit']['state']['source_lines'].items():
    header=p[arm].split('['+eid+'] ')[1].split('\n')[0];version,file_span=header.split(' ',1);file=file_span.split(':')[0]
    if version=='head':observed.setdefault(file,set()).update(lines)
   for n in need:n['complete_visible']=set(range(n['start'],n['end']+1)).issubset(observed.get(n['file'],set()))
   coverage.append({'case_id':p['case_id'],'world':p['world'],'arm':arm,'facts':need,'all_listed_facts_visible':all(n['complete_visible'] for n in need)})
 rows.append({'case_id':p['case_id'],'world':p['world'],'source_difference':p['AST']!=p['LSP'],'observation_difference':p['AST_observations']!=p['LSP_observations'],'AST_source_tokens':p['AST_audit']['state']['tokens'],'LSP_source_tokens':p['LSP_audit']['state']['tokens'],'AST_observation_tokens':p['AST_observation_audit']['reference_tokens'],'LSP_observation_tokens':p['LSP_observation_audit']['reference_tokens']})
result={'native_outcomes':dict(native),'worlds':len(rows),'source_changed_worlds':sum(x['source_difference'] for x in rows),'observation_changed_worlds':sum(x['observation_difference'] for x in rows),'total_changed_worlds':sum(x['source_difference'] or x['observation_difference'] for x in rows),'structural_source_coverage':coverage,'rows':rows,'note':'Query occurrences per world, not 424 distinct code positions. Nonempty native results filtered out by common in-corpus callable policy are not native resolution failures. Typed static relations do not prove runtime dispatch.'}
(R/'navigation_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ['rows','structural_source_coverage']},ensure_ascii=False,indent=2))
