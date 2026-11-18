"""Score only explicit author adjudications; preserve failed/abstained denominators."""
import json
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
a=read(R/'audit.json');labels=read(R/'labels.json');ann=read(R/'annotations.json');resolved=a['resolved_outputs'];invalid=a['invalid'];scored=[]
def grade(rid):
 out=resolved.get(rid);judged=ann.get(rid)
 if out is None or rid in invalid:return {'valid':False,'correct':False,'grounded_detection':False,'false_positive':False,'abstained':False,'status':'operational_failure'}
 assert judged is not None,rid
 review=out['review'];cid,w,*_=rid.split('_');label=labels[cid]['worlds'][w];supported=judged['supported_issue_indices'];extra=judged['unsupported_issue_indices'];assert sorted(supported+extra)==list(range(len(review['issues']))),rid
 detection=bool(supported) and label=='unsafe';fp=bool(review['issues']) and label=='safe';abstained=review['status']=='insufficient_evidence'
 correct=judged['verdict_supported'] and not extra and (detection if label=='unsafe' else review['status']=='no_supported_issue')
 return dict(valid=True,correct=correct,grounded_detection=detection,false_positive=fp,abstained=abstained,status=review['status'],supported_issue_indices=supported,unsupported_issue_indices=extra)
for cid,spec in labels.items():
 for world,label in spec['worlds'].items():
  for phase in ['baseline','critic','alternate','pair']:
   rid=f'{cid}_{world}_{phase}';scored.append(dict(run_id=rid,case_id=cid,world=world,stage=spec['stage'],label=label,phase=phase,**grade(rid)))
summary={}
for stage in sorted({s['stage'] for s in labels.values()}):
 summary[stage]={}
 for phase in ['baseline','critic','alternate','pair']:
  rows=[x for x in scored if x['stage']==stage and x['phase']==phase];unsafe=[x for x in rows if x['label']=='unsafe'];safe=[x for x in rows if x['label']=='safe'];pair_ids=[cid for cid,s in labels.items() if s['stage']==stage and set(s['worlds'].values())=={'safe','unsafe'}]
  summary[stage][phase]=dict(unsafe_worlds=len(unsafe),grounded_detection=sum(x['grounded_detection'] for x in unsafe),safe_worlds=len(safe),false_positive=sum(x['false_positive'] for x in safe),abstained=sum(x['abstained'] for x in rows),correct_worlds=sum(x['correct'] for x in rows),total_worlds=len(rows),correct_pairs=sum(all(x['correct'] for x in rows if x['case_id']==cid) for cid in pair_ids),total_pairs=len(pair_ids),valid_worlds=sum(x['valid'] for x in rows))
selection={};orders=read(R/'order.json')
for stage in summary:
 packets=[(cid,w) for cid,s in labels.items() if s['stage']==stage for w in s['worlds']];oracle=0;success=0;disagreement=0
 for cid,w in packets:
  key=f'{cid}_{w}';candidates=[grade(key+'_'+phase) for phase in ['baseline','alternate']];exists=any(x['correct'] for x in candidates);oracle+=exists;success+=exists and grade(key+'_pair')['correct'];disagreement+=candidates[0]['correct']!=candidates[1]['correct']
 selection[stage]=dict(correct_candidate_available=oracle,total_worlds=len(packets),selector_correct_when_available=success,correctness_disagreements=disagreement)
swaps=[]
for c in read(R/'freeze.json')['swapped_cases']:
 for w in labels[c]['worlds']:
  key=f'{c}_{w}';orig=next((x for x in a['selections'] if x['run_id']==key+'_pair'),None);swap=next((x for x in a['selections'] if x['run_id']==key+'_pair_swapped'),None)
  swaps.append(dict(case_id=c,world=w,same_candidate=(orig['selected_candidate']==swap['selected_candidate']) if orig and swap else None,same_verdict=(orig['review']['status']==swap['review']['status']) if orig and swap else None,original=orig['selected_candidate'] if orig else None,swapped=swap['selected_candidate'] if swap else None))
result={'summary':summary,'candidate_selection':selection,'order_swap':swaps,'rows':scored,'adjudication':'Author-visible source/witness comparison; not blinded. Pair outputs map to existing candidate annotations; tie->baseline, insufficient->abstain.'}
(R/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False,indent=2))
