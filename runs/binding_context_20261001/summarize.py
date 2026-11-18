import json
from pathlib import Path
from collections import Counter
R=Path(__file__).resolve().parent;read=lambda p:json.loads(p.read_text());a=read(R/'audit.json');anns={x['run_id']:x for x in read(R/'annotations.json')};rows=[]
for x in a['rows']:
 r=dict(x);ann=anns.get(r['run_id']);r['annotation']=ann
 if r['operational_status']!='ok':outcome='blocked'
 elif not ann:outcome='unadjudicated'
 elif r['review']['status']=='insufficient_evidence':outcome='abstained'
 elif r['label']=='safe':outcome='false_alarm' if ann['target_alarm'] else 'safe_no_issue'
 elif ann['target_alarm']:outcome='grounded_detection' if ann['mechanism_correct'] and ann['actual_trigger_claim'] and r['complete_world_fact'] else 'ungrounded_alarm'
 else:outcome='missed_no_issue'
 r['outcome']=outcome;rows.append(r)
summary={}
for split,predicate in [('regression14',lambda x:x['case_id']!='P08'),('original_F08',lambda x:x['case_id']=='F08'),('controlled_P08',lambda x:x['case_id']=='P08')]:
 summary[split]={}
 for arm in ['definition','binding']:
  rs=[r for r in rows if r['arm']==arm and predicate(r)];c=Counter(r['outcome'] for r in rs);families={r['case_id'] for r in rs};c['families']=len(families);c['final_reviews']=sum(r['operational_status']=='ok' for r in rs);c['complete_correct_pairs']=sum({r['outcome'] for r in rs if r['case_id']==cid}=={'safe_no_issue','grounded_detection'} for cid in families);summary[split][arm]=dict(c)
(R/'results.json').write_text(json.dumps({'summary':summary,'rows':rows,'note':'Author adjudication, observed regression only; identical prompts share a response frozen before reviewer calls.'},ensure_ascii=False,indent=2));print(json.dumps(summary,indent=2))
