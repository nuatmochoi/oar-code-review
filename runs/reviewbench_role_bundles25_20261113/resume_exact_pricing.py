"""Resume the unchanged scorer using its frozen short-context rates for admission."""

import hashlib
import json
import secrets
import sys
import time

import resume_scoring as previous


class ExactPricingBudget(previous.ScoringBudget):
    def admit(self, body, phase):
        if phase != 'judge':
            raise ValueError('Scoring recovery accepts only judge calls')
        limit = body['max_output_tokens']
        assert body['model'] == previous.base.MODEL
        assert type(limit) is int and 0 < limit <= previous.base.MAX_OUTPUT
        assert body['service_tier'] == 'default'
        assert not body.get('previous_response_id') and not body.get('conversation')

        exact_count, audit = previous.count_tokens(body)
        input_upper = exact_count + 8192
        # Preserve the input margin and reserve every possible output token.
        # The frozen cost estimator applies the long tier only above 272k input.
        maximum_usage = previous.base.estimated_cost({
            'input_tokens': input_upper,
            'input_tokens_details': {'cache_write_tokens': input_upper},
            'output_tokens': limit,
        })
        reserved = maximum_usage['usd_conservative_upper']
        started = time.monotonic()
        with self.changed:
            while self.totals()[0] + reserved > self.cap:
                if not self.active or time.monotonic() - started > 1000:
                    remaining = self.cap - self.totals()[0]
                    raise ValueError(f'Scoring reservation ${reserved:.5f} exceeds remaining ${remaining:.5f}')
                self.changed.wait(timeout=1)
            wire = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()
            identifier = time.strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(5)
            row = dict(id=identifier, phase=phase, reserved_usd=reserved,
                       input_token_upper_bound=input_upper, max_output_tokens=limit,
                       request_sha256=hashlib.sha256(wire).hexdigest(),
                       admitted_at=time.time(), status='admitted_unknown', global_cap=self.cap,
                       input_count_audit=audit, maximum_usage_at_frozen_rates=maximum_usage)
            folder = previous.ROOT / 'api' / identifier
            previous.write(folder / 'request.json', body)
            previous.write(folder / 'admission.json', row)
            self.records[identifier] = row
            self.active.add(identifier)
            return row, folder


if __name__ == '__main__':
    previous.ScoringBudget = ExactPricingBudget
    previous.__file__ = __file__
    if '--serve' in sys.argv:
        previous.serve()
    else:
        previous.resume()
