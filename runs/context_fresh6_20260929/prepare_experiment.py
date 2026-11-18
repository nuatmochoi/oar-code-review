"""Validate, index and freeze the complete six-pair input before model calls."""
import ast,hashlib,json,os,random,sys,tempfile

from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from index_acceleration import install
install()
from ace_review.context_adaptive import digest,QUESTION_SCHEMA,FINAL_SCHEMA
from ace_review.context_adaptive_run import portable_schema
from ace_review.paired_benchmark import validate_pair
from ace_review.python_ast_graph import build_python_ast_graph
from ace_review.delta_scip import changed_ranges
R=Path(__file__).resolve().parent
OUT=R/'experiment_v1';OUT.mkdir(exist_ok=False)
(OUT/'cases').mkdir();(OUT/'private').mkdir();(OUT/'implementation').mkdir()
assert json.loads((R/'index_cache_equivalence.json').read_text())['valid']
assert not json.loads((R/'overlap_audit.json').read_text())['selected_repositories_prior_evaluation_matches']
witness=json.loads((R/'witness_results.json').read_text());assert witness['valid'] and len(witness['records'])==48
assert witness['witness_sha256']==digest((R/'witness.py').read_text())
manifest=json.loads((R/'manifest.json').read_text());private={};cases=[];rng=random.Random(20260929)
for row in manifest:
 cid=row['case_id'];pairroot=R/'pairs'/cid;pair=json.loads((pairroot/'pair.json').read_text())
 static=validate_pair(pair,pairroot);assert static['eligible'],static['errors']
 (OUT/'private'/f'{cid}_static.json').write_text(json.dumps(static))
 worlds=['safe','unsafe'];rng.shuffle(worlds);mapping=dict(zip(['a','b'],worlds))
 case={'case_id':cid,'repository':pair['repository'],'scope':pair['scope'],'diff':(pairroot/'target.diff').read_text(),'origin':'new_real_source_derived_component_pair','worlds':{}}
 for opaque,world in mapping.items():
  versions={};graphs={}
  for revision in ('base','head'):
   snapshot=pairroot/pair['worlds'][world][revision+'_snapshot']
   sources={p.relative_to(snapshot).as_posix():p.read_text() for p in sorted(snapshot.rglob('*.py'))}
   ranges=changed_ranges(case['diff'])[revision]
   # Normalize the package root for Langflow imports; map graph paths back to the original repo paths.
   prefix='src/backend/base/' if pair['repository']=='langflow-ai/langflow' else ''
   if prefix:
    with tempfile.TemporaryDirectory(prefix='fresh6-ast-') as temp:
     aliases={}
     for path in sources:
      short=path[len(prefix):] if path.startswith(prefix) else path
      aliases[short]=path;dest=Path(temp)/short;dest.parent.mkdir(parents=True,exist_ok=True);os.link(snapshot/path,dest)
     remapped={p[len(prefix):] if p.startswith(prefix) else p:v for p,v in ranges.items()}
     graph=build_python_ast_graph(temp,revision=revision,changed_files=remapped,changed_lines=remapped)
   else:
    aliases={p:p for p in sources};graph=build_python_ast_graph(snapshot,revision=revision,changed_files=ranges,changed_lines=ranges)
   graphs[revision]={'nodes':{n.id:{'file':aliases[n.file],'start':n.start_line,'end':n.end_line} for n in graph.nodes if n.start_line is not None and n.end_line is not None},'edges':[{'source':e.source,'target':e.target,'kind':e.type} for e in graph.edges if e.resolved and e.confidence>0],'audit':dict(graph.metadata,source_package_prefix=prefix)}
   versions[revision]=sources
   print(json.dumps({'indexed':cid,'world':opaque,'revision':revision,'files':len(sources),'nodes':len(graphs[revision]['nodes']),'edges':len(graphs[revision]['edges'])}),flush=True)
  case['worlds'][opaque]={'versions':versions,'graphs':graphs}
 (OUT/'cases'/f'{cid}.json').write_text(json.dumps(case,ensure_ascii=False))
 cases.append({'case_id':cid,'repository':pair['repository'],'file':f'cases/{cid}.json'})
 private[cid]={'worlds':mapping,'gold':pair['gold'],'source':pair['source'],'pair_sha256':digest(pair)}
(OUT/'cases.json').write_text(json.dumps(cases,indent=2))
(OUT/'private/labels.json').write_text(json.dumps(private,ensure_ascii=False,indent=2))
for name,data in [('controller.schema.json',json.loads((ROOT/'paper/plans/context_adaptive_review_v1/controller.schema.json').read_text())),('question.schema.json',QUESTION_SCHEMA),('final.schema.json',FINAL_SCHEMA)]:
 (OUT/name).write_text(json.dumps(data,indent=2))
(OUT/'controller.provider.schema.json').write_text(json.dumps(portable_schema(json.loads((OUT/'controller.schema.json').read_text())),indent=2))
# Freeze all reusable policy/transport/index modules, not only the directly imported entrypoints.
for p in (ROOT/'ace_review').glob('*.py'):(OUT/'implementation'/p.name).write_text(p.read_text())
for name in ('build_pairs.py','witness.py','run_witnesses.py','prepare_experiment.py','run_experiment.py','PLAN_KO.md','build_comfy_pairs.py','check_cached_index.py','overlap_audit.json','index_cache_equivalence.json','index_acceleration.py'):
 (OUT/'implementation'/name).write_text((R/name).read_text())
files={str(p.relative_to(OUT)):digest(p.read_text()) for p in OUT.rglob('*') if p.is_file()}
source_hashes={str(p.relative_to(ROOT)):digest(p.read_text()) for p in (ROOT/'ace_review').glob('*.py')}
freeze={'files':files,'workspace_policy_hashes':source_hashes,'model':'gpt-5.6-sol','reasoning':'low','planned_calls':78,'shared_prefix_calls':12,'arms':['L','S','S_priority'],'witness_sha256':digest((R/'witness_results.json').read_text()),'scope':'exploratory_new_derived_component_pairs; not independent annotation or natural-PR benchmark'}
(OUT/'freeze.json').write_text(json.dumps(freeze,indent=2))
print(json.dumps({'prepared':6,'planned_calls':78,'freeze_sha256':digest(freeze)}))
