from pathlib import Path
import hashlib,json,time
ROOT=Path(__file__).resolve().parent
CACHE={}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def verify():
    f=json.loads((ROOT/'freeze.json').read_text())
    for p,h in f['files'].items():
        st=Path(p).stat();stamp=(st.st_size,st.st_mtime_ns,st.st_ctime_ns)
        if CACHE.get(p)!=stamp:
            if sha(p)!=h:raise RuntimeError('Frozen file changed: '+p)
            CACHE[p]=stamp
    return f
def create():
    assert not(ROOT/'freeze.json').exists()
    assert not list((ROOT/'api').glob('*/request.json'))
    files=[*ROOT.glob('*.py'),ROOT/'PROTOCOL_KO.md',ROOT/'judge-config/models.json',
           *list((ROOT/'vendor').rglob('*.py')),*list((ROOT/'public').glob('*.json')),
           *list((ROOT/'plans/current').glob('*/*.json')),
           *list((ROOT/'upstream/scripts').rglob('*.ts')),
           *list((ROOT/'upstream/golden').glob('*.json')),ROOT/'upstream/package-lock.json',
           ROOT/'upstream/corpus/manifest.json',ROOT/'audit/preflight.json']
    f=dict(date='2026-11-13',frozen_at=time.time(),files={str(p.resolve()):sha(p)for p in files},
           arms=['FLAT','PATH','GROUPED'],repeats=1,model='gpt-5.6-sol',reasoning='low',PRs=25,
           model_token_cap=750000,logical_model_calls=2,physical_final_calls_per_PR=1,
           source_and_relation_cap=12000,native_calls=64,native_seconds=240,file_reads=256,
           hard_total_upper_usd=70,workers=3,human_review=0)
    with (ROOT/'freeze.json').open('x')as out:json.dump(f,out,indent=2)
    return f
if __name__=='__main__':
    import sys
    f=verify()if '--verify'in sys.argv else create()
    print(json.dumps(dict(frozen=True,files=len(f['files']))))
