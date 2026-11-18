"""Score only explicit author adjudications; preserve failed/abstained denominators."""
import json
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
a=read(R/'audit.json');labels=read(R/'labels.json');ann=read(R/'annotations.json');resolved={x['run_id']:{'review':x['review'],'checks':x['checks']} for x in a['rows'] if x['review'] is not None};invalid=set(a['invalid']);scored=[]
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
  for phase in ['ast','lsp']:
   rid=f'{cid}_{world}_{phase}';scored.append(dict(run_id=rid,case_id=cid,world=world,stage=next(x['stage'] for x in a['rows'] if x['case_id']==cid),label=label,phase=phase,**grade(rid)))
summary={}
for stage in sorted({x['stage'] for x in a['rows']}):
 summary[stage]={}
 for phase in ['ast','lsp']:
  rows=[x for x in scored if x['stage']==stage and x['phase']==phase];unsafe=[x for x in rows if x['label']=='unsafe'];safe=[x for x in rows if x['label']=='safe'];pair_ids=sorted({x['case_id'] for x in rows})
  summary[stage][phase]=dict(unsafe_worlds=len(unsafe),grounded_detection=sum(x['grounded_detection'] for x in unsafe),safe_worlds=len(safe),false_positive=sum(x['false_positive'] for x in safe),abstained=sum(x['abstained'] for x in rows),correct_worlds=sum(x['correct'] for x in rows),total_worlds=len(rows),correct_pairs=sum(all(x['correct'] for x in rows if x['case_id']==cid) for cid in pair_ids),total_pairs=len(pair_ids),valid_worlds=sum(x['valid'] for x in rows))

result={'summary':summary,'rows':scored,'adjudication':'Author-visible source/witness comparison; identical prompts share the same physical response. No blinded or independent natural-PR evaluation.'}
(R/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False,indent=2))
