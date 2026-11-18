"""Post-hoc paired uncertainty, artifact capture and cost audit; no model calls."""
from pathlib import Path
import collections,hashlib,json,random,re,shutil,time
ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'reviewbench_changeunits_commit_query25_20261105'
def load(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def metrics(rows,kind):
    totals=collections.Counter()
    for m in rows:
        for k in ['matched_tp_count','matched_count','golden_covered_count','golden_tp_count','candidate_count','unmatched_tp_count']:
            totals[k]+=m[k]
    if kind=='grounded':
        p=totals['matched_tp_count']/totals['matched_count']if totals['matched_count']else 0
        r=totals['golden_covered_count']/totals['golden_tp_count']if totals['golden_tp_count']else 0
    else:
        p=(totals['matched_tp_count']+totals['unmatched_tp_count'])/totals['candidate_count']if totals['candidate_count']else 0
        denom=totals['golden_tp_count']+totals['unmatched_tp_count']
        r=(totals['golden_covered_count']+totals['unmatched_tp_count'])/denom if denom else 0
    return 2*p*r/(p+r)if p+r else 0
def main():
    scores={'A':load(OLD/'arms/A/scoring/results.json')}
    for arm in ('FLAT','PATH','GROUPED'):
        p=ROOT/'arms'/arm/'scoring/results.json'
        if p.exists():scores[arm]=load(p)
    rows={a:{p['pr_key']:p['metrics']['overall']for p in s['per_pr']}for a,s in scores.items()}
    keys=sorted(rows['A']);assert len(keys)==25
    for a,s in scores.items():
        assert sorted(rows[a])==keys
        for kind in ('grounded','augmented'):
            p=s['micro']['overall'][kind+'_precision'];r=s['micro']['overall'][kind+'_recall']
            assert abs(metrics(list(rows[a].values()),kind)-2*p*r/(p+r))<1e-10
    intervals=[]
    for a,b in [('FLAT','A'),('PATH','FLAT'),('GROUPED','FLAT'),('PATH','A')]:
        if a not in rows or b not in rows:continue
        for kind in ('grounded','augmented'):
            rng=random.Random(20261113);deltas=[]
            for _ in range(10000):
                sample=rng.choices(keys,k=25)
                deltas.append(metrics([rows[a][k]for k in sample],kind)-metrics([rows[b][k]for k in sample],kind))
            deltas.sort();intervals.append(dict(comparison=a+'-'+b,metric=kind+'_F1',
                observed=metrics(list(rows[a].values()),kind)-metrics(list(rows[b].values()),kind),
                percentile_95=[deltas[250],deltas[9749]],PR_resamples=10000,seed=20261113,
                posthoc=True,model_or_judge_repeat_uncertainty_included=False))
    sessions=[]
    for log in (ROOT/'arms').glob('*/scoring/judge.log'):
        for raw in re.findall(r'Session log:\s*(.+)',log.read_text()):
            source=Path(raw.strip())
            if not source.is_file():continue
            dest=log.parent/'sessions'/source.name;dest.parent.mkdir(exist_ok=True)
            if not dest.exists():shutil.copy2(source,dest)
            assert sha(source)==sha(dest)
            sessions.append(dict(source=str(source),copy=str(dest),sha256=sha(dest)))
    native={};physical=collections.Counter()
    prefix_keys=['native_calls','native_seconds','file_reads','source_bytes_read','ast_parses']
    for arm in ('FLAT','PATH'):
        total=collections.Counter()
        for p in (ROOT/'native').glob('*/'+arm+'/complete.json'):
            c=load(p)
            for k in prefix_keys:total[k]+=c['incremental_cost'].get(k,0)
        native[arm]=dict(total)
    # Common state cost is explicitly recorded on collector resumption; shared
    # physical collection is charged once, prefixes are reused without reread.
    for p in (ROOT/'native').glob('[0-9][0-9]'):
        f=load(p/'FLAT/context.json');v=load(p/'PATH/context.json');prefix=load(ROOT/'public'/f'{p.name}.json')['prefix']['cost']
        shared=next(x['carried_logical_cost']for x in f['admission_events']if x.get('event')=='frozen_context_resumed')
        for k in prefix_keys:physical[k]+=f['cost'].get(k,0)+v['cost'].get(k,0)-shared.get(k,0)-prefix.get(k,0)
    result=dict(created_at=time.time(),paired_PR_bootstrap=intervals,judge_sessions_copied=sessions,
        collector_incremental_logical_cost=native,collector_new_physical_cost=dict(physical),
        collector_cost_scope='Controller counters; snapshot archive/hash preparation is outside these counters and included in top-level collection wall time.',
        human_review=0,new_model_calls=0)
    (ROOT/'audit/posthoc-analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(dict(comparisons=intervals,sessions_copied=len(sessions),native_physical=dict(physical)),ensure_ascii=False))
if __name__=='__main__':main()
