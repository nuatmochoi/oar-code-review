"""Reporting-only recovery for a valid top-three list without the unused tail."""
from additive import BASE, verify_add
from experiment import ROOT, load, write


def main():
    verify_add()
    packets=[load(p)for p in sorted((ROOT/'public').glob('*.json'))]
    root=ROOT/'arms/ADD'
    pending=[p for p in packets if not (root/'review'/f'{p["index"]:02d}'/'complete.json').exists()]
    assert len(pending)==1
    p=pending[0]; folder=root/'review'/f'{p["index"]:02d}'
    base=load(BASE/f'{p["key"]}.json'); assert base['pr']==p['pr']
    old=base['findings']; cap=min(3,6-len(old)); assert cap>0
    d=ROOT/'arms/DIVERGENT/review'/f'{p["index"]:02d}'
    supported={v['id']for v in load(d/'complete.json')['verdicts'] if v['state']=='supported'}
    eligible={c['id']:c for c in load(d/'candidate_ledger.json')['eligible'] if c['id'] in supported}
    v=load(folder/'verify/parsed.json'); rank=v['add_in_rank_order']; verdicts=v['verdicts']
    assert len(verdicts)==len(eligible) and {r['id']for r in verdicts}==set(eligible)
    expected={r['id']for r in verdicts if r['disposition']=='add'}
    assert len(rank)==len(set(rank))==3 and set(rank)<expected
    # Enough valid explicit choices to fill the fixed cap. No outside inputs or invented rank.
    additional=[]
    for cid in rank[:cap]:
        f=eligible[cid]; a=f['anchor']
        additional.append(dict(file=a['path'],start_line=a['line'],end_line=a['line'],
                               message=f'{f["title"]}\n\n{f["mechanism"]}\n\nTrigger: {f["trigger"]}\nImpact: {f["impact"]}',
                               producer='independent-review-additive'))
    write(root/'findings'/f'{p["key"]}.json',dict(base,findings=old+additional))
    write(folder/'complete.json',dict(index=p['index'],baseline_reports=len(old),baseline_reports_preserved=len(old),
                eligible=len(eligible),added=rank[:cap],withheld_by_cap=rank[cap:]+sorted(expected-set(rank)),
                verdicts=verdicts,usage=load(folder/'verify/response.json')['usage'],skipped=None,
                recovery='Explicit ranked eligible top three retained, unranked positive tail recorded as withheld.'))
    write(ROOT/'add_ranking_recovery.json',dict(index=p['index'],output_cap=cap,original_ranking=rank,
                original_unranked_positive_ids=sorted(expected-set(rank)),new_model_calls=0,used_gold=False))
    print('Recovered validated existing ranked outputs for',p['index'])


if __name__=='__main__':main()
