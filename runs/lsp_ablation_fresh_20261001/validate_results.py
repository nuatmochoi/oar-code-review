"""Final completeness/provenance checks; grading remains the frozen score protocol."""
import hashlib,json
from pathlib import Path
import experiment as ex
R=Path(__file__).resolve().parent;read=lambda p:json.loads(p.read_text());f=read(R/'freeze.json');a=read(R/'audit_all.json');inspected=set(read(R/'reviewed_ids.json'));ps={p.stem:read(p) for p in (R/'packets').glob('*.json')}
workspace=True;protected=True;locations=True;n=0
for key,p in ps.items():
 m=read(R/'workspace_manifests'/f'{key}.json');workspace &= all(ex.digest((ex.ROOT/p['workspace_reference']/file).read_text())==h for file,h in m['source_sha256'].items())
 pa=p['P_audit'];core=set(pa['protected_ids'])|{x['unit_id'] for x in pa['additions']};protected &= all(p['R_audit']['state']['source_lines'].get(k)==pa['state']['source_lines'][k] for k in core)
for x in a['rows']:
 if not x['review']:continue
 p=ps[x['case_id']+'_'+x['world']]
 for issue in x['review']['issues']:
  n+=1;file=ex.ROOT/p['workspace_reference']/issue['file'];locations &= file.is_file() and 1<=issue['line']<=len(file.read_text().splitlines())
prep=read(R/'preparation_seal.json');v={'frozen_files_unchanged':a['frozen_files_unchanged'],'preparation_seal_unchanged':all(hashlib.sha256((R/p).read_bytes()).hexdigest()==h for p,h in prep['files'].items()),'caps_valid':a['caps_valid'],'workspace_hashes_valid':workspace,'P_core_protected_in_R':protected,'logical_rows':len(a['rows']),'physical_raw':len([p for p in (R/'raw').glob('*.json') if not p.name.endswith('.trace.json')]),'expected_physical':f['physical_calls'],'reviewed_ids':len(inspected),'all_rows_reviewed':all(x['run_id'] in inspected for x in a['rows']),'missing':a['missing'],'invalid_logical_reviews':a['invalid'],'all_transport_schema_ok':all(x['status'] in ['ok','invalid'] for x in a['rows']),'all_tools_zero':all(x['tools']==0 for x in a['operational'].values()),'issue_file_lines_exist':bool(locations),'logical_issue_locations_checked':n,'localization_accuracy_measured':False};(R/'validation_results.json').write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n');print(json.dumps(v,ensure_ascii=False,indent=2))
