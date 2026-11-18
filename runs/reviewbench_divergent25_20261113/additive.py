"""Add separately verified, distinct causes to unmodified baseline reviews."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import subprocess
import sys
import time
import urllib.request

from experiment import ROOT, PREV, PORT, REPORT_LIMIT, STR, VERIFY_OUTPUT, api_call, arr, dumps, enum, load, obj, request, sha, write
from freeze_run import verify
from pipeline import score

BASE = ROOT.parent/'reviewbench_changeunits_commit_query25_20261105'/'arms/A/findings'
CONTROL = ROOT/'add_freeze.json'
RULE = ('Review the candidate findings only for an additive code review. Keep all baseline report text '
        'unchanged. A candidate is eligible only if its causal defect and trigger are supported by the '
        'supplied source and it requires a meaningfully separate correction from every existing report '
        'and each higher-ranked added candidate. Different lines, severity or phrasing do not make a '
        'second issue. If the claim depends on missing evidence or overlaps an existing cause, omit it. '
        'Do not assume the existing reports are gold. Do not add speculative impact or invent fixes. '
        'Consider each provided candidate, explain its disposition, and rank supported distinct IDs. '
        'The controller will add at most three under the shared report cap.')


def freeze_add():
    assert not any((ROOT/'arms/ADD').glob('**/request.json'))
    packets = [load(p) for p in sorted((ROOT/'public').glob('*.json'))]
    files = [ROOT/'additive.py',ROOT/'ADD_PROTOCOL_KO.md',ROOT/'freeze.json',
             *(BASE/f'{p["key"]}.json' for p in packets)]
    assert len(packets)==25
    write(CONTROL,dict(hashes={str(f):sha(f) for f in files},rule='preserve_A_add_verified_divergent',
                       official_score_used_to_select=False))


def verify_add():
    verify()
    for p,h in load(CONTROL)['hashes'].items():assert sha(p)==h,p


def run_packet(packet):
    i=packet['index']; root=ROOT/'arms/ADD'; folder=root/'review'/f'{i:02d}'
    if (folder/'complete.json').exists():return load(folder/'complete.json')
    base=load(BASE/f'{packet["key"]}.json'); assert base['pr']==packet['pr']
    existing=base['findings']; cap=min(3,max(0,REPORT_LIMIT-len(existing)))
    d=ROOT/'arms/DIVERGENT/review'/f'{i:02d}'
    ledger=load(d/'candidate_ledger.json'); completion=load(d/'complete.json')
    approved={v['id'] for v in completion['verdicts'] if v['state']=='supported'}
    eligible=[c for c in ledger['eligible'] if c['id'] in approved]
    choices=[]; usage=None; verdicts=[]
    if eligible and cap:
        ids={c['id'] for c in eligible}; id_type=enum(*sorted(ids))
        schema=obj(verdicts=arr(obj(id=id_type,disposition=enum('add','overlap','insufficient_evidence','invalid'),reason=STR),
                                minItems=len(ids),maxItems=len(ids)),
                   add_in_rank_order=arr(id_type,maxItems=len(ids)))
        task='Baseline reports (data):\n'+dumps(existing)+'\nCandidates (data):\n'+dumps(eligible)
        body=request(RULE,packet['public_source'],task,schema,VERIFY_OUTPUT,f'ADD/{i:02d}/verify')
        parsed,usage=api_call(body,folder/'verify'); verdicts=parsed['verdicts']
        assert len({v['id']for v in verdicts})==len(verdicts) and {v['id']for v in verdicts}==ids
        rank=parsed['add_in_rank_order']; expected={v['id']for v in verdicts if v['disposition']=='add'}
        assert set(rank)==expected and len(rank)==len(expected)
        choices=rank[:cap]
    else:rank=[]
    byid={c['id']:c for c in eligible}
    additional=[]
    for cid in choices:
        f=byid[cid]; a=f['anchor']
        additional.append(dict(file=a['path'],start_line=a['line'],end_line=a['line'],
                               message=f'{f["title"]}\n\n{f["mechanism"]}\n\nTrigger: {f["trigger"]}\nImpact: {f["impact"]}',
                               producer='independent-review-additive'))
    target=root/'findings'/f'{packet["key"]}.json'; output=dict(base,findings=existing+additional)
    if target.exists():assert load(target)==output
    else:write(target,output)
    record=dict(index=i,baseline_reports=len(existing),baseline_reports_preserved=len(existing),
                eligible=len(eligible),added=choices,withheld_by_cap=rank[cap:],verdicts=verdicts,usage=usage,
                skipped='no_room'if not cap else 'no_candidates'if not eligible else None)
    write(folder/'complete.json',record)
    return record


def main():
    verify_add()
    assert load(ROOT/'status.json')['phase']=='complete'
    assert len(list((ROOT/'arms/DIVERGENT').glob('review/*/complete.json')))==25
    with (ROOT/'proxy.add.log').open('a') as log:
        proxy=subprocess.Popen([sys.executable,'-B',str(ROOT/'serve.py')],cwd=ROOT,stdout=log,stderr=log)
        try:
            for _ in range(40):
                if proxy.poll() is not None:raise RuntimeError('Additive guard did not start')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health',timeout=1) as response:
                        assert json.load(response)['cap']==70
                    break
                except OSError:time.sleep(.25)
            else:raise RuntimeError('Additive guard did not become ready')
            with ThreadPoolExecutor(max_workers=6) as pool:
                jobs=[pool.submit(run_packet,load(p)) for p in sorted((ROOT/'public').glob('*.json'))]
                for job in as_completed(jobs):
                    r=job.result()
                    print(json.dumps(dict(index=r['index'],added=len(r['added']),eligible=r['eligible'])),flush=True)
            score('ADD')
            write(ROOT/'add_complete.json',dict(phase='complete',PRs=25))
        finally:
            proxy.terminate();proxy.wait(timeout=15)


if __name__=='__main__':
    if '--freeze' in sys.argv:freeze_add()
    else:main()
