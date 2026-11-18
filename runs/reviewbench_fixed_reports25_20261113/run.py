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

from additions import CONTROL, FIXED, RETRIEVAL, fallback, one, proposal_request
from adaptive import source_index
from experiment import ARM_PORTS, PORT, ROOT, load, sha, tokens, write
from freeze_run import verify
from merge import packets
from pipeline import score


ARM = os.getenv('REVIEWBENCH_FALLBACK_ARM','SEMANTIC')


def freeze():
    control = load(CONTROL/'freeze.json')
    previous = load(RETRIEVAL/'freeze.json')
    assert control['new_api_cap_usd']==previous['new_api_cap_usd']
    history = load(CONTROL/'summary.json')['costs']
    shared = history['by_phase']['review']['known_usd']
    checks, inputs = [], []
    for packet in packets():
        key = packet['key']
        path = FIXED/'findings'/f'{key}.json'
        base = load(path)
        assert base['pr']==packet['pr'] and len(base['findings'])<=6
        _, entries = source_index(packet)
        checks.append(dict(index=packet['index'],key=key,reports=len(base['findings']),source={}))
        inputs.append(path)
        for arm in ARM_PORTS:
            trace = RETRIEVAL/'arms'/arm/'review'/f'{packet["index"]:02d}'/'questions.json'
            inputs.append(trace)
            retrieval = fallback(arm,packet)
            assert set(retrieval['targets'])=={sid for q in retrieval['receipts'] for sid in q['selected']}
            assert set(retrieval['targets'])<=set(entries)
            assert tokens(retrieval['source'])<=retrieval['tokens']<=6500
            checks[-1]['source'][arm] = dict(source_tokens=retrieval['tokens'],
                additions_allowed=bool(retrieval['targets'] and len(base['findings'])<6))
            if checks[-1]['source'][arm]['additions_allowed']:
                body = proposal_request(packet,base['findings'],retrieval)
                checks[-1]['source'][arm]['proposal_sha256'] = sha_json(body)
    assert sum(p['reports'] for p in checks)==79
    assert all(sum(p['source'][a]['additions_allowed'] for p in checks)==17 for a in ARM_PORTS)
    write(ROOT/'preflight.json',dict(PRs=checks))
    local = [*ROOT.glob('*.py'),ROOT/'PROTOCOL_KO.md',ROOT/'preflight.json']
    for arm, port in ARM_PORTS.items():
        folder = ROOT/'arms'/arm
        prior = RETRIEVAL/'arms'/arm
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
             *FIXED.glob('scoring/results*.json'),*FIXED.glob('review/*/questions.json')]
    hashes = previous['hashes'] | {str(path):sha(path) for path in local+older+inputs}
    write(ROOT/'freeze.json',dict(prs=25,arms=list(ARM_PORTS),new_api_cap_usd=control['new_api_cap_usd'],
                                  shared_cost={arm:shared for arm in ARM_PORTS},hashes=hashes))
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
