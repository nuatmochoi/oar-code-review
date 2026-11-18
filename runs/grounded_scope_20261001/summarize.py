import json
from pathlib import Path
from collections import Counter
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
a=read(R/'audit.json');annotations={x['run_id']:x for x in read(R/'annotations.json')};rows=[]
for x in a['rows']:
 r=dict(x);ann=annotations.get(r['run_id']);r['annotation']=ann
 if r['operational_status']!='ok':outcome='blocked'
 elif ann is None:outcome='unadjudicated'
 elif r['review']['status']=='insufficient_evidence':outcome='abstained'
 elif r['label']=='safe':outcome='false_alarm' if ann['target_alarm'] else 'safe_no_issue'
 elif ann['target_alarm']:outcome='grounded_detection' if ann['mechanism_correct'] and ann['actual_trigger_claim'] and r['complete_world_fact'] else 'ungrounded_alarm'
 else:outcome='missed_no_issue'
 r['outcome']=outcome;rows.append(r)
summary={}
for split,accept in [('regression_10',lambda x:x['stage']=='regression'),('prior_F6',lambda x:x['case_id'].startswith('F')),('prior_H4',lambda x:x['case_id'].startswith('H')),('fresh_N4',lambda x:x['case_id'].startswith('N'))]:
 summary[split]={}
 for arm in ['baseline','grounded']:
  r=[x for x in rows if accept(x) and x['arm']==arm];c=Counter(x['outcome'] for x in r);c['complete_correct_pairs']=sum({x['outcome'] for x in r if x['case_id']==cid}=={'grounded_detection','safe_no_issue'} for cid in {x['case_id'] for x in r});c['final_reviews']=sum(x['operational_status']=='ok' for x in r);c['families']=len({x['case_id'] for x in r});c['ledger_factual_errors']=sum(bool(x['annotation'] and x['annotation'].get('ledger_factual_error')) for x in r);c['ledger_unsupported_assertions']=sum(bool(x['annotation'] and x['annotation'].get('ledger_unsupported_assertion')) for x in r);summary[split][arm]=dict(c)
result={'summary':summary,'rows':rows,'adjudication':'Author read every final response and grounded ledger with visible arm identity. Not independent blinded annotation.'}
(R/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False,indent=2))
