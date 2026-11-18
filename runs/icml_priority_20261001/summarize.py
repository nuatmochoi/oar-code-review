"""Author adjudication after manual reading of every final response and packet."""
import json
from pathlib import Path
from collections import Counter
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
audit=read(R/'audit.json');annotations=read(R/'annotations.json');annotations={x['run_id']:x for x in annotations};rows=[];summary={}
for row in audit['coverage']:
 x=dict(row);a=annotations.get(x['run_id']);x['annotation']=a
 if x['operational_status']!='ok':outcome='blocked'
 elif not a:outcome='unadjudicated'
 elif a['status']=='insufficient_evidence':outcome='abstained'
 elif x['label']=='safe':outcome='false_alarm' if a['target_alarm'] else 'safe_no_issue'
 elif a['target_alarm']:
  outcome='grounded_detection' if a['mechanism_correct'] and a['actual_trigger_claim'] and x['complete_world_fact'] else 'ungrounded_alarm'
 else:outcome='missed_no_issue'
 x['outcome']=outcome;rows.append(x)
for stage in ['development','near_holdout']:
 summary[stage]={}
 for arm in ['L','D','SELF2','CRITIC']:
  a=[x for x in rows if x['stage']==stage and x['arm']==arm];c=Counter(x['outcome'] for x in a)
  c['complete_correct_pairs']=sum({x['outcome'] for x in a if x['case_id']==cid}=={'grounded_detection','safe_no_issue'} for cid in {x['case_id'] for x in a});c['final_responses']=sum(x['operational_status']=='ok' for x in a);c['world_fact_complete']=sum(x['complete_world_fact'] for x in a);summary[stage][arm]=dict(c)
result={'summary':summary,'rows':rows,'author_annotation':'Primary agent read responses and supplied packets; arm identities were visible. Not independent blinded adjudication.','infrastructure_incident':read(R/'infrastructure_failed/incident.json'),'execution_amendment':read(R/'freeze.json')['execution_amendment']}
(R/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False,indent=2))
