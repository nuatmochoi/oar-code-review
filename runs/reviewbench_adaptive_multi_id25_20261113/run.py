from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import secrets
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request

from adaptive import combine, review_packet, source_index, preview, lookup, resolve_targets
from experiment import ROOT, PORT, tokens, load, sha, write
from freeze_run import verify
from merge import BASE, PRIOR, merge_input, merge_request, packets
from pipeline import score, state

PREVIOUS = ROOT.parent/'reviewbench_general_add25_20261113'
BEFORE = ROOT.parent/'reviewbench_adaptive_add25_20261113'


def freeze():
    parent = load(BEFORE/'freeze.json')
    for path, expected in parent['hashes'].items():
        assert sha(path)==expected, path
    assert not (ROOT/'freeze.json').exists() and not (ROOT/'api').exists()
    upstream = ROOT/'upstream'
    upstream.mkdir(exist_ok=True)
    for name in ('package.json','package-lock.json','scripts','schema','corpus','golden','node_modules'):
        (upstream/name).symlink_to((PRIOR/'upstream'/name).resolve())
    assert (ROOT/'judge-repos').is_dir() and not (ROOT/'judge-repos').is_symlink()
    shutil.copytree(BEFORE/'judge-config',ROOT/'judge-config')
    config = ROOT/'judge-config/models.json'
    data = load(config)
    for provider in data['providers'].values():
        provider['baseUrl'] = provider['baseUrl'].replace(':8798/',f':{PORT}/')
    config.write_text(json.dumps(data,indent=2))
    checks = []
    for packet in packets():
        i = packet['index']
        prefix, entries = source_index(packet)
        sample, ids = preview(entries)
        candidates = [{'targets':[identifier],'perspective':'other','question':'Inspect source'} for identifier in list(entries)[:10]]
        fetched = lookup(candidates,entries,1)
        assert set(fetched['targets']) <= set(entries)
        assert tokens(fetched['source'])<=6500
        assert tokens(sample)<=5500
        assert tokens(packet['public_source']) + 3*tokens(prefix) + 3*tokens(list(entries)) + 65000 < 200000
        base, cap, eligible = merge_input(packet,'DIVERGENT')
        if eligible and cap:
            old = PRIOR/'arms/ADD/review'/f'{i:02d}'/'verify/request.json'
            assert merge_request(packet,base['findings'],eligible)==load(old)
        checks.append(dict(index=i,previews=len(ids),indexed_hunks=packet['coverage']['supplied_hunks'],
                           indexed_bodies=packet['coverage']['supplied_bodies'],merge_request_matches=True))
    test_entries = {'H012':{'text':'first'},'H018':{'text':'second'},'B003':{'text':'third'}}
    assert resolve_targets(['H012','H018 src/parser.py','H012, B003','H999','src/H012.py'],test_entries) == {
        'H012':True,'H018':True,'B003':True,'H999':False,'src/H012.py':False}
    question = dict(perspective='contract',targets=['H012','H018 src/parser.py','H999'],question='Compare both sides')
    receipt = lookup([question],test_entries,2,already_visible=['H012'])['receipts'][0]
    assert [r['result'] for r in receipt['target_results']]==['already_visible','delivered','unknown_target']
    write(ROOT/'preflight.json',dict(PRs=checks,multi_id_lookup_check=True))
    prior_cost = load(BEFORE/'summary.json')['costs']['cumulative_upper_usd']
    files = [*ROOT.glob('*.py'),ROOT/'PROTOCOL_KO.md',ROOT/'preflight.json',config,ROOT/'judge-config/bin/fd',
             PREVIOUS/'summary.json',PREVIOUS/'freeze.json',BEFORE/'summary.json',BEFORE/'freeze.json',
             BEFORE/'RESULTS_KO.md',*BEFORE.glob('arms/ADD/scoring/results*.json'),
             *BEFORE.glob('arms/ADD/findings/*.json'),*BEFORE.glob('arms/ADD/review/*/questions.json'),
             *BEFORE.glob('arms/ADD/review/*/complete.json'),
             *BEFORE.glob('arms/ADD/review/*/plan/parsed.json'),*BEFORE.glob('arms/ADD/review/*/update/parsed.json')]
    write(ROOT/'freeze.json',dict(PRs=25,prior_api_usd=prior_cost,cumulative_cap_usd=70,
                                  new_api_cap_usd=70-prior_cost,
                                  hashes=parent['hashes']|{str(f):sha(f) for f in files}))
    print('Frozen source lookup inventory and replayed merge requests for all 25 PRs',flush=True)


def one(packet):
    index = packet['index']
    folder = ROOT/'arms/ADD/review'/f'{index:02d}'
    complete = folder/'complete.json'
    if complete.exists():
        return load(complete)
    base = load(BASE/f'{packet["key"]}.json')
    assert base['pr'] == packet['pr']
    cap = min(3,max(0,6-len(base['findings'])))
    trace = folder/'questions.json'
    if trace.exists():
        review = load(trace)
    else:
        review, _ = review_packet(packet,folder)
        write(trace,review)
    result = combine(packet,base,folder,review['candidate_ledger'],cap)
    write(complete,result)
    return result


def main():
    frozen = verify()
    token = ROOT/'broker.token'
    if not token.exists():
        token.touch(mode=0o600)
        token.write_text(secrets.token_urlsafe(32))
    with (ROOT/'proxy.log').open('a') as log:
        proxy = subprocess.Popen([sys.executable,'-B',str(ROOT/'serve.py')],cwd=ROOT,stdout=log,stderr=log)
        try:
            for _ in range(40):
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
            failures = []
            with ThreadPoolExecutor(max_workers=8) as pool:
                jobs = {pool.submit(one,p):p['index'] for p in packets()}
                for job in as_completed(jobs):
                    index = jobs[job]
                    try:
                        result = job.result()
                        print(json.dumps(dict(index=index,added=len(result['added']))),flush=True)
                    except Exception as error:
                        failures.append(index)
                        write(ROOT/'failures'/f'{index:02d}-{time.time_ns()}.json',
                              dict(error=str(error),traceback=traceback.format_exc()))
            if failures:
                raise RuntimeError(f'Failed PR indices: {failures}')
            state('judge')
            score('ADD')
            state('complete')
        except Exception as error:
            state('failed',error=str(error))
            raise
        finally:
            proxy.terminate()
            proxy.wait(timeout=15)


if __name__ == '__main__':
    if '--freeze' in sys.argv:
        freeze()
    else:
        main()
