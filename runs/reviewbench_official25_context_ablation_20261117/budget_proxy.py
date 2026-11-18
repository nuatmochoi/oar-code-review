"""Arm-isolated, durable Responses gateway. Never retries or truncates prompts.

Endpoints: /{review,judge}/{B0,B1,B2}/v1/responses, plus the corresponding
/input_tokens endpoint. The input-token endpoint is free; no review is sent
until freezing and both total/arm dollar admission have succeeded.
"""
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]/'tools'))
from research_openai_credentials import headers, load_credentials
from runtime_cost import estimated_cost, reservation_cost

MODEL = 'gpt-5.6-sol'
ARMS = ('B0', 'B1', 'B2')
PORT = 8810
CAP = 70.0
ARM_CAP = CAP/3
MAX_OUTPUT = 16384
CONTEXT_WINDOW = 1000000
EXACT_COUNT_BYTE_THRESHOLD = 131072
COUNT_MARGIN = 8192
QUEUE_TIMEOUT_SECONDS = 900
UPSTREAM_TIMEOUT_SECONDS = 900
COUNT_FIELDS = {'model', 'input', 'instructions', 'reasoning', 'text', 'tools',
                'tool_choice', 'parallel_tool_calls', 'truncation'}
NONCOUNT_FIELDS = {'max_output_tokens', 'store', 'service_tier', 'metadata', 'stream',
                  'prompt_cache_key', 'include'}


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())


def wire_json(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def fresh_id():
    return time.strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(6)


def normalize(received, phase):
    if not isinstance(received, dict):
        raise ValueError('A JSON object is required')
    body = dict(received)
    if body.get('model') != MODEL:
        raise ValueError('Only the frozen model is permitted')
    if body.get('previous_response_id') or body.get('conversation'):
        raise ValueError('Unmetered server-side history is forbidden')
    if body.get('truncation', 'disabled') != 'disabled':
        raise ValueError('Automatic context truncation is forbidden')
    if set(body) - COUNT_FIELDS - NONCOUNT_FIELDS:
        raise ValueError('Unsupported request fields: '+','.join(sorted(set(body)-COUNT_FIELDS-NONCOUNT_FIELDS)))
    output = body.setdefault('max_output_tokens', MAX_OUTPUT)
    if type(output) is not int or not 0 < output <= MAX_OUTPUT:
        raise ValueError('max_output_tokens must be 1..16384')
    if body.get('service_tier', 'default') not in ('default', 'auto'):
        raise ValueError('Only standard service is permitted')
    body['service_tier'] = 'default'
    if phase == 'review' and body.get('reasoning', {}).get('effort') != 'low':
        raise ValueError('Reviewer reasoning must remain low')
    # The official classifier uses medium; the official matcher uses low.
    # Their original reasoning configuration must survive unchanged.
    if phase == 'judge' and body.get('reasoning', {}).get('effort') not in ('low', 'medium'):
        raise ValueError('Unexpected official judge reasoning level')
    return body


class Budget:
    def __init__(self, root=ROOT, cap=CAP, arm_cap=ARM_CAP, count_fn=None, queue_timeout=QUEUE_TIMEOUT_SECONDS):
        self.root = Path(root)
        self.cap, self.arm_cap = cap, arm_cap
        self.queue_timeout = queue_timeout
        self.changed = threading.Condition()
        self.records, self.active = {}, set()
        self.count_locks, self.count_locks_lock = {}, threading.Lock()
        self.count_fn = count_fn
        for arm in ARMS:
            for name in ('admission.json', 'receipt.json'):
                for path in sorted((self.root/'arms'/arm/'api').glob('*/'+name)):
                    row = json.loads(path.read_text())
                    if row['arm'] != arm:
                        raise ValueError('Ledger arm mismatch')
                    self.records[row['id']] = row

    def totals(self):
        arms = {a: 0.0 for a in ARMS}
        for value in self.records.values():
            arms[value['arm']] += value.get('settled_upper', value['reserved_usd'])
        return {'total': sum(arms.values()), 'arms': arms}

    def status(self):
        with self.changed:
            return {**self.totals(), 'calls': len(self.records), 'active': len(self.active),
                    'unknown': sum('settled_upper' not in r for r in self.records.values()),
                    'cap': self.cap, 'arm_cap': self.arm_cap, 'model': MODEL}

    def exact_count(self, body, arm):
        request_body = {k: v for k, v in body.items() if k in COUNT_FIELDS}
        digest = hashlib.sha256(wire_json(request_body)).hexdigest()
        folder = self.root/'arms'/arm/'audit/counts'/digest
        with self.count_locks_lock:
            lock = self.count_locks.setdefault((arm, digest), threading.Lock())
        with lock:
            response_path = folder/'response.json'
            if response_path.exists():
                if json.loads((folder/'request.json').read_text()) != request_body:
                    raise ValueError('Input count cache mismatch')
                result = json.loads(response_path.read_text())
            else:
                if not (folder/'request.json').exists():
                    write(folder/'request.json', request_body)
                attempt = folder/'attempts'/fresh_id()
                write(attempt/'request.json', request_body)
                started = time.monotonic()
                try:
                    if self.count_fn:
                        result = {'object': 'response.input_tokens', 'input_tokens': self.count_fn(request_body)}
                    else:
                        request = urllib.request.Request('https://api.openai.com/v1/responses/input_tokens',
                            data=wire_json(request_body), headers=headers(load_credentials()), method='POST')
                        with urllib.request.urlopen(request, timeout=60) as response:
                            raw = response.read()
                        (attempt/'response.raw').write_bytes(raw)
                        result = json.loads(raw)
                    if result.get('object') != 'response.input_tokens' or type(result.get('input_tokens')) is not int or result['input_tokens'] < 0:
                        raise ValueError('Invalid input token count response')
                    write(attempt/'response.json', result)
                    write(response_path, result)
                    write(attempt/'timing.json', {'wall_seconds': time.monotonic()-started, 'free_input_count': True})
                except urllib.error.HTTPError as exc:
                    (attempt/'error.raw').write_bytes(exc.read())
                    write(attempt/'failure.json', {'type': type(exc).__name__, 'status': exc.code,
                                                  'wall_seconds': time.monotonic()-started, 'free_input_count': True})
                    raise ValueError('Input count request rejected; no paid response request sent') from None
                except Exception as exc:
                    write(attempt/'failure.json', {'type': type(exc).__name__, 'wall_seconds': time.monotonic()-started,
                                                  'free_input_count': True})
                    raise ValueError('Input count failed; no paid response request sent') from None
        return result['input_tokens'], str(folder)

    def admit(self, body, phase, arm):
        if arm not in ARMS or phase not in ('review', 'judge'):
            raise ValueError('Unknown arm or phase')
        wire = wire_json(body)
        bound = len(wire) + COUNT_MARGIN
        exact, count_ref = None, None
        reserved = reservation_cost(bound, body['max_output_tokens'])
        with self.changed:
            totals = self.totals()
            tight = totals['total']+reserved > self.cap or totals['arms'][arm]+reserved > self.arm_cap
        if len(wire) >= EXACT_COUNT_BYTE_THRESHOLD or tight or bound+body['max_output_tokens'] > CONTEXT_WINDOW:
            exact, count_ref = self.exact_count(body, arm)
            if exact+body['max_output_tokens'] > CONTEXT_WINDOW:
                raise ValueError('Context window exceeded; automatic truncation is forbidden')
            bound = exact+COUNT_MARGIN
            reserved = reservation_cost(bound, body['max_output_tokens'])
        started = time.monotonic()
        with self.changed:
            while True:
                if any(v.get('reservation_underestimated') for v in self.records.values()):
                    raise ValueError('A prior cost reservation was underestimated; admissions halted')
                totals = self.totals()
                arm_tight = totals['arms'][arm]+reserved > self.arm_cap+1e-9
                total_tight = totals['total']+reserved > self.cap+1e-9
                if not arm_tight and not total_tight:
                    break
                relevant = [r for rid, r in self.records.items() if rid in self.active and (not arm_tight or r['arm'] == arm)]
                if not relevant or time.monotonic()-started >= self.queue_timeout:
                    raise ValueError('Budget admission denied; no paid upstream request sent')
                self.changed.wait(timeout=min(1, self.queue_timeout))
            rid = fresh_id()
            row = dict(id=rid, arm=arm, phase=phase, reserved_usd=reserved,
                       input_token_upper_bound=bound, exact_input_tokens=exact,
                       max_output_tokens=body['max_output_tokens'], request_sha256=hashlib.sha256(wire).hexdigest(),
                       admitted_at=time.time(), queue_seconds=time.monotonic()-started,
                       status='admitted_unknown', global_cap=self.cap, arm_cap=self.arm_cap,
                       input_count_audit=count_ref)
            folder = self.root/'arms'/arm/'api'/rid
            write(folder/'request.json', body)
            write(folder/'admission.json', row)
            self.records[rid] = row
            self.active.add(rid)
            return row, folder

    def settle(self, row, folder, usage, status, error=None):
        with self.changed:
            value = {**row, 'status': status, 'finished_at': time.time(), 'usage': usage, 'error': error}
            try:
                cost = estimated_cost(usage or {})
            except ValueError:
                cost = None
                value['error'] = 'invalid_usage_breakdown'
            if cost:
                value['cost'] = cost
                value['settled_upper'] = cost['usd_conservative_upper']
                value['reservation_underestimated'] = value['settled_upper'] > row['reserved_usd']+1e-9
            elif status == 'upstream_rejected':
                value['settled_upper'] = 0.0
            write(folder/'receipt.json', value)
            self.records[row['id']] = value
            self.active.discard(row['id'])
            self.changed.notify_all()
            print(json.dumps({'event': 'settled', 'arm': row['arm'], 'phase': row['phase'],
                              'id': row['id'], 'status': status,
                              'usd_usage_estimate': (cost or {}).get('usd_list_rate_estimate'),
                              'upper': self.totals()}), flush=True)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def reply(self, status, body):
        raw = wire_json(body)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/health':
            return self.reply(200, self.server.budget.status())
        return self.reply(404, {'error': 'unknown path'})

    def do_POST(self):
        match = re.fullmatch(r'/(review|judge)/(B[012])/v1/responses(/input_tokens)?', self.path)
        if not match:
            return self.reply(404, {'error': 'unsupported endpoint'})
        if self.headers.get('Authorization') != 'Bearer '+self.server.token:
            return self.reply(403, {'error': 'unauthorized'})
        phase, arm, count_only = match.groups()
        attempt = self.server.budget.root/'arms'/arm/'audit/gateway_attempts'/fresh_id()
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 50000000:
                raise ValueError('Invalid request body length')
            raw = self.rfile.read(length)
            attempt.mkdir(parents=True)
            (attempt/'received.raw').write_bytes(raw)
            received = json.loads(raw)
            body = normalize(received, phase)
            from freeze_run import verify
            verify()
            if count_only:
                count, ref = self.server.budget.exact_count(body, arm)
                write(attempt/'result.json', {'input_count_audit': ref, 'free_input_count': True})
                return self.reply(200, {'object': 'response.input_tokens', 'input_tokens': count})
            row, folder = self.server.budget.admit(body, phase, arm)
            write(folder/'received_request.json', received)
            write(folder/'gateway_parameter_policy.json', {
                'max_output_cap': MAX_OUTPUT, 'inserted_max_output': 'max_output_tokens' not in received,
                'service_tier': 'default', 'prompt_changed': False, 'reasoning_changed': False,
                'truncation': 'disabled', 'gateway_attempt': str(attempt)})
            write(attempt/'admitted.json', {'id': row['id'], 'folder': str(folder)})
        except Exception as exc:
            write(attempt/'failure.json', {'type': type(exc).__name__, 'message': str(exc), 'paid_upstream_sent': False})
            return self.reply(400, {'error': {'message': str(exc), 'type': 'budget_guard'}})
        usage, status, error, headers_sent = None, 'usage_unknown', None, False
        try:
            # Recheck immediately before the billable network operation.
            from freeze_run import verify
            verify()
            request = urllib.request.Request('https://api.openai.com/v1/responses',
                data=wire_json(body), headers=headers(load_credentials()), method='POST')
            with urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT_SECONDS) as response:
                content_type = response.headers.get('Content-Type', 'application/json')
                write(folder/'upstream_metadata.json', {'status': response.status,
                    'content_type': content_type, 'request_id': response.headers.get('x-request-id')})
                self.send_response(response.status)
                self.send_header('Content-Type', content_type)
                self.send_header('Connection', 'close')
                self.end_headers()
                headers_sent = True
                self.close_connection = True
                chunks, client_alive = [], True
                with (folder/'response.raw').open('xb') as handle:
                    while True:
                        chunk = response.readline() if 'text/event-stream' in content_type else response.read(65536)
                        if not chunk:
                            break
                        handle.write(chunk)
                        handle.flush()
                        if 'text/event-stream' not in content_type:
                            chunks.append(chunk)
                        if client_alive:
                            try:
                                self.wfile.write(chunk)
                                self.wfile.flush()
                            except OSError:
                                client_alive = False
                        if chunk.startswith(b'data: '):
                            try:
                                event = json.loads(chunk[6:])
                                result = event.get('response', {})
                                if result.get('usage'):
                                    usage, status = result['usage'], result.get('status', 'completed')
                                elif event.get('usage'):
                                    usage, status = event['usage'], 'completed'
                            except (ValueError, TypeError):
                                pass
                    handle.flush()
                    os.fsync(handle.fileno())
                if 'text/event-stream' not in content_type:
                    result = json.loads(b''.join(chunks))
                    usage, status = result.get('usage'), result.get('status', 'completed')
                    write(folder/'response.json', result)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            (folder/'upstream_error.raw').write_bytes(raw)
            status = 'upstream_rejected' if exc.code in (400, 401, 403, 404, 422, 429) else 'usage_unknown'
            error = 'HTTP '+str(exc.code)
            if not headers_sent:
                try:
                    self.send_response(exc.code)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.close_connection = True
                    self.wfile.write(raw)
                except OSError:
                    pass
        except Exception as exc:
            error = type(exc).__name__
            if not headers_sent:
                try:
                    self.reply(502, {'error': {'message': 'Upstream outcome unknown; reservation retained', 'type': 'upstream_unknown'}})
                except OSError:
                    pass
        finally:
            self.server.budget.settle(row, folder, usage, status, error)


class Server(http.server.ThreadingHTTPServer):
    # Finish already-paid streams on orderly shutdown to preserve their usage.
    daemon_threads = False


def main():
    from freeze_run import verify
    verify()
    token_path = ROOT/'broker.token'
    if not token_path.exists():
        fd = os.open(token_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_urlsafe(32))
    server = Server(('127.0.0.1', PORT), Handler)
    server.budget = Budget()
    server.token = token_path.read_text().strip()
    print(f'Budget proxy ready on 127.0.0.1:{PORT}; total ${CAP}, each arm ${ARM_CAP:.8f}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
