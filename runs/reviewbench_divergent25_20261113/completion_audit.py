"""Confirm coverage, matched controls, budget settlement, and earlier artifact hashes."""
import json
from pathlib import Path
import socket

from experiment import ARMS, PORT, PREV, ROOT, discovery_request, load, sha, write
from freeze_run import verify
from additive import BASE, verify_add
from summarize import score_path, sum_cost


def main():
    frozen=verify(); verify_add()
    assert load(ROOT/'status.json')['phase']=='complete'
    assert load(ROOT/'add_complete.json')['phase']=='complete'
    packets=[load(p)for p in sorted((ROOT/'public').glob('*.json'))]
    keys={p['key']for p in packets}
    assert len(keys)==25
    control_pairs=0; baseline_reports=0
    for p in packets:
        baseline=load(BASE/f'{p["key"]}.json')['findings']
        added=load(ROOT/'arms/ADD/findings'/f'{p["key"]}.json')['findings']
        assert added[:len(baseline)]==baseline
        baseline_reports+=len(baseline)
        for j in (0,1):
            normalized=[]
            for a in ARMS:
                r=load(ROOT/'arms'/a/'review'/f'{p["index"]:02d}'/f'discover-{j}/request.json')
                assert r==discovery_request(p,a,j)
                r.pop('metadata')
                assert r['input'][1]['content'].startswith(p['public_source'])
                r['input'][1]['content']=p['public_source']
                normalized.append(r)
            assert normalized[0]==normalized[1]
            control_pairs+=1
    for a in (*ARMS,'ADD'):
        details=load(score_path(a)/'results.details.json')
        assert len(details)==25 and {r['pr_key']for r in details}==keys
        assert len(list((ROOT/'arms'/a/'review').glob('*/complete.json')))==25
    old=load(PREV/'audit/preservation.json')['files']
    assert all(sha(p)==h for p,h in old.items())
    earlier_freeze=load(PREV/'freeze.json')['files']
    assert all(sha(p)==h for p,h in earlier_freeze.items())
    c=sum_cost()['all']; assert c['conservative_usd']<=70 and not c['statuses'].get('admitted_unknown')
    with socket.socket() as sock:
        sock.settimeout(1)
        assert sock.connect_ex(('127.0.0.1',PORT))!=0
    result=dict(official_scored_PRs={a:25 for a in (*ARMS,'ADD')},
                discovery_pairs_matching_except_lens=control_pairs, baseline_reports_preserved=baseline_reports,
                frozen_files_checked=len(frozen['hashes']), old_artifacts_unchanged=len(old),
                previous_frozen_files_unchanged=len(earlier_freeze),physical_api=c,proxy_closed=True)
    write(ROOT/'completion.json',result)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
