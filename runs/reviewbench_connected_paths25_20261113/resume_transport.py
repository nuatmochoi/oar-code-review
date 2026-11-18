"""Exact-input transport recovery, outside immutable experiment implementation.

Only HTTP 5xx slots with no response may be retried. Old failures/request/timing
stay immutable; successful response selection and semantic repair are forbidden.
"""
import concurrent.futures,copy,hashlib,json,os,subprocess,sys,time,urllib.request
from pathlib import Path
import experiment as e
from freeze_run import verify
from pipeline import score,state
ROOT=e.ROOT
original_write=e.write;original_load=e.load;original_call=e.call
timing_overrides={}

def same_or_new(path,value):
    path=Path(path)
    if path.exists():
        if original_load(path)!=value:raise RuntimeError('Recovery would overwrite different artifact: '+str(path))
        return
    original_write(path,value)
def recovery_load(path):
    return timing_overrides.get(str(Path(path)),original_load(path))

def recovery_call(body,dest):
    dest=Path(dest)
    if not(dest/'request.json').exists()or(dest/'response.json').exists():
        return original_call(body,dest)
    verify()
    failure=original_load(dest/'failure.json')
    if failure['type']!='HTTPError' or not any('HTTP Error '+str(c)in failure['message']for c in range(500,600)):
        raise RuntimeError('Not an explicitly recoverable server error: '+str(dest))
    actual=copy.deepcopy(body);actual['metadata']={'experiment_slot':str(dest.relative_to(ROOT))}
    saved=original_load(dest/'request.json')
    if actual!=saved:raise RuntimeError('Recovery input mismatch')
    attempt=ROOT/'attempts'/dest.parent.parent.name/dest.name/'transport-retry-1'
    if (attempt/'request.json').exists():raise RuntimeError('Recovery attempt already exists; inspect rather than repeat')
    previous=[]
    for adm in (ROOT/'api').glob('*/admission.json'):
        request=original_load(adm.parent/'request.json')
        if request==saved:previous.append(original_load(adm))
    planner=original_load(ROOT/'plans/current'/dest.name/'response.json')
    # Keep even the failed request's full conservative input/output reservation
    # inside the same per-PR physical token ceiling before issuing a retry.
    prior_upper=sum(x['input_token_upper_bound']+x['max_output_tokens']for x in previous)
    recovery_upper=e.usage_tokens(planner)+prior_upper+int(e.tokens(saved)*1.2)+saved['max_output_tokens']+4096
    if recovery_upper>e.TOKEN_CAP:raise RuntimeError('Recovery physical token upper exceeds common cap')
    original_write(attempt/'request.json',saved)
    original_write(attempt/'authorization.json',dict(reason=failure['message'],input_exactly_identical=True,
        original_request=str(dest/'request.json'),failed_api_ids=[x['id']for x in previous],
        unknown_charge_reservations_retained=True,physical_token_upper_before_retry=recovery_upper,
        common_token_cap=e.TOKEN_CAP,successful_response_replaced=False))
    token=(ROOT/'broker.token').read_text().strip()
    req=urllib.request.Request('http://127.0.0.1:8795/review/v1/responses',data=json.dumps(saved).encode(),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    started=time.monotonic()
    try:
        with urllib.request.urlopen(req,timeout=1200)as response:value=json.load(response)
        original_write(attempt/'response.json',value)
    except Exception as exc:
        original_write(attempt/'failure.json',dict(type=type(exc).__name__,message=str(exc)));raise
    seconds=time.monotonic()-started
    original_write(attempt/'timing.json',dict(seconds=seconds))
    if value.get('status')!='completed':raise RuntimeError('Recovered request nonterminal; preserved')
    text=''.join(c.get('text','')for item in value.get('output',[])if item.get('type')=='message'for c in item.get('content',[])if c.get('type')=='output_text')
    parsed=json.loads(text);e.jsonschema.validate(parsed,saved['text']['format']['schema'])
    original_write(dest/'response.json',value);original_write(dest/'parsed.json',parsed)
    original_write(dest/'transport_recovery.json',dict(attempt=str(attempt),identical_model_input=True,
        original_failure_preserved=True,original_timing_preserved=True,new_seconds=seconds,
        physical_token_upper_including_retry=prior_upper+e.usage_tokens(planner)+e.usage_tokens(value)))
    timing_overrides[str(dest/'timing.json')]=dict(seconds=seconds+original_load(dest/'timing.json')['seconds'])
    return value

def main():
    verify()
    # Do not race a running original pipeline or replace active requests.
    status=original_load(ROOT/'status.json')
    if status['phase']!='failed':raise RuntimeError('Original pipeline must first terminate in failed state')
    if any(not(p.parent/'receipt.json').exists()for p in(ROOT/'api').glob('*/admission.json')):
        raise RuntimeError('Original API request still unsettled')
    e.write=same_or_new;e.load=recovery_load;e.call=recovery_call
    log=(ROOT/'audit'/f'proxy.resume-{time.time_ns()}.log').open('x')
    code="import budget_proxy as b; from recovery_guard import QueuedBudget; s=b.Server(('0.0.0.0',8795),b.Handler); s.budget=QueuedBudget(); s.token=(b.ROOT/'broker.token').read_text().strip(); print('Exact-input recovery proxy ready',flush=True); s.serve_forever()"
    guard=subprocess.Popen([sys.executable,'-B','-c',code],cwd=ROOT,stdout=log,stderr=log)
    try:
        for _ in range(40):
            try:urllib.request.urlopen('http://127.0.0.1:8795/health',timeout=1).read();break
            except Exception:time.sleep(.5)
        else:raise RuntimeError('Recovery proxy not ready')
        packets=[original_load(x['path'])for x in original_load(ROOT/'public/manifest.json')]
        packets.sort(key=lambda p:len(p['case']['diff']),reverse=True)
        for arm in e.ARMS:
            if(ROOT/'arms'/arm/'scoring/results.json').exists():continue
            state('review',arm=arm,transport_resume=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=3)as pool:
                futures=[pool.submit(e.finalize,p,arm)for p in packets]
                for f in concurrent.futures.as_completed(futures):f.result()
            state('judge',arm=arm,transport_resume=True);score(arm)
            import report_recovery
            from report import update
            result=update();print(json.dumps(dict(event='arm_completed',arm=arm,result=result['arms'][arm])),flush=True)
        state('complete',transport_resume=True)
    except Exception as exc:state('failed',error=str(exc),transport_resume=True);raise
    finally:guard.terminate();guard.wait(timeout=15);log.close()
if __name__=='__main__':main()
