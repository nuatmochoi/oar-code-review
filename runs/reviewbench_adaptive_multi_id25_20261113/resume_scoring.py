"""Resume official scoring with exact input counts and saved identical responses."""
from collections import Counter
import hashlib
import io
import json
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

from experiment import ROOT, PORT, load, sha, write
from freeze_run import verify
from pipeline import score, state
from recovery_guard import QueuedBudget, base
from research_openai_credentials import headers, load_credentials


def canonical(body):
    return {k:v for k,v in body.items() if k!='prompt_cache_key'}


def request_key(body):
    encoded = json.dumps(canonical(body),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    return hashlib.sha256(encoded).hexdigest()


def count_tokens(body):
    input_keys = {'model','input','instructions','conversation','previous_response_id','reasoning',
                  'text','tools','tool_choice','parallel_tool_calls','truncation','personality'}
    non_input_keys = {'max_output_tokens','store','service_tier','metadata','stream','prompt_cache_key','include'}
    assert set(body)<=input_keys|non_input_keys
    count_request = {k:v for k,v in body.items() if k in input_keys}
    dest = ROOT/'audit/input_counts'/request_key(body)
    response_path = dest/'response.json'
    if response_path.exists():
        assert load(dest/'request.json')==count_request
        response = load(response_path)
    else:
        request_path = dest/'request.json'
        if request_path.exists(): assert load(request_path)==count_request
        else: write(request_path,count_request)
        req = urllib.request.Request('https://api.openai.com/v1/responses/input_tokens',
                                     data=json.dumps(count_request).encode(),headers=headers(load_credentials()),method='POST')
        with urllib.request.urlopen(req,timeout=45) as stream:
            response = json.load(stream)
        write(response_path,response)
    assert response['object']=='response.input_tokens' and type(response['input_tokens']) is int
    return response['input_tokens'],str(dest)


class ScoringBudget(QueuedBudget):
    def admit(self, body, phase):
        if phase!='judge':
            raise ValueError('Recovery handles scoring requests only')
        limit = body['max_output_tokens']
        assert body['model']==base.MODEL and type(limit) is int and 0<limit<=base.MAX_OUTPUT
        assert not body.get('previous_response_id') and not body.get('conversation')
        wire = json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()
        token_bound = min(len(wire)+8192,1050000)
        reserved = (token_bound*(10 if token_bound>272000 else 5)+limit*30)/1e6
        count_ref = None
        with self.changed:
            use_exact = self.totals()[0]+reserved>self.cap
        if use_exact:
            count, count_ref = count_tokens(body)
            # The count endpoint measures the provider's serialized input. The margin
            # covers the same input sent to generation, and output is fully reserved.
            token_bound = count+8192
            reserved = (token_bound*(10 if token_bound>272000 else 5)+limit*30)/1e6
        started = time.monotonic()
        with self.changed:
            while self.totals()[0]+reserved>self.cap:
                if not self.active or time.monotonic()-started>1000:
                    raise ValueError(f'Scoring reservation ${reserved:.5f} exceeds remaining budget ${self.cap-self.totals()[0]:.5f}')
                self.changed.wait(timeout=1)
            rid = time.strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(5)
            row = dict(id=rid,phase=phase,reserved_usd=reserved,input_token_upper_bound=token_bound,
                       max_output_tokens=limit,request_sha256=hashlib.sha256(wire).hexdigest(),
                       admitted_at=time.time(),status='admitted_unknown',global_cap=self.cap,input_count_audit=count_ref)
            folder = ROOT/'api'/rid
            write(folder/'request.json',body)
            write(folder/'admission.json',row)
            self.records[rid] = row
            self.active.add(rid)
            return row,folder


class ReplayHandler(base.Handler):
    def reply(self, status, body):
        if status>=400:
            print(json.dumps(dict(http_error=status,message=body)),flush=True)
        return super().reply(status,body)

    def do_POST(self):
        print(json.dumps(dict(request_path=self.path)),flush=True)
        if not self.path.startswith('/judge/') or self.headers.get('Authorization')!='Bearer '+self.server.token:
            return self.reply(403,{'error':'unauthorized'})
        raw = self.rfile.read(int(self.headers['Content-Length']))
        body = json.loads(raw)
        body.setdefault('max_output_tokens',base.MAX_OUTPUT)
        body['service_tier'] = 'default'
        key = request_key(body)
        hit = self.server.replays.get(key)
        if hit:
            assert canonical(body)==canonical(load(hit/'request.json'))
            response = (hit/'response.raw').read_bytes()
            identifier = time.strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(5)
            write(ROOT/'audit/judge_replays'/f'{identifier}.json',
                  dict(original_api_id=hit.name,request_key=key,response_sha256=sha(hit/'response.raw'),new_inference_calls=0))
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream' if body.get('stream') else 'application/json')
            self.send_header('Content-Length',str(len(response)))
            self.send_header('Connection','close')
            self.end_headers()
            self.close_connection = True
            self.wfile.write(response)
            return
        print(json.dumps(dict(replay_miss=key)),flush=True)
        previous = self.rfile
        self.rfile = io.BytesIO(raw)
        try:
            return super().do_POST()
        finally:
            self.rfile = previous


def serve():
    frozen = verify()
    base.CAP = frozen['new_api_cap_usd']
    server = base.Server(('127.0.0.1',PORT),ReplayHandler)
    server.budget = ScoringBudget(cap=base.CAP)
    server.token = (ROOT/'broker.token').read_text().strip()
    server.replays = {}
    for path in sorted((ROOT/'api').glob('*/receipt.json')):
        row = load(path)
        if row['phase']=='judge' and row['status']=='completed' and row.get('usage') and (path.parent/'response.raw').exists():
            server.replays.setdefault(request_key(load(path.parent/'request.json')),path.parent)
    print('Scoring replay server ready',len(server.replays),flush=True)
    server.serve_forever()


def resume():
    frozen = verify()
    checkpoint = ROOT/'arms/ADD/scoring/results.checkpoint.json'
    assert len(load(checkpoint)['completed'])>=23
    attempt = len(list((ROOT/'arms/ADD/scoring').glob('judge-*.log')))+1
    archive = ROOT/'audit'/f'checkpoint_before_attempt_{attempt}.json'
    archive.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(checkpoint,archive)
    with (ROOT/f'proxy.resume-{attempt}.log').open('x') as log:
        proxy = subprocess.Popen([sys.executable,'-B',__file__,'--serve'],cwd=ROOT,stdout=log,stderr=log)
        try:
            for _ in range(40):
                if proxy.poll() is not None: raise RuntimeError('Recovery proxy stopped')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health',timeout=1) as stream:
                        assert json.load(stream)['cap']==frozen['new_api_cap_usd']
                    break
                except OSError: time.sleep(.25)
            else: raise RuntimeError('Recovery proxy unavailable')
            state('judge',resume_attempt=attempt)
            score('ADD')
            state('complete',resume_attempt=attempt)
        except Exception as error:
            state('failed',error=str(error))
            raise
        finally:
            proxy.terminate()
            proxy.wait(timeout=15)


if __name__=='__main__':
    if '--serve' in sys.argv: serve()
    else: resume()
