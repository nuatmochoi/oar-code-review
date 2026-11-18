"""Apply the historical additive review protocol to GENERAL candidates."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import ast
import json
import secrets
import subprocess
import sys
import time
import traceback
import urllib.request

from experiment import (ROOT, PORT, REPORT_LIMIT, STR, VERIFY_OUTPUT, api_call,
                        arr, dumps, enum, load, obj, request, sha, write)
from pipeline import score, state
from freeze_run import verify

PRIOR = ROOT.parent / 'reviewbench_divergent25_20261113'
BASE = ROOT.parent / 'reviewbench_changeunits_commit_query25_20261105' / 'arms/A/findings'
ARM = 'ADD'
HISTORICAL_CODE = ast.parse((PRIOR / 'additive.py').read_text())
RULE = next(ast.literal_eval(node.value) for node in HISTORICAL_CODE.body
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'RULE' for t in node.targets))


def packets():
    rows = [load(file) for file in sorted((PRIOR/'public').glob('*.json'))]
    assert len(rows) == len({p['key'] for p in rows}) == 25
    return rows


def merge_input(packet, source_arm):
    base = load(BASE/f'{packet["key"]}.json')
    assert base['pr'] == packet['pr']
    existing = base['findings']
    cap = min(3, max(0, REPORT_LIMIT-len(existing)))
    prior = PRIOR/'arms'/source_arm/'review'/f'{packet["index"]:02d}'
    ledger = load(prior/'candidate_ledger.json')
    completion = load(prior/'complete.json')
    supported = {v['id'] for v in completion['verdicts'] if v['state']=='supported'}
    eligible = [c for c in ledger['eligible'] if c['id'] in supported]
    return base, cap, eligible


def merge_request(packet, existing, eligible):
    ids = enum(*sorted(c['id'] for c in eligible))
    n = len(eligible)
    schema = obj(verdicts=arr(obj(id=ids,disposition=enum('add','overlap','insufficient_evidence','invalid'),reason=STR),
                              minItems=n,maxItems=n), add_in_rank_order=arr(ids,maxItems=n))
    task = 'Baseline reports (data):\n'+dumps(existing)+'\nCandidates (data):\n'+dumps(eligible)
    return request(RULE,packet['public_source'],task,schema,VERIFY_OUTPUT,f'ADD/{packet["index"]:02d}/verify')


def select(assessment, eligible, cap):
    ids = {c['id'] for c in eligible}
    verdicts = assessment['verdicts']
    assert len(verdicts) == len(ids) and {v['id'] for v in verdicts} == ids
    expected = {v['id'] for v in verdicts if v['disposition']=='add'}
    rank = assessment['add_in_rank_order']
    assert len(rank) == len(set(rank)) and set(rank) <= expected
    assert len(rank) >= min(cap, len(expected)), 'Provider omitted rankings needed to fill the agreed report limit'
    tail = sorted(expected-set(rank))
    return rank[:cap], rank[cap:]+tail, tail


def run_packet(packet):
    index = packet['index']
    root = ROOT/'arms'/ARM
    folder = root/'review'/f'{index:02d}'
    if (folder/'complete.json').exists():
        return load(folder/'complete.json')
    base, cap, eligible = merge_input(packet,'GENERAL')
    existing = base['findings']
    usage, verdicts, chosen, withheld, unranked = None, [], [], [], []
    if eligible and cap:
        body = merge_request(packet,existing,eligible)
        result, usage = api_call(body,folder/'verify')
        verdicts = result['verdicts']
        chosen, withheld, unranked = select(result,eligible,cap)
    by_id = {c['id']:c for c in eligible}
    added = []
    for identifier in chosen:
        finding = by_id[identifier]
        anchor = finding['anchor']
        added.append(dict(file=anchor['path'],start_line=anchor['line'],end_line=anchor['line'],
                          message=f'{finding["title"]}\n\n{finding["mechanism"]}\n\nTrigger: {finding["trigger"]}\nImpact: {finding["impact"]}',
                          producer='independent-review-additive'))
    target = root/'findings'/f'{packet["key"]}.json'
    output = dict(base,findings=existing+added)
    if target.exists():
        assert load(target) == output
    else:
        write(target,output)
    record = dict(index=index,baseline_reports_preserved=len(existing),eligible=len(eligible),
                  added=chosen,withheld_by_cap=withheld,unranked_positive_tail=unranked,
                  verdicts=verdicts,usage=usage,skipped='no_room' if not cap else 'no_candidates' if not eligible else None)
    write(folder/'complete.json',record)
    return record


def main():
    frozen = verify()
    token_path = ROOT/'broker.token'
    if not token_path.exists():
        token_path.touch(mode=0o600)
        token_path.write_text(secrets.token_urlsafe(32))
    with (ROOT/'proxy.log').open('a') as log:
        proxy = subprocess.Popen([sys.executable,'-B',str(ROOT/'serve.py')],cwd=ROOT,stdout=log,stderr=log)
        try:
            for _ in range(40):
                if proxy.poll() is not None:
                    raise RuntimeError('Budget proxy stopped')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health',timeout=1) as stream:
                        assert json.load(stream)['cap'] == frozen['new_api_cap_usd']
                    break
                except OSError:
                    time.sleep(.25)
            else:
                raise RuntimeError('Budget proxy unavailable')
            state('review')
            errors = []
            with ThreadPoolExecutor(max_workers=6) as pool:
                jobs = {pool.submit(run_packet,packet):packet['index'] for packet in packets()}
                for job in as_completed(jobs):
                    try:
                        result = job.result()
                        print(dumps(dict(index=result['index'],added=len(result['added']))),flush=True)
                    except Exception as error:
                        index = jobs[job]
                        errors.append(index)
                        write(ROOT/'failures'/f'{index:02d}-{time.time_ns()}.json',
                              dict(error=str(error),traceback=traceback.format_exc()))
            if errors:
                raise RuntimeError(f'Combine failed for {errors}')
            state('judge')
            score(ARM)
            state('complete')
        except Exception as error:
            state('failed',error=str(error))
            raise
        finally:
            proxy.terminate()
            proxy.wait(timeout=15)


if __name__ == '__main__':
    main()
