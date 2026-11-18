"""Aggregate existing official results and reviewer ledgers. No API calls."""
from collections import Counter
from pathlib import Path
import json
import random
import statistics

from budget_proxy import estimated_cost
from experiment import ARMS, PREV, ROOT, load, write


def score_path(arm):
    if arm == 'A':
        return PREV.parent/'reviewbench_changeunits_commit_query25_20261105'/'arms/A/scoring'
    return ROOT/'arms'/arm/'scoring'


def sum_cost():
    rows = []
    for file in sorted((ROOT/'api').glob('*/admission.json')):
        row = load(file)
        settled = file.with_name('receipt.json')
        if settled.exists(): row=load(settled)
        cost = estimated_cost(row.get('usage') or {})
        rows.append(dict(phase=row['phase'],status=row['status'],known=(cost or {}).get('usd_list_rate_estimate'),
                         upper=row.get('settled_upper',row['reserved_usd'])))
    totals = {}
    for k in ('all','review','judge'):
        subset=[r for r in rows if k=='all' or r['phase']==k]
        totals[k]=dict(calls=len(subset),known_usd=sum(r['known'] or 0 for r in subset),
                       conservative_usd=sum(r['upper'] for r in subset),statuses=dict(Counter(r['status'] for r in subset)))
    return totals


def f1(p, r): return 2*p*r/(p+r) if p is not None and r is not None and p+r else 0.


def counts(rows):
    c=Counter()
    for r in rows:
        for k in ('matched_tp_count','matched_count','golden_covered_count','golden_tp_count','candidate_count','unmatched_tp_count'):
            c[k]+=r[k]
    return c


def metrics(c):
    p=c['matched_tp_count']/c['matched_count'] if c['matched_count'] else 0.
    r=c['golden_covered_count']/c['golden_tp_count'] if c['golden_tp_count'] else 0.
    ap=(c['matched_tp_count']+c['unmatched_tp_count'])/c['candidate_count'] if c['candidate_count'] else 0.
    ar=(c['golden_covered_count']+c['unmatched_tp_count'])/(c['golden_tp_count']+c['unmatched_tp_count']) if c['golden_tp_count']+c['unmatched_tp_count'] else 0.
    return dict(grounded_F1=f1(p,r),augmented_F1=f1(ap,ar),grounded_precision=p,recall=r)


def scores(arm):
    path=score_path(arm)
    results=load(path/'results.json')
    details=load(path/'results.details.json')
    assert len(details)==results['pr_count']==25
    seen,stat,categories,groups=set(),Counter(),Counter(),Counter()
    for r in details:
        k=r['pr_key']
        gold=load(ROOT/'upstream/golden'/f'{k}.json')['findings']
        for f in r['candidate_findings']:
            stat[f['status']]+=1
            for j in f['covered_golden_indices']:
                if gold[j]['tp_fp']=='tp':seen.add((k,j))
        for kk,j in seen:
            if kk==k:
                categories[gold[j]['category']]+=1
                groups[gold[j]['context_required']]+=1
    per={r['pr_key']:r['metrics']['overall'] for r in results['per_pr']}
    calculated=metrics(counts(per.values()))
    official=results['micro']['overall']
    for m in ('grounded','augmented'):
        assert abs(calculated[m+'_F1']-f1(official[m+'_precision'],official[m+'_recall']))<1e-8
    assert len(seen)==official['golden_covered_count'] and official['golden_tp_count']==352
    return dict(**calculated,gold=len(seen),reports=official['candidate_count'],novel_tp=stat['novel_tp'],
                false_findings=stat['matched_fp']+stat['novel_fp'],status=dict(stat),
                by_category=dict(categories),by_context=dict(groups)),seen,per


def mechanism(arm):
    p=ROOT/'arms'/arm/'review'
    rows=[load(x) for x in sorted(p.glob('*/complete.json'))]
    verdicts=Counter(v['state'] for r in rows for v in r['verdicts'])
    paths, branches = [], Counter()
    for record in rows:
        r=p/f'{record["index"]:02d}'
        x,y=({c['anchor']['path'] for c in load(r/f'discover-{j}'/'parsed.json')['candidates']} for j in (0,1))
        if x|y:paths.append(len(x&y)/len(x|y))
        for j in (1,2):
            branches[f'branch_{j}_emitted']+=sum(cid.startswith(f'P{j}-') for cid in record['kept'])
    return dict(reviewed=len(rows),raw_candidates=sum(r['raw_candidates'] for r in rows),
                eligible=sum(r['eligible'] for r in rows),ineligible=sum(r['ineligible'] for r in rows),
                emitted=sum(len(r['kept']) for r in rows),no_report_PRs=sum(not r['kept'] for r in rows),
                withheld_by_cap=sum(len(r['withheld_by_report_cap']) for r in rows),verdicts=dict(verdicts),
                actual_review_tokens=sum(r['actual_tokens'] for r in rows),
                review_usd=sum(estimated_cost(u)['usd_list_rate_estimate']for r in rows for u in r['usage']),
                model_calls=sum(r['model_calls'] for r in rows),
                mean_candidate_path_overlap=statistics.mean(paths) if paths else None,**dict(branches))


def additive_mechanism():
    root=ROOT/'arms/ADD'; base=score_path('A').parent/'findings'
    rows=[load(f) for f in sorted((root/'review').glob('*/complete.json'))]
    for p in (root/'findings').glob('*.json'):
        old=load(base/p.name); new=load(p)
        assert old['pr']==new['pr'] and old['findings']==new['findings'][:len(old['findings'])]
    v=Counter(x['disposition']for r in rows for x in r['verdicts'])
    return dict(reviewed=len(rows),original_reports_preserved=sum(r['baseline_reports_preserved']for r in rows),
                appended=sum(len(r['added'])for r in rows),
                with_addition_PRs=sum(bool(r['added'])for r in rows),
                withheld_by_cap=sum(len(r['withheld_by_cap'])for r in rows),
                verifications=sum(bool(r['usage'])for r in rows), verdicts=dict(v),
                review_usd=sum(estimated_cost(r['usage'])['usd_list_rate_estimate']for r in rows if r['usage']))


def delta(label, a, b, found, per):
    left,right=found[a],found[b]
    keys=sorted(per[a]); assert keys==sorted(per[b]); assert len(keys)==25
    seed=20261113
    rng=random.Random(seed)
    d={m:[] for m in ('grounded_F1','augmented_F1')}
    for _ in range(10000):
        sampled=rng.choices(keys,k=len(keys))
        x=metrics(counts(per[a][k] for k in sampled))
        y=metrics(counts(per[b][k] for k in sampled))
        for m in d:d[m].append(x[m]-y[m])
    bands={m:[sorted(v)[i] for i in (250,9749)] for m,v in d.items()}
    return dict(comparison=label,retained=len(left & right),lost=len(right-left),added=len(left-right),
                observed_union=len(left|right),F1_delta_95_pr_bootstrap=bands,seed=seed,includes_rerun_uncertainty=False)


def main():
    records,found,per={}, {}, {}
    for arm in ('A',*ARMS,'ADD'):
        if (score_path(arm)/'results.json').exists():
            records[arm],found[arm],per[arm]=scores(arm)
    checks=[]
    for a,b in [('DIVERGENT','GENERAL'),('GENERAL','A'),('DIVERGENT','A'),('ADD','A'),('ADD','DIVERGENT')]:
        if a in records and b in records:checks.append(delta(f'{a}-{b}',a,b,found,per))
    m={a:mechanism(a) for a in ARMS}
    if (ROOT/'arms/ADD').exists():m['ADD']=additive_mechanism()
    pre=load(ROOT/'preflight.json')['PRs']
    c={k:sum(r['coverage'][k]for r in pre)for k in ['total_hunks','supplied_hunks','total_files','supplied_files','available_bodies','supplied_bodies']}
    c['partial_diff_PRs']=sum(r['coverage']['supplied_hunks']<r['coverage']['total_hunks'] for r in pre)
    result=dict(records=records,mechanism=m,pairs=checks,coverage=c,cost=sum_cost(),human_review=0,
                official_scored_arms=list(records),original_score_as_historical_reference=True)
    destination=ROOT/'summary.json'
    tmp=destination.with_suffix('.tmp')
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    tmp.replace(destination)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
