"""Validate frozen preparation, unchanged workspaces, preservation and review completeness."""
import hashlib,json,re
from pathlib import Path
import experiment as ex
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
f=read(R/'freeze.json');audit=read(R/'audit_all.json');reviewed=set(read(R/'reviewed_ids.json'))
workspace_ok=True;protected_ok=True;extra_errors=[];source_summary={a:{'new_queries':0,'unresolved_queries':0,'protected_units':0,'evicted_units':0,'additions':0} for a in ['B15','F']}
packets={p.stem:read(p) for p in (R/'packets').glob('*.json')}
for p in packets.values():
 m=read(ex.ROOT/p['workspace_manifest_reference'])
 workspace_ok &= all(ex.digest((ex.ROOT/p['workspace_reference']/file).read_text())==h for file,h in m['source_sha256'].items())
 original=p['B_audit']['state']['source_lines']
 final=p['F_audit']['state']['source_lines']
 protected_ok &= all(k in final and original[k]==final[k] for k in p['F_audit']['protected_ids'])
 for a in ['B15','F']:
  z=p[a+'_audit'];s=source_summary[a];s['new_queries']+=len(z['extra_navigation']);s['unresolved_queries']+=sum(not x['units'] for x in z['extra_navigation']);s['protected_units']+=len(z['protected_ids']);s['evicted_units']+=len(z['evicted_ids']);s['additions']+=len(z.get('additions',[]))
  extra_errors += [x['error'] for x in z['extra_navigation'] if x['error']]
  assert len(z['jumps'])+len(z['extra_navigation'])<=15
locations_ok=True;locations_count=0
for x in audit['rows']:
 if not x['review']:continue
 p=packets[x['case_id']+'_'+x['world']]
 for issue in x['review']['issues']:
  locations_count+=1
  file=issue['file'];line=issue['line'];path=ex.ROOT/p['workspace_reference']/file
  locations_ok &= path.is_file() and 1<=line<=len(path.read_text().splitlines())
v={'frozen_files_unchanged':audit['frozen_files_unchanged'],'source_caps_valid':audit['caps_valid'],'logical_rows':len(audit['rows']),'physical_raw':len([p for p in (R/'raw').glob('*.json') if not p.name.endswith('.trace.json')]),'expected_physical':f['physical_calls'],'reviewed_ids':len(reviewed),'all_logical_rows_reviewed':all(x['run_id'] in reviewed for x in audit['rows']),'missing_reviews':audit['missing'],'invalid_logical_reviews':audit['invalid'],'all_transport_ok':all(x['status'] in ['ok','invalid'] for x in audit['rows']),'all_tools_zero':all(x['tools']==0 for x in audit['operational'].values()),'workspace_source_hashes_valid':workspace_ok,'protected_original_source_lines_unchanged':protected_ok,'native_extra_errors':extra_errors,'source_summary':source_summary,'reported_file_line_exists':bool(locations_ok),'reported_issue_locations_checked':locations_count,'localization_accuracy_measured':False}
(R/'validation.json').write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n');print(json.dumps(v,ensure_ascii=False,indent=2))
