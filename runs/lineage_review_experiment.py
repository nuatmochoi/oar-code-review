"""Frozen development probe and matched 18-call identity-field ablation."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import random
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runs.probe_review_experiment import PROMPT, runtime_blocks, sha  # noqa: E402
from runs.question_review_experiment import checked_call  # noqa: E402
from ace_review.context_necessity import assert_frozen, digest, freeze  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402
from ace_review.object_lineage_probe import run_lineage  # noqa: E402
from ace_review.obligation import render_blocks, token_count  # noqa: E402
from ace_review.reviewer import render_prompt  # noqa: E402

RUN = ROOT / 'runs/lineage_review1'
PROBE = ROOT / 'runs/object_lineage1'
QUESTION = ROOT / 'runs/question_review1'
SOURCE = ROOT / 'runs/answer_source1'
INDEX = ROOT / 'runs/auto_context_dependency_check1'
OLD_PROBE = ROOT / 'runs/runtime_probe1'
ARMS = ('isolated_predicate', 'production_types', 'production_lineage')


def evidence_blocks(observation, old):
    target, _ = runtime_blocks(old)
    types = copy.deepcopy(observation)
    for key in ('consumer_is_producer', 'consumer_buffer_is_producer_buffer'):
        types.pop(key, None)
    for key in ('capability_checks', 'binaryio_checks'):
        for check in types[key]:
            check.pop('same_as_producer', None)
    common = {'id': 'RUNTIME-01', 'kind': 'bounded_runtime_observation', 'file': '',
              'start_line': None, 'end_line': None}
    return {'isolated_predicate': target,
            'production_types': common | {'content': json.dumps(types)},
            'production_lineage': common | {'content': json.dumps(observation)}}


def match_prompts(row, source, proposal, blocks, prompt_path):
    rows = []
    for arm, block in blocks.items():
        current = copy.deepcopy(row)
        current.update(condition=arm, contexts=copy.deepcopy(source) + [copy.deepcopy(block),
            {'id': 'HYPOTHESES', 'kind': 'untrusted_hypotheses', 'file': '', 'start_line': None,
             'end_line': None, 'content': 'Candidate questions and hypothetical outcomes, not evidence:\n' +
             json.dumps(proposal, ensure_ascii=False)}])
        current['contexts'][-2]['content'] += '\nLength-control padding (not observations):'
        rows.append(current)
    suffix = '\nEnd runtime block.'
    desired = max(token_count(render_prompt(r, prompt_path) + suffix) for r in rows) + 8
    for current in rows:
        block = current['contexts'][-2]
        base = block['content']
        for count in range(desired + 1):
            block['content'] = base + ' .' * count + suffix
            if token_count(render_prompt(current, prompt_path)) == desired:
                break
        else:
            raise ValueError('Cannot token match whole prompt')
        current['context_status'] = {'context_tokens': token_count(render_blocks(current['contexts']))}
    return rows


def probes():
    assert_frozen(INDEX)
    PROBE.mkdir(exist_ok=False)
    for name in ('ace_review/object_lineage_probe.py', 'ace_review/runtime_probe.py',
                 'runs/lineage_review_experiment.py', 'tests/test_object_lineage_probe.py',
                 'runs/OBJECT_LINEAGE_PLAN_KO.md'):
        write_new(PROBE / 'inputs' / name, (ROOT / name).read_text(), raw=True)
    python = ROOT / 'tools/venv-context-runtime/bin/python'
    write_new(PROBE / 'inputs/provenance.json', {'python': str(python), 'python_sha256': digest(python),
        'index_freeze_sha256': digest(INDEX / 'freeze.json'), 'development_tests_before_freeze': True})
    freeze(PROBE / 'inputs')
    records = []
    for cell in ('safe_base', 'safe_head', 'unsafe_base', 'unsafe_head'):
        result = run_lineage(python, INDEX / 'indices/click' / cell)
        write_new(PROBE / 'raw' / (cell + '.json'), result)
        if result['status'] != 'ok':
            raise RuntimeError('Probe failed; raw result retained')
        records.append({'cell': cell, **result})
        print(json.dumps({'cell': cell, 'observation': result['observation']}), flush=True)
    write_new(PROBE / 'observations.json', records)
    assert_frozen(PROBE / 'inputs')
    assert_frozen(INDEX)
    freeze(PROBE)


def prepare():
    for path in (QUESTION, SOURCE, OLD_PROBE, PROBE):
        assert_frozen(path)
    RUN.mkdir(exist_ok=False)
    write_new(RUN / 'plan.md', (ROOT / 'runs/OBJECT_LINEAGE_PLAN_KO.md').read_text(), raw=True)
    write_new(RUN / 'verify.txt', PROMPT, raw=True)
    rows = [json.loads(s) for s in (QUESTION / 'base_requests.jsonl').read_text().splitlines()][:2]
    sources = {(r['task_id'], r['repeat']): r['contexts'] for r in
        map(json.loads, (SOURCE / 'selections/requests.jsonl').read_text().splitlines()) if r['condition'] == 'answer_source'}
    old = {r['task_id']: r for r in map(json.loads, (OLD_PROBE / 'evidence.jsonl').read_text().splitlines())}
    observations = {r['cell']: r['observation'] for r in json.loads((PROBE / 'observations.json').read_text())}
    schema = json.loads((QUESTION / 'review.schema.json').read_text())
    manifest = []
    for index, row in enumerate(rows):
        blocks = evidence_blocks(observations[('safe' if index == 0 else 'unsafe') + '_head'], old[row['task_id']])
        for repeat in (1, 2, 3):
            proposal = json.loads((QUESTION / f'raw/proposal_{index:02}_r{repeat}.jsonl').read_text())['response']
            source = sources[row['task_id'], repeat]
            matched = match_prompts(row, source, proposal, blocks, RUN / 'verify.txt')
            for current in matched:
                arm = current['condition']
                stem = f'{index:02}_{arm}_r{repeat}'
                write_new(RUN / 'inputs' / (stem + '.jsonl'), [current], jsonl=True)
                constrained = copy.deepcopy(schema)
                constrained['properties']['issues']['items']['properties']['evidence_ids']['items']['enum'] = (
                    ['DIFF'] + [b['id'] for b in current['contexts'] if b['id'] != 'HYPOTHESES'])
                write_new(RUN / 'inputs' / (stem + '.schema.json'), constrained)
                prompt = render_prompt(current, RUN / 'verify.txt')
                manifest.append({'index': index, 'condition': arm, 'repeat': repeat, 'stem': stem,
                    'task_id': row['task_id'], 'source_sha256': sha(source), 'proposal_sha256': sha(proposal),
                    'prompt_reference_tokens': token_count(prompt), 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()})
    write_new(RUN / 'manifest.json', manifest)
    files = ('runs/lineage_review_experiment.py', 'runs/probe_review_experiment.py',
             'runs/question_review_experiment.py', 'ace_review/reviewer.py', 'ace_review/obligation.py',
             'ace_review/context_necessity.py', 'ace_review/protocol_hypothesis.py')
    for name in files:
        write_new(RUN / 'code' / name, (ROOT / name).read_text(), raw=True)
    write_new(RUN / 'code_hashes.json', {name: digest(ROOT / name) for name in files})
    write_new(RUN / 'provenance.json', {'new_calls': 18, 'model': 'gpt-5.6-sol',
        'reused_proposals': 6, 'parent_freezes': {str(p): digest(p / 'freeze.json') for p in (QUESTION, SOURCE, OLD_PROBE, PROBE)}})
    freeze(RUN)


def run():
    assert_frozen(RUN)
    for name, expected in json.loads((RUN / 'code_hashes.json').read_text()).items():
        if digest(ROOT / name) != expected:
            raise ValueError('Live code differs from frozen code')
    write_new(RUN / 'started.json', {'calls': 18, 'jobs': 4})
    manifest = json.loads((RUN / 'manifest.json').read_text())
    random.Random(20260909).shuffle(manifest)
    def call(item):
        stem = item['stem']
        result = checked_call(RUN / 'inputs' / (stem + '.jsonl'), RUN / 'raw' / (stem + '.jsonl'),
                              RUN / 'verify.txt', RUN / 'inputs' / (stem + '.schema.json'), item['repeat'])
        print(json.dumps({'completed': stem}), flush=True)
        return result | {'index': item['index'], 'stage': 'verify'}
    with ThreadPoolExecutor(max_workers=4) as pool:
        predictions = list(pool.map(call, manifest))
    assert_frozen(RUN)
    write_new(RUN / 'predictions.jsonl', predictions, jsonl=True)
    write_new(RUN / 'completion.json', {'complete': len(predictions) == 18, 'new_calls': len(predictions),
                                       'generation_errors': 0, 'schema_errors': 0, 'tool_calls': 0})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('probes', 'prepare', 'run'))
    {'probes': probes, 'prepare': prepare, 'run': run}[parser.parse_args().command]()
