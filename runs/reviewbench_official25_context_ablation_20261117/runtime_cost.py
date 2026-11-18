"""Frozen historical price estimates, not an assertion about invoice charges.

This experiment keeps the preceding experiment's unit rates for comparability.
The durable proxy ledger, rather than the judge SDK's display rates, is the
source of truth. Unknown attempts retain their maximum reservation.
"""
import json
from pathlib import Path

PRICE_BASIS = {
    'status': 'frozen_historical_estimate_not_invoice',
    'model': 'gpt-5.6-sol',
    'source': 'reviewbench_changeunits_commit_query25_20261105/budget_proxy.py',
    'long_input_threshold': 272000,
    'usd_per_million': {
        'regular': {'input': 4.0, 'cached_input': 0.4, 'cache_write': 5.0, 'output': 20.0},
        'long': {'input': 8.0, 'cached_input': 0.8, 'cache_write': 10.0, 'output': 30.0},
    },
}


def estimated_cost(usage):
    """Return a rate estimate only when the API supplied a usable usage object."""
    i = usage.get('input_tokens', usage.get('prompt_tokens'))
    o = usage.get('output_tokens', usage.get('completion_tokens'))
    if type(i) is not int or type(o) is not int or min(i, o) < 0:
        return None
    details = usage.get('input_tokens_details') or usage.get('prompt_tokens_details') or {}
    cached = details.get('cached_tokens', 0)
    writes = details.get('cache_write_tokens', 0)
    if any(type(v) is not int or v < 0 for v in (cached, writes)) or cached + writes > i:
        raise ValueError('Usage cache breakdown is inconsistent')
    tier = 'long' if i > PRICE_BASIS['long_input_threshold'] else 'regular'
    rate = PRICE_BASIS['usd_per_million'][tier]
    usd = ((i-cached-writes)*rate['input'] + cached*rate['cached_input']
           + writes*rate['cache_write'] + o*rate['output']) / 1e6
    return dict(input_tokens=i, output_tokens=o, cached_tokens=cached,
                cache_write_tokens=writes, usd_list_rate_estimate=usd,
                usd_conservative_upper=usd, price_tier=tier)


def reservation_cost(input_upper, output_upper):
    """Reserve the most expensive input type and the complete output allowance."""
    return estimated_cost(dict(input_tokens=input_upper, output_tokens=output_upper,
                               input_tokens_details={'cache_write_tokens': input_upper}))['usd_conservative_upper']


def summarize(root):
    root = Path(root)
    arms = {}
    for arm in ('B0', 'B1', 'B2'):
        records = {}
        for name in ('admission.json', 'receipt.json'):
            for path in sorted((root/'arms'/arm/'api').glob('*/'+name)):
                row = json.loads(path.read_text())
                records[row['id']] = row
        phases = {}
        for phase in ('review', 'judge'):
            rows = [v for v in records.values() if v['phase'] == phase]
            phases[phase] = {
                'attempts': len(rows),
                'usage_recorded': sum(bool(v.get('cost')) for v in rows),
                'usage_unknown': sum('settled_upper' not in v for v in rows),
                'rejected_non_billed': sum(v.get('status') == 'upstream_rejected' for v in rows),
                'input_tokens': sum(v.get('cost', {}).get('input_tokens', 0) for v in rows),
                'output_tokens': sum(v.get('cost', {}).get('output_tokens', 0) for v in rows),
                'cached_tokens': sum(v.get('cost', {}).get('cached_tokens', 0) for v in rows),
                'cache_write_tokens': sum(v.get('cost', {}).get('cache_write_tokens', 0) for v in rows),
                'usd_usage_estimate': sum(v.get('cost', {}).get('usd_list_rate_estimate', 0) for v in rows),
                'usd_upper_including_unknown': sum(v.get('settled_upper', v['reserved_usd']) for v in rows),
            }
        arms[arm] = phases
    return {'price_basis': PRICE_BASIS, 'arms': arms,
            'usd_usage_estimate': sum(p['usd_usage_estimate'] for a in arms.values() for p in a.values()),
            'usd_upper_including_unknown': sum(p['usd_upper_including_unknown'] for a in arms.values() for p in a.values()),
            'usage_unknown': sum(p['usage_unknown'] for a in arms.values() for p in a.values())}


if __name__ == '__main__':
    import sys
    print(json.dumps(summarize(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent), indent=2))
