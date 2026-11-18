"""Aggregate explicit per-review author adjudication, including failures/abstentions."""
import json,sys
from pathlib import Path
R=Path(__file__).resolve().parent;stage=sys.argv[1] if len(sys.argv)>1 else 'all';a=json.loads((R/f'audit_{stage}.json').read_text());annotations=json.loads((R/f'annotations_{stage}.json').read_text());rows=[]
for x in a['rows']:
 judged=annotations[x['run_id']];valid=x['status']=='ok' and not x['errors'];review=x['review'];supported=judged['supported_issue_indices'];unsupported=judged['unsupported_issue_indices']
 if review:assert sorted(supported+unsupported)==list(range(len(review['issues'])))
 detected=valid and x['label']=='unsafe' and bool(supported);false_positive=valid and x['label']=='safe' and bool(review['issues']);abstained=valid and review['status']=='insufficient_evidence'
 correct=valid and judged['verdict_supported'] and not unsupported and (detected if x['label']=='unsafe' else review['status']=='no_supported_issue')
 rows.append({k:x[k] for k in ['run_id','case_id','world','arm','repeat','label','repository']}|{'valid':valid,'grounded_detection':detected,'false_positive':false_positive,'abstained':abstained,'correct':correct,'unsupported_issues':len(unsupported)})
summary={};by_repeat={}
for arm in sorted({x['arm'] for x in rows}):
 rr=[x for x in rows if x['arm']==arm];summary[arm]={'grounded_detection':sum(x['grounded_detection'] for x in rr),'unsafe_reviews':sum(x['label']=='unsafe' for x in rr),'false_positive':sum(x['false_positive'] for x in rr),'safe_reviews':sum(x['label']=='safe' for x in rr),'abstentions':sum(x['abstained'] for x in rr),'unsupported_issues':sum(x['unsupported_issues'] for x in rr),'correct_worlds':sum(x['correct'] for x in rr),'total_worlds':len(rr),'valid_reviews':sum(x['valid'] for x in rr),'correct_pair_repeats':sum(all(x['correct'] for x in rr if x['case_id']==cid and x['repeat']==rep) for cid in sorted({x['case_id'] for x in rr}) for rep in [1,2,3]),'total_pair_repeats':33}
 by_repeat[arm]={}
 for rep in [1,2,3]:
  ss=[x for x in rr if x['repeat']==rep];by_repeat[arm][str(rep)]={'detections':sum(x['grounded_detection'] for x in ss),'pair_success':sum(all(x['correct'] for x in ss if x['case_id']==cid) for cid in sorted({x['case_id'] for x in ss})),'abstentions':sum(x['abstained'] for x in ss),'false_positive':sum(x['false_positive'] for x in ss)}
result={'stage':stage,'summary':summary,'by_repeat':by_repeat,'rows':rows,'note':'Repeat totals are repeated measurements on11families, not33independent PRs. Unblinded author grading; no statistical superiority claim.'};(R/f'results_{stage}.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'summary':summary,'by_repeat':by_repeat},indent=2))
