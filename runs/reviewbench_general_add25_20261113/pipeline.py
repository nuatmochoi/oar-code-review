from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import secrets
import subprocess
import sys
import time
import traceback
import urllib.request

from experiment import ARMS, MODEL, PORT, ROOT, load, run_one, write
from freeze_run import verify


def state(phase, **kw):
    value = dict(phase=phase,pid=os.getpid(),time=time.time(),**kw)
    tmp = ROOT/'status.tmp'
    tmp.write_text(json.dumps(value,indent=2))
    tmp.replace(ROOT/'status.json')
    print(json.dumps(value),flush=True)


def score(arm):
    dest = ROOT/'arms'/arm/'scoring'
    dest.mkdir(exist_ok=True)
    results = dest/'results.json'
    if results.exists():
        assert load(results)['pr_count']==25
        return
    candidates = dest.parent/'findings'
    assert len(list(candidates.glob('*.json')))==25
    token = (ROOT/'broker.token').read_text().strip()
    env = dict(os.environ,PI_CODING_AGENT_DIR=str(ROOT/'judge-config'),
               REVIEW_BROKER_TOKEN=token,OPENAI_API_KEY=token,REVIEW_BENCH_JUDGE_TIMEOUT_MS='1800000')
    for name in ('OPENAI_PROJECT_ID','OPENAI_ORG_ID','ANTHROPIC_API_KEY'):
        env.pop(name,None)
    cmd=['npm','run','judge','--','--candidate',str(candidates),'--golden',str(ROOT/'upstream/golden'),
         '--manifest',str(ROOT/'upstream/corpus/manifest.json'),'--provider','openai','--model',MODEL,
         '--output',str(results),'--repo-dir',str(ROOT/'judge-repos'),'--concurrency','4']
    n=1+len(list(dest.glob('judge-*.log')))
    write(dest/f'command-{n}.json',cmd)
    with (dest/f'judge-{n}.log').open('x') as log:
        proc=subprocess.run(cmd,cwd=ROOT/'upstream',env=env,stdout=log,stderr=log)
    write(dest/f'exit-{n}.json',dict(returncode=proc.returncode))
    if proc.returncode:raise RuntimeError(f'Strict official scoring failed: {arm}')
    assert load(results)['pr_count']==25


def main():
    verify()
    token = ROOT/'broker.token'
    if not token.exists():
        token.touch(mode=0o600)
        token.write_text(secrets.token_urlsafe(32))
    with (ROOT/'proxy.log').open('a') as log:
        proxy = subprocess.Popen([sys.executable,'-B',str(ROOT/'serve.py')],cwd=ROOT,stdout=log,stderr=log)
        try:
            for _ in range(50):
                if proxy.poll() is not None:raise RuntimeError('Budget guard failed to start')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health',timeout=1) as response:
                        assert json.load(response)['cap']==70
                    break
                except OSError: time.sleep(.2)
            else: raise RuntimeError('Budget guard did not become ready')
            packets=sorted((load(p) for p in (ROOT/'public').glob('*.json')),key=lambda p:len(p['public_source']),reverse=True)
            assert len(packets)==25
            state('review')
            errors=[]
            with ThreadPoolExecutor(max_workers=8) as pool:
                pending={pool.submit(run_one,p,a):(a,p['index']) for p in packets for a in ARMS}
                for f in as_completed(pending):
                    a,i=pending[f]
                    try:
                        value=f.result()
                        print(json.dumps(dict(arm=a,index=i,candidates=value['raw_candidates'],findings=len(value['kept']))),flush=True)
                    except Exception as e:
                        errors.append((a,i))
                        write(ROOT/'arms'/a/'failures'/f'{i:02d}-{time.time_ns()}.json',dict(error=str(e),traceback=traceback.format_exc()))
            if errors: raise RuntimeError(f'Reviewer failures {errors}')
            for a in ARMS:
                state('judge',arm=a)
                score(a)
            state('complete')
        except Exception as e:
            state('failed',error=str(e))
            raise
        finally:
            proxy.terminate()
            proxy.wait(timeout=20)


if __name__=='__main__':main()
