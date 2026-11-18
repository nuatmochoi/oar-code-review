"""Score frozen reviews using explicit assistant-author annotations."""
import collections,hashlib,json,random,statistics
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
audit=read(R/'audit_all.json');ann=read(R/'author_annotations.json');rows=[]
assert len(audit['rows'])==288 and len(ann)==288
for x in audit['rows']:
 rid=x['run_id'];a=ann[rid]
 assert all(hashlib.sha256((R/'raw'/f'{rid}_s{k}.json').read_bytes()).hexdigest()==h for k,h in a['raw_sha256'].items()),rid
 final=x.get('final');rev=final['review'] if final else None
 issue_count=len(rev['issues']) if rev else 0
 assert sorted(a['supported_issue_indices']+a['unsupported_issue_indices'])==list(range(issue_count)),rid
 valid=x['status']=='ok' and not x.get('errors')
 detected=valid and x['label']=='unsafe' and bool(a['supported_issue_indices'])
 safe=valid and x['label']=='safe' and rev['status']=='no_supported_issue' and a['safe_verdict_supported']
 rows.append({k:x[k] for k in ['run_id','case_id','world','arm','repeat','label']}|{
  'valid':valid,'grounded_detection':detected,'safe_grounded':safe,
  'false_positive':x['label']=='safe' and bool(issue_count),
  'raw_unsafe_reported':x['label']=='unsafe' and bool(issue_count),
  'raw_safe_no_issue':x['label']=='safe' and rev is not None and rev['status']=='no_supported_issue',
  'abstained':rev is not None and rev['status']=='insufficient_evidence',
  'correct':safe or (detected and not a['unsupported_issue_indices']),
  'unsupported_issues':len(a['unsupported_issue_indices']),
  'first_target_state':a['first_target_state'],'final_target_state':a['final_target_state'],
  'physical_calls':x.get('physical_calls',0),'source_tokens':x.get('source_tokens',0),
  'observation_tokens':x.get('observation_tokens',0),'seconds':x.get('seconds',0),
  'usage':x.get('usage',[])})
arms=['P0Q0','P1Q0','P0Q1','P1Q1'];cases=sorted({x['case_id'] for x in rows});summary={};per_case={}
for arm in arms:
 rr=[x for x in rows if x['arm']==arm];pairs={(x['case_id'],x['repeat']) for x in rr}
 keys=['valid','grounded_detection','safe_grounded','false_positive','raw_unsafe_reported','raw_safe_no_issue','abstained','unsupported_issues','physical_calls']
 s={k:sum(x[k] for x in rr) for k in keys};s.update(trajectories=len(rr),unsafe_reviews=36,safe_reviews=36,pair_repeats=36)
 s['correct_pair_repeats']=sum(all(x['correct'] for x in rr if (x['case_id'],x['repeat'])==p) for p in pairs)
 s['unsafe_first_to_final']=dict(collections.Counter(x['first_target_state']+' -> '+x['final_target_state'] for x in rr if x['label']=='unsafe'))
 s['mean_source_tokens']=statistics.mean(x['source_tokens'] for x in rr);s['mean_observation_tokens']=statistics.mean(x['observation_tokens'] for x in rr)
 s['model_seconds_sum']=sum(x['seconds'] for x in rr)
 s['usage']={k:sum(u.get(k,0) for x in rr for u in x['usage']) for k in ['input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens']}
 summary[arm]=s
 for cid in cases:
  cr=[x for x in rr if x['case_id']==cid]
  per_case.setdefault(cid,{})[arm]={k:sum(x[k] for x in cr) for k in ['grounded_detection','safe_grounded','false_positive','abstained','raw_unsafe_reported']}
  per_case[cid][arm]['correct_pair_repeats']=sum(all(x['correct'] for x in cr if x['repeat']==rep) for rep in [1,2,3])
contrasts={'P_at_Q0':[-1,1,0,0],'P_at_Q1':[0,0,-1,1],'Q_at_P0':[-1,0,1,0],'Q_at_P1':[0,-1,0,1],'combined_vs_baseline':[-1,0,0,1],'interaction':[1,-1,-1,1]}
bootstrap={};rng=random.Random(20261025);draws=[[rng.randrange(12) for _ in range(12)] for _ in range(10000)]
for metric in ['grounded_detection','correct_pair_repeats']:
 bootstrap[metric]={}
 for name,weights in contrasts.items():
  deltas=[sum(w*per_case[c][arm][metric]/3 for w,arm in zip(weights,arms)) for c in cases]
  b=sorted(statistics.mean(deltas[i] for i in d) for d in draws)
  bootstrap[metric][name]={'delta_percentage_points':100*statistics.mean(deltas),'PR_cluster_percentile_95CI_pp':[100*b[249],100*b[9749]],'positive_PRs':sum(v>0 for v in deltas),'negative_PRs':sum(v<0 for v in deltas),'ties':sum(v==0 for v in deltas)}
out={'summary':summary,'per_case':per_case,'contrasts':bootstrap,'rows':rows,'notes':['Assistant-author nonblind adjudication, not independent human labels.','Bootstrap resamples 12 purposively selected PRs with all repeats/arms together; descriptive within-sample uncertainty, not population representativeness.','All 288 trajectories remain in denominators; unsupported raw issue is not grounded detection; safe reported issue counts as false positive even if evidence citation fails.']}
(R/'scores.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in out.items() if k!='rows'},ensure_ascii=False,indent=2))
