"""Run each additive arm with a frozen report prefix and the official scorer."""

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

from additions import CONTROL, FIXED, PREVIOUS, RETRIEVAL, fallback, historical, one, proposal_request
from adaptive import source_index
from breadth import expand
from budget_proxy import estimated_cost
from experiment import ARM_PORTS, PORT, PREV, ROOT, load, sha, tokens, write
from freeze_run import verify
from merge import packets
from pipeline import score


ARM = os.getenv('REVIEWBENCH_FALLBACK_ARM','BREADTH')


def freeze():
    control = load(CONTROL/'freeze.json')
    previous = load(PREVIOUS/'freeze.json')
    assert control['new_api_cap_usd']==previous['new_api_cap_usd']
    history = load(CONTROL/'summary.json')['costs']
    shared = history['by_phase']['review']['known_usd']
    checks, inputs = [], []
    reused_review_usd = 0.
    for packet in packets():
        key = packet['key']
        path = FIXED/'findings'/f'{key}.json'
        base = load(path)
        assert base['pr']==packet['pr'] and len(base['findings'])<=6
        if len(base['findings'])<6:
            recorded = load(PREVIOUS/'arms/SEMANTIC/review'/f'{packet["index"]:02d}'/'complete.json')
            reused_review_usd += sum(estimated_cost(usage)['usd_list_rate_estimate'] for usage in recorded['usage'])
        _, entries = source_index(packet)
        checks.append(dict(index=packet['index'],key=key,reports=len(base['findings']),source={}))
        inputs.append(path)
        trace = RETRIEVAL/'arms/SEMANTIC/review'/f'{packet["index"]:02d}'/'questions.json'
        inputs.extend((trace,PREV/'public'/f'{packet["index"]:02d}.json'))
        retrieved = fallback('SEMANTIC',packet)
        wide = expand(packet,retrieved,load(historical(packet)/'questions.json'),base['findings'])
        write(ROOT/'retrieval'/f'{packet["index"]:02d}.json',wide)
        assert set(retrieved['targets'])<=set(wide['targets'])
        for arm in ARM_PORTS:
            retrieval = retrieved if arm=='CAP' else wide
            assert set(retrieval['targets'])=={sid for q in retrieval['receipts'] for sid in q['selected']}
            assert all(i in entries or i.startswith('X') for i in retrieval['targets'])
            assert tokens(retrieval['source'])<=retrieval['tokens']<=6500
            can_propose = len(base['findings'])>=6 if arm=='CAP' else len(base['findings'])<6
            checks[-1]['source'][arm] = dict(source_tokens=retrieval['tokens'],
                additions_allowed=bool(retrieval['targets'] and can_propose),
                targets=len(retrieval['targets']),new_diff_hunks=retrieval.get('extra_blocks',0),
                outside_packet=retrieval.get('outside_packet',0))
            if checks[-1]['source'][arm]['additions_allowed']:
                body = proposal_request(packet,base['findings'],retrieval,improve=arm=='CANDIDATES')
                checks[-1]['source'][arm]['proposal_sha256'] = sha_json(body)
    assert sum(p['reports'] for p in checks)==79
    assert sum(p['source']['CAP']['additions_allowed'] for p in checks)==5
    assert sum(p['source']['BREADTH']['additions_allowed'] for p in checks)>=17
    write(ROOT/'preflight.json',dict(PRs=checks))
    local = [*ROOT.glob('*.py'),ROOT/'seed_checkpoint.mts',ROOT/'PROTOCOL_KO.md',ROOT/'preflight.json',
             *ROOT.glob('retrieval/*.json')]
    old = PREVIOUS/'arms/SEMANTIC'
    (ROOT/'upstream').mkdir()
    (ROOT/'upstream/scripts').symlink_to((old/'upstream/scripts').resolve())
    for arm, port in ARM_PORTS.items():
        folder = ROOT/'arms'/arm
        prior = old
        upstream = folder/'upstream'
        upstream.mkdir(parents=True)
        for name in ('package.json','package-lock.json','scripts','schema','corpus','golden','node_modules'):
            (upstream/name).symlink_to((prior/'upstream'/name).resolve())
        shutil.copytree(prior/'judge-config',folder/'judge-config')
        config = folder/'judge-config/models.json'
        data = load(config)
        for provider in data['providers'].values():
            provider['baseUrl'] = f'http://127.0.0.1:{port}/judge/v1'
        config.write_text(json.dumps(data,indent=2))
        shutil.copytree(prior/'judge-repos',folder/'judge-repos',symlinks=True)
        local += [config,folder/'judge-config/bin/fd']
    older = [CONTROL/'summary.json',CONTROL/'RESULTS_KO.md',RETRIEVAL/'summary.json',RETRIEVAL/'RESULTS_KO.md',
             PREVIOUS/'RESULTS_KO.md',PREVIOUS/'summary.json',*old.glob('scoring/results*.json'),
             *old.glob('findings/*.json'),*old.glob('review/*/complete.json'),
             *FIXED.glob('scoring/results*.json'),*FIXED.glob('review/*/questions.json')]
    hashes = previous['hashes'] | {str(path):sha(path) for path in local+older+inputs}
    write(ROOT/'freeze.json',dict(prs=25,arms=list(ARM_PORTS),new_api_cap_usd=control['new_api_cap_usd'],
                                  shared_cost={arm:shared+(reused_review_usd if arm=='CAP' else 0.)
                                               for arm in ARM_PORTS},hashes=hashes))
    verify()
    print(f'Frozen {len(hashes)} files, 25 PRs and 79 preserved reports',flush=True)


def sha_json(value):
    import hashlib
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


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
            failed = []
            with ThreadPoolExecutor(max_workers=5) as pool:
                jobs = {pool.submit(one,ARM,p):p['index'] for p in packets()}
                for job in as_completed(jobs):
                    index = jobs[job]
                    try:
                        row = job.result()
                        print(json.dumps(dict(index=index,added=len(row['added']),skip=row['skip'])),flush=True)
                    except Exception as exc:
                        failed.append(index)
                        write(folder/'failures'/f'{index:02d}-{time.time_ns()}.json',
                              dict(error=str(exc),traceback=traceback.format_exc()))
            if failed:
                raise RuntimeError(f'Reviewer failed: {failed}')
            state('judge')
            scoring = folder/'scoring'
            scoring.mkdir(exist_ok=True)
            if not any(scoring.glob('results*.json')):
                with (scoring/'seed.log').open('x') as seed_log:
                    subprocess.run([str(folder/'upstream/node_modules/.bin/tsx'),str(ROOT/'seed_checkpoint.mts'),
                                    str(folder),str(PREVIOUS/'arms/SEMANTIC'),str(FIXED)],
                                   cwd=ROOT,stdout=seed_log,stderr=seed_log,check=True)
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
        path = ROOT/'broker.token'
        with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
            stream.write(secrets.token_urlsafe(32))
    else:
        main()
