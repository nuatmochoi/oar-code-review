from pathlib import Path
import concurrent.futures,json,subprocess,time
from experiment import ROOT,IMAGE,load,write

def one(packet,outbase=None):
    outbase=Path(outbase) if outbase else ROOT/'native'
    dest=outbase/f'{packet["index"]:02d}'
    if (dest/'complete.json').exists():return load(dest/'complete.json')
    if (dest/'started.json').exists():raise RuntimeError('Incomplete native attempt preserved: '+str(dest))
    dest.mkdir(parents=True,exist_ok=True)
    questions=load(ROOT/'plans/current'/f'{packet["index"]:02d}'/'validation.json')['valid_questions']
    write(dest/'input.json',dict(packet=packet,questions=questions))
    write(dest/'started.json',dict(time=time.time()))
    repo=ROOT/'native-repos'/packet['pr']['nwo'].replace('/','_')
    cmd=['docker','run','--rm','--platform','linux/amd64','--network','none',
         '-v',str(repo.resolve())+':/work/repo:ro','-v',str(dest.resolve())+':/work/out/agent',
         '-v',str(ROOT)+':/experiment:ro','-e','REVIEW_RUN_ROOT=/work/out/agent',
         '--entrypoint','python',IMAGE,'/experiment/native_collect.py']
    with (dest/'docker.log').open('x') as log:
        result=subprocess.run(cmd,stdout=log,stderr=log,timeout=1950)
    write(dest/'exit.json',dict(command=cmd,returncode=result.returncode))
    if result.returncode:raise RuntimeError('Native failed '+str(dest))
    r=load(dest/'complete.json');print(json.dumps(dict(event='native_completed',index=packet['index'],**r)),flush=True);return r

def main():
    from freeze_run import verify
    verify()
    packets=[load(r['path']) for r in load(ROOT/'public/manifest.json')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(one,p):p['index'] for p in packets}
        errors=[]
        for f in concurrent.futures.as_completed(futures):
            try:f.result()
            except Exception as e:errors.append(dict(index=futures[f],error=str(e)))
    write(ROOT/'audit/collection.json',dict(completed=25-len(errors),errors=errors))
    if errors:raise RuntimeError(errors)
if __name__=='__main__':main()
