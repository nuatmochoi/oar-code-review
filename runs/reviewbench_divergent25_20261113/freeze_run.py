from functools import cache
import json
from pathlib import Path
import shutil

from experiment import ARMS, MODEL, PREV, ROOT, discovery_request, freeze_packet, load, sha, tokens, write


@cache
def checked(path, stamp, expected):
    if sha(path) != expected:
        raise ValueError(f'Frozen input changed: {path}')


def verify():
    frozen = load(ROOT/'freeze.json')
    for p,h in frozen['hashes'].items():
        st = Path(p).stat()
        checked(p,(st.st_size,st.st_mtime_ns,st.st_ctime_ns),h)
    return frozen


def freeze():
    assert not (ROOT/'freeze.json').exists()
    assert not (ROOT/'api').exists()
    upstream = ROOT/'upstream'
    upstream.mkdir()
    for name in ('package.json','package-lock.json','scripts','schema','corpus','golden','node_modules'):
        (upstream/name).symlink_to((PREV/'upstream'/name).resolve())
    (ROOT/'judge-repos').symlink_to((PREV/'judge-repos').resolve())
    shutil.copytree(PREV/'judge-config',ROOT/'judge-config')
    config = ROOT/'judge-config/models.json'
    data = load(config)
    for row in data['providers'].values():
        row['baseUrl'] = row['baseUrl'].replace(':8795/', ':8796/')
    config.write_text(json.dumps(data,indent=2))
    for name in ('budget_proxy.py','recovery_guard.py'):
        shutil.copy2(PREV/name,ROOT/name)
    index = []
    for i in range(25):
        p = freeze_packet(i)
        counts = [tokens(discovery_request(p,a,j)) for a in ARMS for j in (0,1)]
        assert max(counts) * 3 + 3 * 4096 + 12000 < 200000
        g, d = (discovery_request(p,a,0) for a in ARMS)
        for x in (g,d):
            assert x['input'][1]['content'].startswith(p['public_source'])
            x['input'][1]['content'] = p['public_source']
            x.pop('metadata')
        assert g == d
        write(ROOT/'public'/f'{i:02d}.json',p)
        index.append(dict(index=i,key=p['key'],coverage=p['coverage'],max_discovery_ref_tokens=max(counts)))
    assert len({r['key'] for r in index}) == 25
    write(ROOT/'preflight.json',dict(PRs=index,total_ref_input_for_6_calls=sum(x['max_discovery_ref_tokens'] for x in index)*6,
                                     identical_source_and_schema=True,private_gold_reviewer_input=False))
    files = [*ROOT.glob('*.py'),ROOT/'PROTOCOL_KO.md',ROOT/'preflight.json',config,
             ROOT/'judge-config/bin/fd',*ROOT.glob('public/*.json'),*PREV.glob('vendor/*.py'),
             *upstream.glob('scripts/**/*.ts'),*upstream.glob('schema/*.*'),
             *upstream.glob('golden/*.json'),upstream/'corpus/manifest.json',upstream/'package-lock.json',upstream/'package.json']
    frozen = dict(arms=ARMS, PRs=25, model=MODEL, total_usd_cap=70, hashes={str(p):sha(p) for p in files if p.is_file()})
    write(ROOT/'freeze.json',frozen)
    print('Frozen files',len(frozen['hashes']))


if __name__ == '__main__':
    freeze()
