import json,sys
from pathlib import Path
R=Path(__file__).resolve().parent;read=lambda p:json.loads(p.read_text());audit=read(R/'audit_all.json');wanted=set(sys.argv[1:]);previous=set(read(R/'reviewed_ids.json')) if (R/'reviewed_ids.json').exists() else set();printed=[]
for x in audit['rows']:
 if wanted and x['case_id'] not in wanted:continue
 if x['run_id'] in previous or not x['review']:continue
 printed.append(x['run_id']);v=x['review'];print(x['run_id'],x['label'],v['status'],'errors='+','.join(x['errors']))
 if v['issues']:
  for i,q in enumerate(v['issues']):print(' ISSUE',i,q['claim'],'SCENARIO',q['failure_scenario'])
 elif v['status']=='no_supported_issue':
  for c in x['checks']:print(' HEAD',c['head_observation'],'BRANCH',c['exercised_branch'])
 else:
  # An abstention cannot earn correctness/detection/FP; print all head observations for audit.
  for c in x['checks']:print(' UNKNOWN',c['head_observation'])
(R/'last_inspected_ids.json').write_text(json.dumps(printed,indent=2)+'\n')
print('Displayed',len(printed),'reviews. Commit inspected IDs only after reading this output.')
