"""One-to-one finding F1 and paired development-case comparison."""
import collections,hashlib,json,random,statistics
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
audit=read(R/'audit_all.json');ann={}
for arm in ['C0','C1']:
    part=read(R/f'author_annotations_{arm}.json');assert not set(ann)&set(part);ann.update(part)
assert len(ann)==144 and len(audit['rows'])==144
(R/'author_annotations.json').write_text(json.dumps(ann,ensure_ascii=False,indent=2))
rows=[]
for x in audit['rows']:
    rid=x['run_id'];g=ann[rid]
    assert all(hashlib.sha256((R/'raw'/f'{rid}_s{k}.json').read_bytes()).hexdigest()==h for k,h in g['raw_sha256'].items())
    final=x.get('final');rev=final['review'] if final else None;n=len(rev['issues']) if rev else 0
    assert sorted(g['supported_issue_indices']+g['unsupported_issue_indices'])==list(range(n)),rid
    assert set(g['supported_issue_indices']).issubset(g['local_supported_issue_indices']) and set(g['local_supported_issue_indices']).issubset(range(n)),rid
    valid=x['status']=='ok' and not x['errors'];unsafe=x['label']=='unsafe'
    tp=int(valid and unsafe and bool(g['supported_issue_indices']))
    ltp=int(valid and unsafe and bool(g['local_supported_issue_indices']))
    stp=int(unsafe and n>0);sfp=int(not unsafe and n>0)
    safe=bool(valid and not unsafe and rev and rev['status']=='no_supported_issue' and g['safe_verdict_supported'])
    rows.append({k:x[k] for k in ['run_id','case_id','world','arm','repeat','label']}|{
        'valid':valid,'grounded_tp':tp,'grounded_fp':n-tp,'grounded_fn':int(unsafe)-tp,
        'local_tp':ltp,'local_fp':n-ltp,'local_fn':int(unsafe)-ltp,
        'status_tp':stp,'status_fp':sfp,'status_fn':int(unsafe)-stp,
        'reported_issues':n,'safe_false_alarm_reviews':sfp,'safe_false_alarm_issues':n if not unsafe else 0,
        'unsafe_unmatched_issues':n-tp if unsafe else 0,
        'safe_grounded':safe,'raw_safe_no_issue':bool(not unsafe and rev and rev['status']=='no_supported_issue'),
        'abstained':bool(rev and rev['status']=='insufficient_evidence'),
        'correct':safe or bool(tp and not g['unsupported_issue_indices'] and n==1),
        'first_target_state':g['first_target_state'],'final_target_state':g['final_target_state'],
        'physical_calls':x.get('physical_calls',0),'source_tokens':x.get('source_tokens',0),'observation_tokens':x.get('observation_tokens',0),
        'seconds':x.get('seconds',0),'usage':x.get('usage',[])})
def metric(tp,fp,fn):
    return {'TP':tp,'FP':fp,'FN':fn,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,'F1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0}
def summary(rr):
    out={k:sum(x[k] for x in rr) for k in ['valid','reported_issues','safe_false_alarm_reviews','safe_false_alarm_issues','unsafe_unmatched_issues','safe_grounded','raw_safe_no_issue','abstained','physical_calls']}
    for name in ['grounded','local','status']:out[name+'_F1']=metric(*(sum(x[name+'_'+k] for x in rr) for k in ['tp','fp','fn']))
    pairs={(x['case_id'],x['repeat']) for x in rr}
    out['pair_success']=sum(all(x['correct'] for x in rr if (x['case_id'],x['repeat'])==p) for p in pairs)
    out['trajectories']=len(rr);out['unsafe_reviews']=sum(x['label']=='unsafe' for x in rr);out['safe_reviews']=sum(x['label']=='safe' for x in rr)
    out['unsafe_first_to_final']=dict(collections.Counter(x['first_target_state']+' -> '+x['final_target_state'] for x in rr if x['label']=='unsafe'))
    out['mean_source_tokens']=statistics.mean(x['source_tokens'] for x in rr);out['mean_observation_tokens']=statistics.mean(x['observation_tokens'] for x in rr)
    out['model_seconds_sum']=sum(x['seconds'] for x in rr)
    out['usage']={k:sum(u.get(k,0) for x in rr for u in x['usage']) for k in ['input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens']}
    return out
arms=['C0','C1'];cases=sorted({x['case_id'] for x in rows})
totals={arm:summary([x for x in rows if x['arm']==arm]) for arm in arms}
per_case={cid:{arm:summary([x for x in rows if x['case_id']==cid and x['arm']==arm]) for arm in arms} for cid in cases}
rng=random.Random(2026102303);draws=[[rng.choice(cases) for _ in cases] for _ in range(10000)];contrasts={}
for name in ['grounded_F1','local_F1','status_F1']:
    samples=[]
    for draw in draws:
        values={arm:metric(*(sum(per_case[c][arm][name][k] for c in draw) for k in ['TP','FP','FN']))['F1'] for arm in arms}
        samples.append(values['C1']-values['C0'])
    samples.sort();contrasts[name]={'delta_percentage_points':100*(totals['C1'][name]['F1']-totals['C0'][name]['F1']),'PR_cluster_percentile_95CI_pp':[100*samples[249],100*samples[9749]],'bootstrap_samples':10000}
out={'summary':totals,'per_case':per_case,'contrasts':contrasts,'rows':rows,'notes':['Evidence-grounded finding F1 counts all unmatched final issues as FP; insufficient-evidence FP need not be a nonexistent real bug.','At most one gold match per unsafe review. No raw issue or abstention is silently dropped.','Local-causal and raw-status F1 are distinct sensitivity/reference metrics.','Same previously analyzed 12 PRs, nonblind AI assistant grading; not holdout.','Bootstrap resamples PR clusters including both arms and all repeats, then recomputes F1 from pooled counts.']}
(R/'scores.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({'summary':totals,'contrasts':contrasts},ensure_ascii=False,indent=2))
