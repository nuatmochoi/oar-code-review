"""Freeze the paired inputs, then run two retrieval policies and the official scorer."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request

import numpy as np

from adaptive import combine, review_packet, source_index
from budget_proxy import estimated_cost
from encoder import Encoder
from experiment import ARM_PORTS, PORT, ROOT, PREV, load, sha, write
from fallback import collect
from freeze_run import verify
from merge import BASE, PRIOR, packets
from pipeline import score
from relationships import build


CONTROL = ROOT.parent/'reviewbench_adaptive_multi_id25_20261113'
ROLE_RUN = ROOT.parent/'reviewbench_role_bundles25_20261113'
ARM = os.getenv('REVIEWBENCH_FALLBACK_ARM','SEMANTIC')


def historical(packet):
    return CONTROL/'arms/ADD/review'/f'{packet["index"]:02d}'


def collector(arm, packet, encoder, graph):
    index = packet['index']
    vectors = np.load(ROOT/'vectors'/f'{index:02d}.npy',allow_pickle=False)
    return lambda first, second, entries, seen: collect(first,second,entries,seen,encoder,vectors,graph,arm)


def freeze():
    previous = load(CONTROL/'freeze.json')
    role_run = load(ROLE_RUN/'freeze.json')
    assert previous['new_api_cap_usd']==role_run['new_api_cap_usd']
    started = time.monotonic()
    checks = []
    encoder = Encoder()
    for packet in packets():
        index = packet['index']
        old = historical(packet)
        _, entries = source_index(packet)
        graph = build(packet,entries)
        write(ROOT/'graph'/f'{index:02d}.json',graph)
        # Use an inert callback to replay and check all original plan/update/verify requests.
        fallback_empty = lambda *_: dict(targets=[],receipts=[],tokens=0)
        review, _ = review_packet(packet,ROOT/'audit/preflight'/f'{index:02d}',old,fallback_empty)
        original = load(old/'questions.json')
        assert {k:v for k,v in original.items() if k in review}=={k:review[k] for k in original if k in review}
        row = dict(index=index,blocks=len(entries),routes=len(graph['routes']),
                   full_routes=sum(any(b['ids'] for b in r['failure_branches']) for r in graph['routes']))
        row['shared_cost'] = sum(estimated_cost(load(old/stage/'response.json')['usage'])['usd_list_rate_estimate']
                                 for stage in ('plan','update'))
        for arm in ARM_PORTS:
            check = {}

            def simulate(first,second,entries,seen):
                value = collector(arm,packet,encoder,graph)(first,second,entries,seen)
                check.update(eligible=len(value['receipts']),delivered=sum(bool(q['selected']) for q in value['receipts']),
                             source_tokens=value['tokens'],reranked=sum(q['selected']!=q['semantic_choice']
                                                                       for q in value['receipts']))
                return fallback_empty()

            review_packet(packet,ROOT/'audit/preflight'/f'{index:02d}',old,simulate)
            row[arm] = check
        checks.append(row)
    assert any(row['CONNECTED']['reranked'] for row in checks)
    write(ROOT/'preflight.json',dict(prs=checks,preflight_seconds=time.monotonic()-started))
    local = [*ROOT.glob('*.py'),ROOT/'PROTOCOL_KO.md',ROOT/'preflight.json',
             *ROOT.glob('graph/*.json'),*ROOT.glob('model/*'),*ROOT.glob('vectors/*.npy'),
             *ROOT.glob('syntax_dependencies/**/*.so'),*ROOT.glob('syntax_dependencies/tree_sitter_language_pack/*.py')]
    for arm, port in ARM_PORTS.items():
        folder = ROOT/'arms'/arm
        upstream = folder/'upstream'
        upstream.mkdir(parents=True)
        for name in ('package.json','package-lock.json','scripts','schema','corpus','golden','node_modules'):
            (upstream/name).symlink_to((PRIOR/'upstream'/name).resolve())
        shutil.copytree(CONTROL/'judge-config',folder/'judge-config')
        config = folder/'judge-config/models.json'
        data = load(config)
        for provider in data['providers'].values():
            provider['baseUrl'] = f'http://127.0.0.1:{port}/judge/v1'
        config.write_text(json.dumps(data,indent=2))
        shutil.copytree(ROLE_RUN/'judge-repos',folder/'judge-repos',symlinks=True)
        local += [config,folder/'judge-config/bin/fd']
    older = [CONTROL/'freeze.json',CONTROL/'RESULTS_KO.md',CONTROL/'summary.json',
             *CONTROL.glob('arms/ADD/scoring/results*.json'),*CONTROL.glob('arms/ADD/findings/*.json'),
             *CONTROL.glob('arms/ADD/review/*/questions.json'),*CONTROL.glob('arms/ADD/review/*/complete.json'),
             *CONTROL.glob('arms/ADD/review/*/*/request.json'),*CONTROL.glob('arms/ADD/review/*/*/parsed.json'),
             *CONTROL.glob('arms/ADD/review/*/*/response.json'),ROLE_RUN/'freeze.json',ROLE_RUN/'RESULTS_KO.md',
             *ROLE_RUN.glob('arms/ADD/scoring/results*.json'),*PREV.glob('native/*/*/raw_queries.json'),
             *PREV.glob('native/*/*/context.json')]
    hashes = previous['hashes'] | {str(path):sha(path) for path in local+older}
    per_arm_shared = {arm:sum(row['shared_cost'] for row in checks) for arm in ARM_PORTS}
    for arm in ARM_PORTS:
        for packet, row in zip(packets(),checks,strict=True):
            if row[arm]['source_tokens']:
                continue
            old = historical(packet)
            for stage in ('verify','merge'):
                path = old/stage/'response.json'
                if path.exists():
                    per_arm_shared[arm] += estimated_cost(load(path)['usage'])['usd_list_rate_estimate']
    write(ROOT/'freeze.json',dict(prs=25,arms=list(ARM_PORTS),new_api_cap_usd=previous['new_api_cap_usd'],
                                  shared_cost=per_arm_shared,hashes=hashes))
    print(f'Frozen {len(hashes)} inputs and exact historical stages',flush=True)


def one(packet, encoder):
    index = packet['index']
    folder = ROOT/'arms'/ARM/'review'/f'{index:02d}'
    complete = folder/'complete.json'
    if complete.exists():
        return load(complete)
    graph = load(ROOT/'graph'/f'{index:02d}.json')
    trace = folder/'questions.json'
    if trace.exists():
        review = load(trace)
    else:
        review, _ = review_packet(packet,folder,historical(packet),collector(ARM,packet,encoder,graph))
        write(trace,review)
    base = load(BASE/f'{packet["key"]}.json')
    assert base['pr']==packet['pr']
    cap = min(3,max(0,6-len(base['findings'])))
    result = combine(packet,base,folder,review['candidate_ledger'],cap,ARM,historical(packet))
    write(complete,result)
    return result


def state(phase, **extra):
    folder = ROOT/'arms'/ARM
    value = dict(phase=phase,arm=ARM,pid=os.getpid(),time=time.time(),**extra)
    temporary = folder/'status.tmp'
    temporary.write_text(json.dumps(value,indent=2))
    temporary.replace(folder/'status.json')
    print(json.dumps(value),flush=True)


def main():
    frozen = verify()
    folder = ROOT/'arms'/ARM
    token = ROOT/'broker.token'
    if not token.exists():
        raise FileNotFoundError('Run --init-token once before starting paired processes')
    with (folder/'proxy.log').open('a') as log:
        proxy = subprocess.Popen([sys.executable,'-B',str(ROOT/'serve.py')],cwd=ROOT,stdout=log,stderr=log)
        try:
            for _ in range(100):
                if proxy.poll() is not None:
                    raise RuntimeError('Budget proxy stopped')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health',timeout=1) as stream:
                        assert json.load(stream)['cap']==frozen['new_api_cap_usd']
                    break
                except OSError:
                    time.sleep(.25)
            else:
                raise RuntimeError('Budget proxy unavailable')
            state('review')
            errors = []
            with ThreadPoolExecutor(max_workers=5) as pool:
                encoder = Encoder()
                jobs = {pool.submit(one,p,encoder):p['index'] for p in packets()}
                for job in as_completed(jobs):
                    index = jobs[job]
                    try:
                        print(json.dumps(dict(index=index,added=len(job.result()['added']))),flush=True)
                    except Exception as error:
                        errors.append(index)
                        write(folder/'failures'/f'{index:02d}-{time.time_ns()}.json',
                              dict(error=str(error),traceback=traceback.format_exc()))
            if errors:
                raise RuntimeError(f'Reviewer failed: {errors}')
            state('judge')
            score(ARM)
            state('complete')
        except Exception as error:
            state('failed',error=str(error))
            raise
        finally:
            proxy.terminate()
            proxy.wait(timeout=15)


if __name__=='__main__':
    if '--freeze' in sys.argv:
        freeze()
    elif '--init-token' in sys.argv:
        token = ROOT/'broker.token'
        with os.fdopen(os.open(token,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
            stream.write(secrets.token_urlsafe(32))
    else:
        main()
