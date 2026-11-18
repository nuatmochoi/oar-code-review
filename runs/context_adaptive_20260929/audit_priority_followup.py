"""Audit the exploratory policy repair without making any model calls."""
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import jsonschema
from ace_review.context_adaptive import Corpus, EvidenceState, digest, validate_controller, FINAL_SCHEMA
from ace_review.obligation import token_count

RUN = Path(__file__).resolve().parent
OUT = RUN / 'priority_followup_v2'
MAIN = RUN / 'p0_v2'
read = lambda p: json.loads(p.read_text())
errors = []
freeze = read(OUT / 'freeze.json')
for path, key in [(OUT / 'policy.py', 'policy_sha256'),
                  (RUN / 'run_priority_followup.py', 'script_sha256'),
                  (MAIN / 'freeze.json', 'source_main_freeze_sha256')]:
    if digest(path.read_text()) != freeze[key]:
        errors.append('freeze:' + str(path))
controllers = read(OUT / 'controller_packets.json')
finals = read(OUT / 'final_packets.json')
final_freeze = read(OUT / 'final_freeze.json')
if digest(controllers) != freeze['controller_packets_sha256']:
    errors.append('controller_packets_hash')
if digest(finals) != final_freeze['packets_sha256']:
    errors.append('final_packets_hash')
execution_failures = final_freeze['failures']
cases = {c['case_id']: c for c in read(MAIN / 'cases.json')[:2]}
questions = read(MAIN / 'proposals.json')
original = read(MAIN / 'retrieval_round2.json')['records']
updates = [read(OUT / 'first_update.json'), read(OUT / 'second_update.json')]
states, packet_checks, endpoints = {}, [], []
for cid, case in cases.items():
    for world, spec in case['worlds'].items():
        corpus = Corpus(spec['versions'], spec['graphs'], case['diff'])
        old = next(r for r in original if (r['case_id'], r['world'], r['arm']) == (cid, world, 'S'))
        sequence = [old['rounds'][0]] + [r for rows in updates for r in rows if (r['case_id'], r['world']) == (cid, world)]
        previous, previous_paths = {}, {}
        for index, row in enumerate(sequence):
            state = EvidenceState(corpus)
            state.lines = {k: set(v) for k, v in row['state']['source_lines'].items()}
            state.paths = row['state']['paths']
            if state.tokens > row['cap'] or state.tokens != row['state']['tokens']:
                errors.append(f'cap:{cid}:{world}:{index}')
            if digest(state.render()) != row['state']['packet_sha256']:
                errors.append(f'packet:{cid}:{world}:{index}')
            if any(not state.lines.get(k, set()) >= v for k, v in previous.items()):
                errors.append(f'source_loss:{cid}:{world}:{index}')
            if any(state.paths.get(k) != v for k, v in previous_paths.items()):
                errors.append(f'path_loss:{cid}:{world}:{index}')
            for key, lines in state.lines.items():
                unit = corpus.units[key]
                if not lines <= set(range(unit.start, unit.end + 1)):
                    errors.append('invalid_source_line:' + key)
            states[cid, world, index] = state
            packet_checks.append({'case_id': cid, 'world': world, 'round': index, 'tokens': state.tokens, 'cap': row['cap']})
            previous, previous_paths = state.lines, state.paths
        for key, unit in corpus.units.items():
            if unit.symbol.endswith('_tempfilepager') or unit.symbol.endswith('TrioBackend.run'):
                endpoints.append({'case_id': cid, 'world': world, 'unit': key,
                    'symbol': unit.symbol, 'version': unit.version, 'last_completed_retrieval_round': len(sequence) - 1,
                    'complete': unit.positions('L1') <= state.lines.get(key, set()),
                    'visible_lines': len(state.lines.get(key, set())), 'unit_lines': len(unit.positions('L1'))})
records = [read(f) for f in (OUT / 'raw').glob('*.json') if not f.name.endswith('.trace.json')]
usage = Counter()
for record in records:
    rid = record['run_id']
    saved = read(OUT / 'inputs' / (rid + '.json'))
    phase = record['phase']
    state = states[record['case_id'], record['world'], 1 if phase == 'controller' else 2]
    if digest(saved['prompt']) != record['prompt_sha256'] or token_count(saved['prompt']) != record['prompt_reference_tokens']:
        errors.append('prompt:' + rid)
    if digest(state.render()) != record['evidence_sha256'] or not saved['prompt'].endswith(state.render()):
        errors.append('prompt_source:' + rid)
    if record['status'] != 'ok':
        if not any(f['run_id'] == rid for f in execution_failures):
            errors.append('unrecorded_transport_failure:' + rid)
        continue
    if record['tool_audit']['tool_call_count']:
        errors.append('tool_call:' + rid)
    response = record['response']
    schema = read(MAIN / 'controller.schema.json') if phase == 'controller' else FINAL_SCHEMA
    errors.extend(rid + ':schema:' + e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(response))
    if phase == 'controller':
        errors.extend(rid + ':' + e for e in validate_controller(response, state, questions[record['case_id']]['response']['questions']))
        bound = read(OUT / 'schemas' / (rid + '.json'))
        errors.extend(rid + ':bound_schema:' + e.message for e in jsonschema.Draft202012Validator(bound).iter_errors(response))
    else:
        if (response['status'] == 'reported_issues') != bool(response['issues']):
            errors.append('status_issues:' + rid)
        if any(not set(i['evidence_ids']) <= set(state.lines) | {'DIFF'} for i in response['issues']):
            errors.append('final_unknown_id:' + rid)
    for item in record.get('usage', []):
        usage.update({k: v for k, v in item.items() if isinstance(v, (int, float))})
if len(records) != 4 + len(finals) or len(finals) + len(execution_failures) != 4:
    errors.append('unexpected_call_or_gate_count')
result = {'valid': not errors, 'errors': errors, 'calls': len(records),
    'execution_complete': len(records) == 8 and len(finals) == 4,
    'execution_failures': execution_failures, 'blocked_final_reviews': len(execution_failures),
    'successful_calls': sum(r['status'] == 'ok' for r in records),
    'observed_tool_calls': sum(r['tool_audit']['tool_call_count'] for r in records),
    'reported_usage': dict(usage), 'packet_checks': packet_checks, 'terminal_endpoints': endpoints,
    'scope': 'post_hoc_development_repair; unchanged first controller prefixes reused; no independent holdout'}
(OUT / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False))
