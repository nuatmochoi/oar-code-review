"""Freeze and execute the paired-candidate union on the 25 historical PRs."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

from budget_proxy import estimated_cost
from experiment import PORT, ROOT, load, sha, write
from freeze_run import verify
from merge import packets
from pipeline import score
from union_review import CONTROL, FIXED, PREVIOUS, SOURCES, one, prepare


ARM = ROOT/'arms/UNION'


def freeze():
    previous = load(PREVIOUS/'freeze.json')
    baseline = load(FIXED.parents[1]/'summary.json')
    shared = baseline['costs']['by_phase']['review']['known_usd'] if 'by_phase' in baseline['costs'] else None
    if shared is None:
        prior = load(CONTROL.parents[1]/'summary.json')['costs']['by_phase']['review']['known_usd']
        shared = prior+baseline['costs']['SEMANTIC']['review_usd']
    components = dict(fixed_review=shared)
    for arm in SOURCES:
        proposals = (PREVIOUS/'arms'/arm/'review').glob('*/verify/response.json')
        components[arm+'_candidate_generation'] = sum(
            estimated_cost(load(path)['usage'])['usd_list_rate_estimate'] for path in proposals)
    inputs, rows = [], []
    for packet in packets():
        index = packet['index']
        base,candidates,bindings,origins,decisions,cap,body = prepare(packet)
        inputs.append(PREVIOUS/'retrieval'/f'{index:02d}.json')
        for arm in SOURCES:
            origin = PREVIOUS/'arms'/arm/'review'/f'{index:02d}'
            inputs += [origin/'complete.json',*origin.glob('verify/parsed.json'),
                       *origin.glob('verify/recovered_prefix.json'),*origin.glob('verify/response.json')]
        if body:
            write(ROOT/'requests'/f'{index:02d}.json',body)
        rows.append(dict(index=index,key=packet['key'],fixed=len(base['findings']),
                         candidates=len(candidates),origin_count=sum(map(len,origins.values())),
                         exact_filters=decisions,cap=cap,verification=bool(body)))
    assert len(rows)==25 and sum(row['fixed'] for row in rows)==81
    write(ROOT/'preflight.json',dict(PRs=rows,cost_components=components))
    upstream = ARM/'upstream'
    upstream.mkdir(parents=True)
    for name in ('package.json','package-lock.json','scripts','schema','corpus','golden','node_modules'):
        (upstream/name).symlink_to((FIXED/'upstream'/name).resolve())
    (ROOT/'upstream').mkdir()
    (ROOT/'upstream/scripts').symlink_to((upstream/'scripts').resolve())
    shutil.copytree(FIXED/'judge-config',ARM/'judge-config')
    config = ARM/'judge-config/models.json'
    data = load(config)
    for provider in data['providers'].values():
        provider['baseUrl'] = f'http://127.0.0.1:{PORT}/judge/v1'
    config.write_text(json.dumps(data,indent=2))
    shutil.copytree(FIXED/'judge-repos',ARM/'judge-repos',symlinks=True)
    inputs += [*ROOT.glob('*.py'),*ROOT.glob('*.mts'),*ROOT.glob('requests/*.json'),ROOT/'preflight.json',
               ROOT/'PROTOCOL_KO.md',config,ARM/'judge-config/bin/fd',PREVIOUS/'summary.json',
               PREVIOUS/'RESULTS_KO.md',FIXED.parents[1]/'summary.json']
    for root in (FIXED,CONTROL,*(PREVIOUS/'arms'/name for name in SOURCES)):
        inputs += [*root.glob('findings/*.json'),*root.glob('scoring/results*.json')]
    write(ROOT/'freeze.json',dict(PRs=25,new_api_cap_usd=previous['new_api_cap_usd'],
                                  shared_cost={'UNION':sum(components.values())},
                                  hashes=previous['hashes']|{str(path):sha(path) for path in inputs}))
    frozen = verify()
    print(dict(frozen=len(frozen['hashes']),verify_PRs=sum(row['verification'] for row in rows),
               candidates=sum(row['candidates'] for row in rows),shared=frozen['shared_cost']['UNION']),flush=True)


def state(phase, **extra):
    temporary = ARM/'status.tmp'
    row = dict(phase=phase,**extra)
    temporary.write_text(json.dumps(row))
    temporary.replace(ARM/'status.json')
    print(row,flush=True)


def main():
    frozen = verify()
    os.environ['REVIEWBENCH_FALLBACK_ARM'] = 'UNION'
    with (ARM/'proxy.log').open('a') as log:
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
            with ThreadPoolExecutor(max_workers=5) as pool:
                for row in pool.map(one,packets()):
                    print(dict(index=row['index'],added=len(row['added'])),flush=True)
            state('judge')
            scoring = ARM/'scoring'
            scoring.mkdir(exist_ok=True)
            if not any(scoring.glob('results*.json')):
                with (scoring/'seed.log').open('x') as seed_log:
                    subprocess.run([str(ARM/'upstream/node_modules/.bin/tsx'),str(ROOT/'seed_checkpoint.mts'),
                                    str(ARM),str(CONTROL),str(FIXED),
                                    *(str(PREVIOUS/'arms'/arm) for arm in SOURCES)],
                                   cwd=ROOT,stdout=seed_log,stderr=seed_log,check=True)
            score('UNION')
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
    else:
        token = ROOT/'broker.token'
        if not token.exists():
            with os.fdopen(os.open(token,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
                stream.write(secrets.token_urlsafe(32))
        main()
