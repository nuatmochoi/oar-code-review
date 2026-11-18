"""Recover one pending judgment while keeping the experiment's spend cap."""

from collections import defaultdict
import io
import json
import re
import sys
import time

import resume_scoring as scorer
from resume_exact_pricing import ExactPricingBudget


LAST_INPUT_KEY = '535f631edc6d0bb0cbe5c0e822d395b3c25ff0bff3e4d9e22625d7a2bdd1d357'
LAST_MAX_OUTPUT = 8192
ATTEMPT_TWO_START = '20261113T152740-39868b6197'


def same_grep_matches(current, saved):
    # Ripgrep can emit files in different orders. Require each file's exact line sequence.
    def by_file(output):
        groups = defaultdict(list)
        for line in output.splitlines():
            match = re.match(r'^(.+?)[:-]\d+[:-]', line)
            if not match:
                return None
            groups[match[1]].append(line)
        return dict(groups)

    source = by_file(current)
    return source is not None and source == by_file(saved)


class FinalReplayHandler(scorer.ReplayHandler):
    def do_POST(self):
        if not self.path.startswith('/judge/') or self.headers.get('Authorization') != 'Bearer ' + self.server.token:
            return self.reply(403, {'error': 'unauthorized'})
        size = self.headers['Content-Length']
        original_stream = self.rfile
        body = json.loads(original_stream.read(int(size)))
        ordered = self.restore_grep_order(body)
        pending = self.server.last_count_request
        limited = all(body.get(key) == value for key, value in pending.items())
        if limited:
            body['max_output_tokens'] = LAST_MAX_OUTPUT
        if ordered or limited:
            scorer.write(scorer.ROOT / 'audit/final_judge_recovery' / f'{time.time_ns()}.json',
                         dict(grep_outputs_restored=ordered, last_output_cap=LAST_MAX_OUTPUT if limited else None,
                              original_output_cap=scorer.base.MAX_OUTPUT,
                              request_key=scorer.request_key(body)))
        raw = json.dumps(body).encode()
        self.rfile = io.BytesIO(raw)
        self.headers.replace_header('Content-Length', str(len(raw)))
        try:
            super().do_POST()
        finally:
            self.rfile = original_stream
            self.headers.replace_header('Content-Length', size)

    def restore_grep_order(self, body):
        if not isinstance(body.get('input'), list):
            return []
        if body['input'][:2] != self.server.old_classifier_prefix:
            return []
        restored, grep_calls = [], set()
        for item in body['input']:
            if item.get('type') == 'function_call' and item.get('name') == 'grep':
                grep_calls.add(item['call_id'])
            if item.get('type') != 'function_call_output' or item.get('call_id') not in grep_calls:
                continue
            old = self.server.old_grep_results.get(item['call_id'])
            new = item.get('output')
            if old and isinstance(new, str) and new != old and same_grep_matches(new, old):
                item['output'] = old
                restored.append(item['call_id'])
        return restored


class FinalServer(scorer.base.Server):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_count_request = scorer.load(scorer.ROOT / 'audit/input_counts' / LAST_INPUT_KEY / 'request.json')
        old_input = scorer.load(scorer.ROOT / 'api' / ATTEMPT_TWO_START / 'request.json')['input']
        assert old_input[:2] == self.last_count_request['input'][:2]
        self.old_classifier_prefix = old_input[:2]
        grep_calls = {item['call_id'] for item in self.last_count_request['input']
                      if item.get('type') == 'function_call' and item.get('name') == 'grep'}
        self.old_grep_results = {item['call_id']: item['output'] for item in self.last_count_request['input']
                                 if item.get('type') == 'function_call_output' and item.get('call_id') in grep_calls}


if __name__ == '__main__':
    scorer.ReplayHandler = FinalReplayHandler
    scorer.ScoringBudget = ExactPricingBudget
    scorer.base.Server = FinalServer
    scorer.__file__ = __file__
    if '--serve' in sys.argv:
        scorer.serve()
    else:
        scorer.resume()
