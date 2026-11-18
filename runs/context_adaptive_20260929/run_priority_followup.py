"""Eight-call exploratory repair run; reuse only causally unchanged prefixes."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import jsonschema
from ace_review.auto_context import changed_query
from ace_review.context_adaptive import (Corpus, EvidenceState, digest, CONTROLLER_PROMPT,
    FINAL_PROMPT, FINAL_SCHEMA, validate_controller)
from ace_review.context_adaptive_run import ask, common
from ace_review.context_gap_priority import priority_step, bound_controller_schema
from ace_review.counterfactual import write_new

PARENT = Path(__file__).resolve().parent
SOURCE = PARENT / 'p0_v2'
OUT = PARENT / 'priority_followup_v2'
OUT.mkdir(exist_ok=False)
cases = json.loads((SOURCE / 'cases.json').read_text())[:2]
qs = json.loads((SOURCE / 'proposals.json').read_text())
audits = json.loads((SOURCE / 'retrieval_round2.json').read_text())['records']
provider_schema = json.loads((SOURCE / 'controller.provider.schema.json').read_text())
full_schema = json.loads((SOURCE / 'controller.schema.json').read_text())
write_new(OUT / 'plan.md', '''# Post-hoc S retrieval-policy repair

Exploratory development follow-up selected after observing P0 v2. Two previously
seen real-repository-derived families, two worlds each. New policy allocates at
most one whole-unit lexical slot then one whole-unit graph path per question.
All questions receive their lexical acquisition before any graph fill.
Only source-new acquisitions are eligible. Same 1,200/1,600 additional-round
caps, same original 800-token initial state, model and reviewer instruction.

Reuse the original common question and FIRST controller output only: their
input is unchanged by this policy. Generate a NEW second controller and final
review for each world: 4+4=8 new calls. Never reuse the old second controller
against changed context. Freeze modified first-update packets and policy source
before calls; freeze final packets before new final reviews. No retries.
Visible evidence/path IDs are bound as provider-schema enums, preventing citation
transcription errors. The initial repair batch's seven calls are excluded in
full after one fabricated ID; this is a complete four-world rerun.

This repairs a development retrieval bottleneck and is not a new holdout,
pre-registered superiority test, full S2G reproduction, or clean factor contrast
with the original slot policy. Label-free selection; gold used for evaluation
only. The previous 136-call main comparison remains immutable.
''', raw=True)
policy_path = ROOT / 'ace_review/context_gap_priority.py'
write_new(OUT / 'policy.py', policy_path.read_text(), raw=True)
contexts, first_audits, controller_inputs = {}, [], []
for case in cases:
    cid = case['case_id']
    questions = qs[cid]['response']['questions']
    for world, spec in case['worlds'].items():
        corpus = Corpus(spec['versions'], spec['graphs'], case['diff'])
        previous = next(r for r in audits if r['case_id'] == cid and r['world'] == world and r['arm'] == 'S')
        state = EvidenceState(corpus)
        initial = previous['rounds'][0]['state']
        state.lines = {k: set(v) for k, v in initial['source_lines'].items()}
        state.paths = dict(initial['paths'])
        assert digest(state.render()) == initial['packet_sha256']
        first = json.loads((SOURCE / 'raw' / f'{cid}_{world}_S_controller1.json').read_text())
        assert first['status'] == 'ok' and first['evidence_sha256'] == digest(state.render())
        result = priority_step(corpus, state, questions, changed_query(case['diff']), first['response'], 1200)
        first_audits.append({'case_id': cid, 'world': world, 'reused_first_controller': first['run_id'], **result})
        prompt = CONTROLLER_PROMPT + common(case) + '\nQUESTIONS:\n' + json.dumps(questions) + '\nVISIBLE EVIDENCE:\n' + state.render()
        controller_inputs.append({'run_id': f'{cid}_{world}_S_priority_controller2', 'prompt': prompt,
            'case_id': cid, 'world': world, 'arm': 'S_priority', 'phase': 'controller',
            'evidence_tokens': state.tokens, 'evidence_sha256': digest(state.render())})
        contexts[cid, world] = case, questions, corpus, state
write_new(OUT / 'first_update.json', first_audits)
write_new(OUT / 'controller_packets.json', controller_inputs)
write_new(OUT / 'freeze.json', {'policy_sha256': digest(policy_path.read_text()),
    'script_sha256': digest(Path(__file__).read_text()), 'controller_packets_sha256': digest(controller_inputs),
    'source_main_freeze_sha256': digest((SOURCE / 'freeze.json').read_text()), 'new_calls_planned': 8})
(OUT / 'raw').mkdir()


def invoke(row, schema):
    if row['phase'] == 'controller':
        _, questions, _, state = contexts[row['case_id'], row['world']]
        schema = bound_controller_schema(schema, state, questions)
        write_new(OUT / 'schemas' / (row['run_id'] + '.json'), schema)
    return ask(OUT, row['run_id'], row['prompt'], schema, {k: v for k, v in row.items() if k not in ('run_id', 'prompt')})


with ThreadPoolExecutor(max_workers=4) as pool:
    responses = list(pool.map(lambda r: invoke(r, provider_schema), controller_inputs))
finals, second_audits, failures = [], [], []
for r in responses:
    case, questions, corpus, state = contexts[r['case_id'], r['world']]
    errors = []
    if r['status'] == 'ok':
        errors.extend(str(e.message) for e in jsonschema.Draft202012Validator(full_schema).iter_errors(r['response']))
        errors.extend(validate_controller(r['response'], state, questions))
    if r['status'] != 'ok' or errors:
        failures.append({'run_id': r['run_id'], 'errors': errors, 'status': r['status']})
        continue
    result = priority_step(corpus, state, questions, changed_query(case['diff']), r['response'], 1600)
    second_audits.append({'case_id': r['case_id'], 'world': r['world'], **result})
    prompt = FINAL_PROMPT + common(case) + '\nQUESTIONS:\n' + json.dumps(questions) + '\nREPOSITORY EVIDENCE:\n' + state.render()
    finals.append({'run_id': f'{r["case_id"]}_{r["world"]}_S_priority_final', 'prompt': prompt,
        'phase': 'final', 'case_id': r['case_id'], 'world': r['world'], 'arm': 'S_priority',
        'evidence_tokens': state.tokens, 'evidence_ids': sorted(state.lines), 'evidence_sha256': digest(state.render())})
write_new(OUT / 'second_update.json', second_audits)
write_new(OUT / 'final_packets.json', finals)
write_new(OUT / 'final_freeze.json', {'packets_sha256': digest(finals), 'failures': failures})
with ThreadPoolExecutor(max_workers=4) as pool:
    predictions = list(pool.map(lambda r: invoke(r, FINAL_SCHEMA), finals))
write_new(OUT / 'predictions.json', predictions)
print(json.dumps({'new_calls': len(responses) + len(predictions), 'finals': len(predictions), 'controller_failures': failures}))
