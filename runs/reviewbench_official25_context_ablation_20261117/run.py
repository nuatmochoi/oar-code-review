"""Parallel fresh review and official judging with a shared durable dollar guard."""
from concurrent.futures import ThreadPoolExecutor,as_completed
import json,os,secrets,subprocess,sys,time,traceback,urllib.request
from config import *
from experiment import load,write,run_one
from freeze_run import verify

def state(phase,**extra):
    row=dict(phase=phase,pid=os.getpid(),time=time.time(),**extra)
    tmp=ROOT/'status.tmp';tmp.write_text(json.dumps(row,indent=2));tmp.replace(ROOT/'status.json')
    print(json.dumps(row),flush=True)

def score(arm):
    dest=ROOT/'arms'/arm/'scoring';dest.mkdir(exist_ok=True)
    result=dest/'results.json'
    if result.exists():
        assert load(result)['pr_count']==25
        return
    candidates=dest.parent/'findings'
    assert len(list(candidates.glob('*.json')))==25
    token=(ROOT/'broker.token').read_text().strip()
    env=dict(os.environ,PI_CODING_AGENT_DIR=str(dest.parent/'judge-config'),REVIEW_BROKER_TOKEN=token,
             OPENAI_API_KEY=token,REVIEW_BENCH_JUDGE_TIMEOUT_MS='1800000')
    for name in ('OPENAI_PROJECT_ID','OPENAI_ORG_ID','ANTHROPIC_API_KEY'):env.pop(name,None)
    upstream=dest.parent/'upstream'
    cmd=['npm','run','judge','--','--candidate',str(candidates),'--golden',str(upstream/'golden'),
         '--manifest',str(upstream/'corpus/manifest.json'),'--provider','openai','--model',MODEL,
         '--output',str(result),'--repo-dir',str(dest.parent/'judge-repos'),'--concurrency',str(JUDGE_WORKERS_PER_ARM)]
    n=1+len(list(dest.glob('judge-*.log')));write(dest/f'command-{n}.json',cmd)
    with (dest/f'judge-{n}.log').open('x') as log:proc=subprocess.run(cmd,cwd=upstream,env=env,stdout=log,stderr=log)
    write(dest/f'exit-{n}.json',dict(returncode=proc.returncode))
    if proc.returncode:raise RuntimeError(f'Official scoring failed: {arm}')
    assert load(result)['pr_count']==25
    print(json.dumps(dict(scoring_complete=arm)),flush=True)

def main():
    verify()
    token=ROOT/'broker.token'
    if not token.exists():
        with os.fdopen(os.open(token,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:f.write(secrets.token_urlsafe(32))
    with (ROOT/'proxy.log').open('a') as log:
        proxy=subprocess.Popen([sys.executable,'-B',str(ROOT/'budget_proxy.py')],cwd=ROOT,stdout=log,stderr=log)
        try:
            for _ in range(100):
                if proxy.poll() is not None:raise RuntimeError('Budget proxy stopped')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health',timeout=1) as r:
                        assert json.load(r)['cap']==TOTAL_USD
                    break
                except OSError:time.sleep(.2)
            else:raise RuntimeError('Budget proxy unavailable')
            packets=sorted((load(p) for p in (ROOT/'public').glob('[0-9][0-9].json')),key=lambda p:len(p['case']['diff']),reverse=True)
            assert len(packets)==25
            state('review');errors=[];done=0
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                pending={}
                for i,p in enumerate(packets):
                    for a in ARMS[i%3:]+ARMS[:i%3]:pending[pool.submit(run_one,p,a)]=(a,p['index'])
                for future in as_completed(pending):
                    a,i=pending[future]
                    try:future.result();done+=1
                    except Exception as e:
                        errors.append((a,i));write(ROOT/'arms'/a/'failures'/f'{i:02d}-{time.time_ns()}.json',dict(error=str(e),traceback=traceback.format_exc()))
                    state('review',completed=done,failed=errors)
            if errors:raise RuntimeError(f'Reviewer failures: {errors}')
            state('judge')
            with ThreadPoolExecutor(max_workers=3) as pool:
                for future in as_completed([pool.submit(score,a) for a in ARMS]):future.result()
            state('complete')
        except Exception as error:
            state('failed',error=str(error));raise
        finally:
            proxy.terminate();proxy.wait(timeout=30)

if __name__=='__main__':main()
