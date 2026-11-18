import concurrent.futures,json,os,secrets,subprocess,sys,time,traceback,urllib.request
from experiment import ROOT,ARMS,load,write,finalize
from freeze_run import verify
def state(phase,**kw):
    (ROOT/'status.json').write_text(json.dumps(dict(phase=phase,time=time.time(),pid=os.getpid(),**kw),indent=2))
def ids():return {p.parent.name for p in (ROOT/'api').glob('*/admission.json')}
def score(arm):
    out=ROOT/'arms'/arm;dest=out/'scoring';dest.mkdir(exist_ok=True)
    if (dest/'results.json').exists():return
    assert len(list((out/'findings').glob('*.json')))==25
    token=(ROOT/'broker.token').read_text().strip()
    env={**os.environ,'PI_CODING_AGENT_DIR':str(ROOT/'judge-config'),'REVIEW_BROKER_TOKEN':token,
         'OPENAI_API_KEY':token,'REVIEW_BENCH_JUDGE_TIMEOUT_MS':'1800000'}
    for k in ['OPENAI_PROJECT_ID','OPENAI_ORG_ID','ANTHROPIC_API_KEY']:env.pop(k,None)
    command=['npm','run','judge','--','--candidate',str(out/'findings'),'--golden',str(ROOT/'upstream/golden'),
        '--manifest',str(ROOT/'upstream/corpus/manifest.json'),'--provider','openai','--model','gpt-5.6-sol',
        '--output',str(dest/'results.json'),'--repo-dir',str(ROOT/'judge-repos'),'--concurrency','3']
    before=ids();write(dest/'command.json',dict(command=command,before_api_ids=sorted(before)))
    with (dest/'judge.log').open('x')as f:r=subprocess.run(command,cwd=ROOT/'upstream',env=env,stdout=f,stderr=f)
    for _ in range(30):
        if not[p for p in (ROOT/'api').glob('*/admission.json')if not(p.parent/'receipt.json').exists()]:break
        time.sleep(.5)
    write(dest/'exit.json',dict(returncode=r.returncode,api_ids=sorted(ids()-before)))
    if r.returncode:raise RuntimeError('Official strict scorer failed: '+arm)
def main():
    verify()
    token=ROOT/'broker.token'
    if not token.exists():token.write_text(secrets.token_urlsafe(32));token.chmod(0o600)
    log=(ROOT/'audit/proxy.log').open('a')
    guard=subprocess.Popen([sys.executable,'-B',str(ROOT/'recovery_guard.py')],stdout=log,stderr=log,cwd=ROOT)
    try:
        for _ in range(40):
            try:urllib.request.urlopen('http://127.0.0.1:8795/health',timeout=1).read();break
            except Exception:time.sleep(.5)
        else:raise RuntimeError('Budget proxy not ready')
        packets=[load(r['path'])for r in load(ROOT/'public/manifest.json')]
        packets.sort(key=lambda p:len(p['case']['diff']),reverse=True)
        for arm in ARMS:
            state('review',arm=arm);errors=[]
            with concurrent.futures.ThreadPoolExecutor(max_workers=3)as pool:
                futures={pool.submit(finalize,p,arm):p['index']for p in packets}
                for f in concurrent.futures.as_completed(futures):
                    try:f.result()
                    except Exception as e:
                        v=dict(index=futures[f],arm=arm,error=str(e),traceback=traceback.format_exc())
                        write(ROOT/'arms'/arm/'failures'/f'{futures[f]:02d}.json',v);errors.append(v);print(json.dumps(v),flush=True)
            if errors:raise RuntimeError(f'{arm}: {len(errors)} reviewer failures; preserved')
            state('judge',arm=arm);score(arm)
            from report import update
            result=update();print(json.dumps(dict(event='arm_completed',arm=arm,result=result['arms'][arm])),flush=True)
        state('complete')
    except Exception as e:state('failed',error=str(e));raise
    finally:
        guard.terminate();guard.wait(timeout=15);log.close()
if __name__=='__main__':main()
