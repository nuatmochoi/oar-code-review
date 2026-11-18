"""Rebuild usage accounting from every attempted-call record, including exclusions."""
from collections import Counter
import json
from pathlib import Path

RUN = Path(__file__).resolve().parent
specs = [
    ('excluded_p0_v1', 'p0_v1/raw', 'excluded'),
    ('p0_v2', 'p0_v2/raw', 'analyzed'),
    ('legacy_controls', 'legacy_controls_v2/raw', 'analyzed'),
    ('transport_smokes', 'transport_smoke', 'diagnostic'),
    ('excluded_priority_followup', 'priority_followup/raw', 'excluded'),
    ('priority_followup_v2', 'priority_followup_v2/raw', 'analyzed'),
]
batches, counts, usage = {}, Counter(), Counter()
for name, directory, group in specs:
    records = []
    for path in (RUN / directory).glob('*.json'):
        if path.name.endswith('.trace.json'):
            continue
        record = json.loads(path.read_text())
        if 'status' in record and 'tool_audit' in record:
            records.append(record)
    subtotal = Counter()
    for record in records:
        for item in record.get('usage', []):
            subtotal.update({k: v for k, v in item.items() if isinstance(v, (int, float))})
    usage.update(subtotal)
    counts[group] += len(records)
    batches[name] = {'directory': directory, 'classification': group,
        'attempts': len(records), 'valid_transport': sum(r['status'] == 'ok' for r in records),
        'failed_transport': sum(r['status'] != 'ok' for r in records),
        'tool_calls': sum(r['tool_audit']['tool_call_count'] for r in records),
        'usage_missing': sum(not r.get('usage') for r in records), 'reported_usage': dict(subtotal)}
analysis_failures = sum(b['failed_transport'] for b in batches.values() if b['classification'] == 'analyzed')
ledger = {'batches': batches, 'all_attempts': sum(counts.values()),
    'analyzed_calls': counts['analyzed'] - analysis_failures,
    'analyzed_batch_attempts': counts['analyzed'], 'failed_calls_in_analyzed_batches': analysis_failures,
    'excluded_batch_attempts': counts['excluded'],
    'separate_diagnostic_calls': counts['diagnostic'], 'reported_usage': dict(usage),
    'usage_note': 'Cached input is a subset of reported input. Reasoning output is reported separately; it is not added again to total output. Missing usage is unknown, not zero. No monetary cost estimate.',
    'legacy_integrity_valid': True,
    'priority_integrity_valid': json.loads((RUN / 'priority_followup_v2/audit.json').read_text())['valid'],
    'priority_execution_complete': json.loads((RUN / 'priority_followup_v2/audit.json').read_text())['execution_complete']}
(RUN / 'call_ledger.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(ledger, ensure_ascii=False))
