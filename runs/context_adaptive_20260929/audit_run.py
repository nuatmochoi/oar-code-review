"""Independent read-only audit of records, prompts, budgets and frozen inputs."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ace_review.context_adaptive import Corpus, EvidenceState, digest
from ace_review.obligation import token_count

RUN = Path(__file__).resolve().parent
batch = RUN / 'p0_v2'
freeze = json.loads((batch / 'freeze.json').read_text())
errors = []
for name, expected in freeze['source_and_input_sha256'].items():
    path = ROOT / name if name.startswith('ace_review/') else batch / name
    if digest(path.read_text()) != expected:
        errors.append('freeze_mismatch:' + name)
cases = {c['case_id']: c for c in json.loads((batch / 'cases.json').read_text())}
corpora = {(cid, world): Corpus(spec['versions'], spec['graphs'], case['diff'])
           for cid, case in cases.items() for world, spec in case['worlds'].items()}
records = [json.loads(f.read_text()) for f in (batch / 'raw').glob('*.json') if not f.name.endswith('.trace.json')]
phases = Counter(r['phase'] for r in records)
usage = Counter()
cost_by_arm = defaultdict(Counter)
for record in records:
    key = record['run_id']
    original = json.loads((batch / 'inputs' / (key + '.json')).read_text())
    if digest(original['prompt']) != record['prompt_sha256']:
        errors.append('prompt_hash:' + key)
    if record['tool_audit']['tool_call_count']:
        errors.append('tool_call:' + key)
    if record['status'] != 'ok':
        errors.append('transport_failure:' + key)
    if original['prompt_reference_tokens'] != token_count(original['prompt']):
        errors.append('prompt_tokens:' + key)
    for item in record.get('usage', []):
        for k, v in item.items():
            if isinstance(v, (int, float)):
                usage[k] += v
                cost_by_arm[record.get('arm', 'shared_questions')][k] += v
    cost_by_arm[record.get('arm', 'shared_questions')]['calls'] += 1
    cost_by_arm[record.get('arm', 'shared_questions')]['duration_seconds_sum'] += record['duration_seconds']
    cost_by_arm[record.get('arm', 'shared_questions')]['prompt_reference_tokens'] += record['prompt_reference_tokens']
retrieval = json.loads((batch / 'retrieval_round2.json').read_text())
packet_audit = []
for row in retrieval['records']:
    c = corpora[row['case_id'], row['world']]
    previous = {}
    ppr_attempts = ppr_nonconverged = 0
    for round_record in row['rounds']:
        state_record = round_record['state']
        state = EvidenceState(c)
        state.lines = {k: set(v) for k, v in state_record['source_lines'].items()}
        state.paths = state_record['paths']
        if state.tokens != state_record['tokens'] or digest(state.render()) != state_record['packet_sha256']:
            errors.append('packet_reconstruction:' + str((row['case_id'], row['world'], row['arm'], round_record['round'])))
        if state.tokens > round_record['cap']:
            errors.append('budget_exceeded:' + str(row))
        if any(not state.lines.get(k, set()) >= lines for k, lines in previous.items()):
            errors.append('forgotten_source:' + str(row))
        for k, lines in state.lines.items():
            u = c.units[k]
            if not lines <= set(range(u.start, u.end + 1)):
                errors.append('out_of_source_span:' + k)
        previous = state.lines
        for query in round_record['queries']:
            if 'converged' in query:
                ppr_attempts += 1
                ppr_nonconverged += not query['converged']
    packet_audit.append({'case_id': row['case_id'], 'world': row['world'], 'arm': row['arm'],
        'rounds': len(row['rounds']), 'final_source_tokens': row['rounds'][-1]['state']['tokens'],
        'ppr_attempts': ppr_attempts, 'ppr_nonconverged': ppr_nonconverged})
finals = json.loads((batch / 'final_packets.json').read_text())
ff = json.loads((batch / 'final_freeze.json').read_text())
if digest(finals) != ff['final_packets_sha256']:
    errors.append('final_freeze_mismatch')
indexed = {(r['case_id'], r['world'], r['arm']): r for r in finals}
identical_pairs = []
for cid in cases:
    for arm in ('L', 'R', 'S', 'C', 'Q', 'SCQ'):
        a, b = indexed.get((cid, 'a', arm)), indexed.get((cid, 'b', arm))
        if a and b and a['prompt_sha256'] == b['prompt_sha256']:
            identical_pairs.append({'case_id': cid, 'arm': arm})
if len(records) != 136:
    errors.append('expected_136_records_found_' + str(len(records)))
execution = json.loads((batch / 'execution.json').read_text())
errors.extend('controller_semantics:' + str(x) for x in execution['controller_failures'])
errors.extend('final_semantics:' + str(x) for x in execution['final_semantic_invalid'])
report = {'valid': not errors, 'errors': errors, 'recorded_calls': len(records), 'phases': dict(phases),
    'transport_errors': sum(r['status'] != 'ok' for r in records),
    'observed_tool_calls': sum(r['tool_audit']['tool_call_count'] for r in records),
    'reported_usage': dict(usage), 'calls_without_usage': sum(not r.get('usage') for r in records),
    'cost_by_arm': {k: dict(v) for k, v in cost_by_arm.items()}, 'packet_audit': packet_audit,
    'same_prompt_safe_unsafe_pairs_excluding_shared_D': identical_pairs,
    'source_union_cap': 1600, 'controller_failures': retrieval['failures'],
    'scope': 'four_observed_development_cases_not_new_12_family_pilot'}
(RUN / 'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({k: report[k] for k in ('valid', 'errors', 'recorded_calls', 'phases', 'transport_errors', 'observed_tool_calls', 'same_prompt_safe_unsafe_pairs_excluding_shared_D')}, ensure_ascii=False))
