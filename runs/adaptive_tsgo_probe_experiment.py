"""Freeze automatic producer selection and selective runtime observations."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ace_review.adaptive_evidence import _functions, find_resource_producers, needs_runtime_probe  # noqa: E402
from ace_review.adaptive_string_probe import run_probe  # noqa: E402
from ace_review.context_necessity import assert_frozen, digest, freeze  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402

RUN = ROOT / 'runs/adaptive_tsgo_probe1'
CASE = ROOT / 'runs/adaptive_tsgo_case1'
PYTHON = ROOT / 'tools/venv-context-runtime/bin/python'
PRODUCER = 'src/internal/fourslash/_scripts/makeManual.mts'


def oracle_blocks(snapshot):
    path = Path(snapshot) / PRODUCER
    functions = {item['symbol']: item for item in _functions(path.read_text())}
    names = ['normalizeManualTestName', 'main'] if 'normalizeManualTestName' in functions else ['main']
    return [{'id': f'ORACLE-{index:02}', 'kind': 'author_specified_producer',
             'file': PRODUCER, **functions[name]} for index, name in enumerate(names, 1)]


def comparable(blocks):
    fields = ('file', 'symbol', 'start_line', 'end_line', 'content')
    return sorted([{field: block[field] for field in fields} for block in blocks],
                  key=lambda block: (block['file'], block['start_line'], block['symbol']))


def render_evidence(observation):
    return json.dumps(observation, ensure_ascii=False, sort_keys=True)


def main(root=RUN):
    assert_frozen(CASE)
    root.mkdir(exist_ok=False)
    files = ('ace_review/adaptive_evidence.py', 'ace_review/adaptive_string_probe.py',
             'ace_review/runtime_probe.py', 'runs/adaptive_tsgo_case_builder.py',
             'runs/adaptive_tsgo_probe_experiment.py', 'runs/ADAPTIVE_TSGO_PLAN_KO.md')
    for name in files:
        write_new(root / 'inputs' / name, (ROOT / name).read_text(), raw=True)
    write_new(root / 'inputs/code_hashes.json', {name: digest(ROOT / name) for name in files})
    write_new(root / 'inputs/provenance.json', {'case_freeze_sha256': digest(CASE / 'freeze.json'),
        'python': str(PYTHON), 'python_sha256': digest(PYTHON), 'model_calls': 0,
        'oracle_symbols': {'safe': ['normalizeManualTestName', 'main'], 'unsafe': ['main']},
        'selector_uses_gold': False, 'policy_designed_after_case_selection': True})
    freeze(root / 'inputs')
    diff = (CASE / 'target.diff').read_text()
    selections, observations, evidence = [], [], []
    for variant in ('safe', 'unsafe'):
        snapshot = CASE / f'{variant}_head'
        automatic = find_resource_producers(snapshot, diff)
        for index, block in enumerate(automatic, 1):
            block['id'] = f'AUTO-{index:02}'
        oracle = oracle_blocks(snapshot)
        decision = needs_runtime_probe(diff, automatic)
        matched = comparable(automatic) == comparable(oracle)
        selection = {'variant_private_for_evaluation': variant,
                     'automatic_blocks': automatic, 'oracle_blocks': oracle,
                     'retrieval_matches_oracle': matched, 'probe_decision': decision}
        selections.append(selection)
        if not matched or not decision['needed']:
            raise RuntimeError('Retrieval or uncertainty gate failed before review calls')
        result = run_probe(PYTHON, snapshot)
        write_new(root / 'raw' / f'{variant}.json', result)
        if result['status'] != 'ok':
            raise RuntimeError('Probe failed; raw result retained')
        observations.append({'variant_private_for_evaluation': variant, **result})
        evidence.append({'variant_private_for_evaluation': variant, 'id': 'RUNTIME-01',
                         'kind': 'bounded_runtime_observation',
                         'content': render_evidence(result['observation'])})
    memberships = {row['variant_private_for_evaluation']: row['observation']['membership']
                   for row in observations}
    if memberships != {'safe': True, 'unsafe': False}:
        raise RuntimeError('Probe discrimination gate failed')
    write_new(root / 'selections.json', selections)
    write_new(root / 'observations.json', observations)
    write_new(root / 'evidence.json', evidence)
    write_new(root / 'completion.json', {'complete': True, 'variants': 2,
        'automatic_retrieval_matches_oracle': 2, 'probe_triggered': 2,
        'probe_discrimination_gate': True, 'model_calls': 0})
    assert_frozen(root / 'inputs')
    assert_frozen(CASE)
    freeze(root)


if __name__ == '__main__':
    main()
