import ast,hashlib,json
from pathlib import Path
R=Path(__file__).resolve().parent
manifest=json.loads((R/'manifest.json').read_text());rows=[];targets={}
facts={'F01':('deployment.py','config'),'F02':('deployment.py','model'),'F03':('api/db/services/document_service.py','get_doc_ids_by_doc_names'),'F04':('deployment.py','update_payload'),'F07':('deployment.py','arguments'),'F08':('deployment.py','missing_image')}
for entry in manifest:
 cid=entry['case_id'];pr=entry['pr'];pairroot=R/'pairs'/cid;pair=json.loads((pairroot/'pair.json').read_text());source=R/'snapshots'/pr
 allowed={*pair['target']['changed_files'],*pair['companion_paths'],'deployment.py'}
 targets[cid]={}
 for world in ('safe','unsafe'):
  for rev in ('base','head'):
   root=pairroot/pair['worlds'][world][rev+'_snapshot'];different=[];copied=0
   for f in root.rglob('*.py'):
    path=f.relative_to(root).as_posix();original=source/path
    if original.exists():
     text=original.read_text();text=text if text.endswith('\n') else text+'\n'
     if f.read_text()==text:copied+=1;continue
    assert path in allowed,(cid,path)
    different.append(path)
   rows.append({'case_id':cid,'world':world,'revision':rev,'unchanged_upstream_python_files':copied,'documented_derived_files':sorted(different)})
  path,name=facts[cid];text=(pairroot/pair['worlds'][world]['head_snapshot']/path).read_text()
  nodes=[n for n in ast.walk(ast.parse(text)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name]
  assert len(nodes)==1
  n=nodes[0];targets[cid][world]={'file':path,'symbol':name,'start':n.lineno,'end':n.end_lineno,'role':'essential_world_distinguishing_fact','source_sha256':hashlib.sha256(text.encode()).hexdigest()}
report={'valid':True,'records':rows,'normalization':'Only ensure a trailing newline on exported text; all other departures from upstream limited to declared target/companion/deployment files.','scope':'Entire declared production Python pool, not all repository assets or installed dependencies.'}
(R/'provenance_audit.json').write_text(json.dumps(report,indent=2))
(R/'evaluation_targets.json').write_text(json.dumps(targets,indent=2))
(R/'adjudication_protocol.json').write_text(json.dumps({'written_before_model_calls':True,'case_ids':[x['case_id'] for x in manifest],'rules':['Exact target mechanism, triggering conditions, observable consequence required; a mixed or false causal claim is not strict detection.','Count safe target-path alarms independently; abstention is not a normal/safe conclusion.','Source-grounded detection additionally requires the world-distinguishing fact in the supplied DIFF/evidence; do not credit guessed deployment values.','No complete-case success rate excluding operational failures. Missing/blocked finals remain failures to produce a verdict.','Report six-family descriptive paired outcomes; no p-values, generalization or official benchmark claim.','Author annotation with arm names removed, not an independent blind human study.','F08 assesses a declared missing-generated-image polling contract, not generic HTTP cache validity.','Full-function world-fact coverage is diagnostic; inspecting a sufficient exact statement can support a finding without needing an entire unrelated file.'], 'frozen_gold_location':'experiment_v1/private/labels.json','coverage_targets':'evaluation_targets.json'},indent=2))
print(json.dumps({'source_audit_valid':True,'cells':len(rows),'world_fact_targets':sum(len(x) for x in targets.values())}))
