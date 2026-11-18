"""Keep a separate durable ledger per arm and replay exact official judge calls."""

import hashlib
import io
import json
import os
import secrets
import time
import urllib.request

from experiment import PORT, ROOT, load, sha, write
from freeze_run import verify
from recovery_guard import QueuedBudget, base
from research_openai_credentials import headers, load_credentials


def canonical(body):
    return {k:v for k,v in body.items() if k!='prompt_cache_key'}


def key(body):
    payload = json.dumps(canonical(body),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    return hashlib.sha256(payload).hexdigest()


def exact_count(body, folder):
    fields = {'model','input','instructions','reasoning','text','tools','tool_choice','parallel_tool_calls','truncation'}
    known = fields|{'max_output_tokens','store','service_tier','metadata','stream','prompt_cache_key','include'}
    assert set(body)<=known
    req = {k:v for k,v in body.items() if k in fields}
    path = folder/'audit/counts'/key(body)
    response = path/'response.json'
    if response.exists():
        assert load(path/'request.json')==req
        count = load(response)
    else:
        if (path/'request.json').exists():
            assert load(path/'request.json')==req
        else:
            write(path/'request.json',req)
        request = urllib.request.Request('https://api.openai.com/v1/responses/input_tokens',
                                         data=json.dumps(req).encode(),headers=headers(load_credentials()),method='POST')
        with urllib.request.urlopen(request,timeout=45) as stream:
            count = json.load(stream)
        write(response,count)
    assert count['object']=='response.input_tokens' and type(count['input_tokens']) is int
    return count['input_tokens'], str(path)


class Budget(QueuedBudget):
    def __init__(self, folder, cap, shared):
        self.shared = shared
        super().__init__(root=folder,cap=cap)

    def totals(self):
        total,review = super().totals()
        return total+self.shared,review+self.shared

    def admit(self, body, phase):
        limit = body['max_output_tokens']
        assert body['model']==base.MODEL and type(limit) is int and 0<limit<=base.MAX_OUTPUT
        assert body['service_tier']=='default' and not body.get('previous_response_id') and not body.get('conversation')
        wire = json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()

        def upper(input_tokens):
            usage = dict(input_tokens=input_tokens,output_tokens=limit,
                         input_tokens_details=dict(cache_write_tokens=input_tokens))
            return base.estimated_cost(usage)['usd_conservative_upper']

        bound = min(len(wire)+8192,1050000)
        count_ref = None
        if self.totals()[0]+upper(bound)>self.cap:
            count, count_ref = exact_count(body,self.root)
            bound = count+8192
        reserved = upper(bound)
        started = time.monotonic()
        with self.changed:
            while self.totals()[0]+reserved>self.cap:
                if not self.active or time.monotonic()-started>900:
                    raise ValueError(f'Admission ${reserved:.5f} exceeds remaining ${self.cap-self.totals()[0]:.5f}')
                self.changed.wait(timeout=1)
            rid = time.strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(5)
            row = dict(id=rid,phase=phase,reserved_usd=reserved,input_token_upper_bound=bound,
                       max_output_tokens=limit,request_sha256=hashlib.sha256(wire).hexdigest(),
                       admitted_at=time.time(),status='admitted_unknown',global_cap=self.cap,input_count_audit=count_ref)
            folder = self.root/'api'/rid
            write(folder/'request.json',body)
            write(folder/'admission.json',row)
            self.records[rid] = row
            self.active.add(rid)
            return row,folder


class Handler(base.Handler):
    def do_POST(self):
        if self.path!='/judge/v1/responses' or self.headers.get('Authorization')!='Bearer '+self.server.token:
            return super().do_POST()
        raw = self.rfile.read(int(self.headers['Content-Length']))
        body = json.loads(raw)
        body.setdefault('max_output_tokens',base.MAX_OUTPUT)
        body['service_tier'] = 'default'
        hit = self.server.replays.get(key(body))
        if hit:
            assert canonical(load(hit/'request.json'))==canonical(body)
            data = (hit/'response.raw').read_bytes()
            write(self.server.budget.root/'audit/replays'/f'{time.time_ns()}-{secrets.token_hex(4)}.json',
                  dict(request_key=key(body),origin=str(hit),response_sha256=sha(hit/'response.raw'),
                       historical_usd=load(hit/'receipt.json')['cost']['usd_list_rate_estimate']))
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream' if body.get('stream') else 'application/json')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Connection','close')
            self.end_headers()
            self.close_connection = True
            self.wfile.write(data)
            return
        original = self.rfile
        self.rfile = io.BytesIO(raw)
        try:
            super().do_POST()
        finally:
            self.rfile = original


if __name__=='__main__':
    frozen = verify()
    arm = os.environ['REVIEWBENCH_FALLBACK_ARM']
    folder = ROOT/'arms'/arm
    base.CAP = frozen['new_api_cap_usd']
    server = base.Server(('127.0.0.1',PORT),Handler)
    server.budget = Budget(folder,base.CAP,frozen['shared_cost'][arm])
    server.token = (ROOT/'broker.token').read_text().strip()
    server.replays = {}
    # Identical paid judge inputs can share their responses. Cache keys do not alter content.
    older = ROOT.parent/'reviewbench_unresolved_semantic25_20261113'/'arms'
    for directory in (folder,ROOT.parent/'reviewbench_adaptive_multi_id25_20261113',
                      older/'SEMANTIC',older/'CONNECTED'):
        for path in sorted(directory.glob('api/*/receipt.json')):
            receipt = load(path)
            if (receipt['phase']=='judge' and receipt['status']=='completed' and receipt.get('usage')
                and (path.parent/'response.raw').exists()):
                server.replays.setdefault(key(load(path.parent/'request.json')),path.parent)
    print(f'Budget proxy ready for {arm}, cap ${base.CAP:.8f}, {len(server.replays)} exact-response keys',flush=True)
    server.serve_forever()
