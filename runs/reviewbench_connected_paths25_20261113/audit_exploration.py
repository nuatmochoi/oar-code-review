"""Post-hoc breadth/depth audit; never imported by collection or reviewer."""
from pathlib import Path
import json,collections
ROOT=Path(__file__).resolve().parent
def load(p):return json.loads(Path(p).read_text())
def key(q):return tuple(q.get(x)for x in ('snapshot','file','line','column','symbol','query','kind'))
def main():
    pairs=[]
    for p in sorted((ROOT/'native').glob('[0-9][0-9]')):
        if not(p/'complete.json').exists():continue
        pools={};arms={}
        for arm in ('FLAT','PATH'):
            led=load(p/arm/'query_ledger.json');ctx=load(p/arm/'context.json')
            qs=[x['question']for x in led['queries']if x['question'].get('depth',0)>0]
            pools[arm]={key(q)for q in qs}
            parents={q.get('parent_evidence_id')for q in qs}
            arms[arm]=dict(executed_followup_queries=len(qs),distinct_query_files=len({(q['snapshot'],q['file'])for q in qs}),
                distinct_parent_bodies=len(parents),seed_queries=sum(q['depth']==1 for q in qs),
                deeper_queries=sum(q['depth']>1 for q in qs),maximum_depth=max([q['depth']for q in qs]or[0]),
                actual_delivered_source_files=len({(e['snapshot'],e['path'])for e in ctx['evidence']if e['kind']in('implementation_body','source_lines','body','exception_branch')}),
                deferred_seed_queries=sum(q.get('depth',0)==1 for q in led['deferred']),
                deferred_deeper_queries=sum(q.get('depth',0)>1 for q in led['deferred']))
        pairs.append(dict(index=int(p.name),arms=arms,common_followup_queries=len(pools['FLAT']&pools['PATH']),
                          only_FLAT_queries=len(pools['FLAT']-pools['PATH']),only_PATH_queries=len(pools['PATH']-pools['FLAT'])))
    totals={a:{k:sum(p['arms'][a][k]for p in pairs)for k in pairs[0]['arms'][a]if k!='maximum_depth'}for a in ('FLAT','PATH')}if pairs else{}
    out=dict(status='posthoc_mechanism_audit_not_gold_relevance',PRs=len(pairs),per_PR=pairs,sums_over_PR=totals,
             interpretation='All counts describe observed exploration. Larger breadth/depth or more files do not establish relevant evidence, new TP, runtime feasibility, or F1 gain.',
             frozen_method_changed=False,private_gold_read=False)
    (ROOT/'audit/exploration-breadth.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(json.dumps(dict(PRs=len(pairs),totals=totals),ensure_ascii=False))
if __name__=='__main__':main()
