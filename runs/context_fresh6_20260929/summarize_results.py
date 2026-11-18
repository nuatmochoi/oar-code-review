import json
from collections import Counter
from pathlib import Path
R=Path(__file__).resolve().parent; O=R/'experiment_v1'
read=lambda p:json.loads(p.read_text())
audit=read(R/'audit.json');labels=read(O/'private/labels.json');mapping=read(O/'private/blind_mapping.json')
annotations=read(R/'blind_annotations.json'); byrun={mapping[a['blind_id']]['run_id']:a for a in annotations}
coverage={(x['case_id'],x['world'],x['arm']):x for x in audit['world_fact_coverage']}
rows=[];counts={}
for arm in ['L','S','S_priority']:
 c=Counter()
 for cid in labels:
  pair=[]
  for world,label in labels[cid]['worlds'].items():
   rid=f'{cid}_{world}_{arm}_final';a=byrun.get(rid);fact=coverage.get((cid,world,arm),{})
   row={'run_id':rid,'case_id':cid,'arm':arm,'world':world,'label':label,'annotation':a,'world_fact_complete':fact.get('complete_world_fact',False)}
   if a is None:outcome='blocked';c['blocked']+=1
   elif a['status']=='insufficient_evidence':outcome='abstained';c['abstained']+=1
   elif label=='safe':
    outcome='false_alarm' if a['target_alarm'] else 'safe_no_issue';c[outcome]+=1
   elif a['target_alarm']:
    c['unsafe_mechanism_match']+=int(a['mechanism_correct'])
    if a['concrete_trigger_claim'] and row['world_fact_complete']:outcome='grounded_detection';c[outcome]+=1
    else:outcome='ungrounded_conditional_alarm';c[outcome]+=1
   else:outcome='missed_no_issue';c[outcome]+=1
   row['outcome']=outcome;rows.append(row);pair.append(outcome)
   if a:
    c['final_responses']+=1;c['final_world_fact_complete']+=row['world_fact_complete']
  c['complete_correct_pairs']+=set(pair)=={'grounded_detection','safe_no_issue'}
 counts[arm]=dict(c)
result={'counts':counts,'rows':rows,'grounding_note':'Author inspected supplied packets for reported target findings. Whole world-fact coverage corroborates visible triggers; F07 BM25 only conditional, no deployment.arguments supplied. F03 empty/omitted names are equivalent; stated scope supplies ID-only request semantics. One S_priority F07 prose citation typo retained separately.'}
(R/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(counts,indent=2))
for cid in labels:
 print(cid,[(r['arm'],r['label'],r['outcome'],r['world_fact_complete']) for r in rows if r['case_id']==cid])
