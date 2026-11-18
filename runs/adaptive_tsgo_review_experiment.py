"""Frozen four-arm review of automatic static and uncertainty-triggered evidence."""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ace_review.context_necessity import assert_frozen, digest, freeze  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402
from ace_review.obligation import render_blocks, token_count  # noqa: E402
from ace_review.reviewer import render_prompt  # noqa: E402
from runs.probe_review_experiment import PROMPT  # noqa: E402
from runs.question_review_experiment import checked_call  # noqa: E402

RUN = ROOT / 'runs/adaptive_tsgo_review1'
CASE = ROOT / 'runs/adaptive_tsgo_case1'
PROBE = ROOT / 'runs/adaptive_tsgo_probe1'
QUESTION = ROOT / 'runs/question_review1'
ARMS = ('diff_only', 'automatic_static', 'adaptive_probe', 'oracle_producer')
TARGET_PROMPT_TOKENS = 4096


def base_request(variant):
    return {'task_id': f'adaptive-tsgo-{variant}', 'condition': 'unassigned',
            'repository': 'microsoft/typescript-go', 'language': 'TypeScript',
            'pr': {'title': 'Add manual tests list',
                   'description': 'Skip auto-generation for tests maintained manually.'},
            'diff': (CASE / 'target.diff').read_text(), 'contexts': []}


def build_arms(row, selection, runtime, instruction):
    automatic = copy.deepcopy(selection['automatic_blocks'])
    oracle = copy.deepcopy(selection['oracle_blocks'])
    runtime_block = {'id': runtime['id'], 'kind': runtime['kind'], 'file': '',
                     'start_line': None, 'end_line': None,
                     'content': runtime['content']}
    evidence = {
        'diff_only': [{'id': 'CONTROL-01', 'kind': 'length_control', 'file': '',
                       'start_line': None, 'end_line': None,
                       'content': 'No additional source or behavioral evidence is supplied.'}],
        'automatic_static': automatic,
        'adaptive_probe': copy.deepcopy(automatic) + [runtime_block],
        'oracle_producer': oracle,
    }
    rows = []
    suffix = '\nEnd length control.'
    for arm in ARMS:
        current = copy.deepcopy(row)
        current.update(condition=arm, contexts=evidence[arm] + [{
            'id': 'LENGTH-CONTROL', 'kind': 'length_control', 'file': '',
            'start_line': None, 'end_line': None,
            'content': 'Length-control padding (not evidence):'}])
        padding = current['contexts'][-1]
        base = padding['content']
        padding['content'] = base + suffix
        initial = token_count(render_prompt(current, instruction))
        if initial > TARGET_PROMPT_TOKENS:
            raise ValueError('Evidence exceeds fixed whole-prompt budget')
        count = TARGET_PROMPT_TOKENS - initial
        for _ in range(16):
            padding['content'] = base + ' .' * count + suffix
            measured = token_count(render_prompt(current, instruction))
            if measured == TARGET_PROMPT_TOKENS:
                break
            count += TARGET_PROMPT_TOKENS - measured
            if count < 0:
                raise ValueError('Cannot match fixed whole-prompt budget')
        else:
            raise ValueError('Cannot match fixed whole-prompt budget')
        current['context_status'] = {'context_tokens': token_count(render_blocks(current['contexts']))}
        rows.append(current)
    return rows


def prepare(root=RUN):
    for parent in (CASE, PROBE, QUESTION):
        assert_frozen(parent)
    root.mkdir(exist_ok=False)
    write_new(root / 'plan.md', (ROOT / 'runs/ADAPTIVE_TSGO_PLAN_KO.md').read_text(), raw=True)
    write_new(root / 'verify.txt', PROMPT, raw=True)
    write_new(root / 'private_labels.json', (CASE / 'private_labels.json').read_text(), raw=True)
    selections = {row['variant_private_for_evaluation']: row for row in
                  json.loads((PROBE / 'selections.json').read_text())}
    runtime = {row['variant_private_for_evaluation']: row for row in
               json.loads((PROBE / 'evidence.json').read_text())}
    schema = json.loads((QUESTION / 'review.schema.json').read_text())
    manifest = []
    for variant_index, variant in enumerate(('safe', 'unsafe')):
        for repeat in (1, 2, 3):
            for current in build_arms(base_request(variant), selections[variant],
                                      runtime[variant], root / 'verify.txt'):
                arm = current['condition']
                stem = f'{variant_index:02}_{arm}_r{repeat}'
                write_new(root / 'inputs' / (stem + '.jsonl'), [current], jsonl=True)
                constrained = copy.deepcopy(schema)
                allowed = ['DIFF'] + [block['id'] for block in current['contexts']
                                      if block['id'] not in ('CONTROL-01', 'LENGTH-CONTROL')]
                constrained['properties']['issues']['items']['properties']['evidence_ids']['items']['enum'] = allowed
                write_new(root / 'inputs' / (stem + '.schema.json'), constrained)
                prompt = render_prompt(current, root / 'verify.txt')
                manifest.append({'index': variant_index, 'variant_private_for_evaluation': variant,
                    'repeat': repeat, 'condition': arm, 'task_id': current['task_id'],
                    'stem': stem, 'prompt_reference_tokens': token_count(prompt),
                    'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                    'context_sha256': hashlib.sha256(render_blocks(current['contexts']).encode()).hexdigest()})
    if len(manifest) != 24 or {row['prompt_reference_tokens'] for row in manifest} != {TARGET_PROMPT_TOKENS}:
        raise ValueError('Incomplete or unmatched inputs')
    diff_prompts = {row['prompt_sha256'] for row in manifest if row['condition'] == 'diff_only'}
    if len(diff_prompts) != 1:
        raise ValueError('Safe and unsafe diff-only prompts must be identical')
    write_new(root / 'manifest.json', manifest)
    files = ('runs/adaptive_tsgo_review_experiment.py', 'runs/question_review_experiment.py',
             'runs/probe_review_experiment.py', 'ace_review/reviewer.py',
             'ace_review/obligation.py', 'ace_review/context_necessity.py',
             'ace_review/protocol_hypothesis.py')
    for name in files:
        write_new(root / 'code' / name, (ROOT / name).read_text(), raw=True)
    write_new(root / 'code_hashes.json', {name: digest(ROOT / name) for name in files})
    write_new(root / 'provenance.json', {'new_calls': 24, 'model': 'gpt-5.6-sol',
        'proposal_calls': 0, 'target_prompt_tokens': TARGET_PROMPT_TOKENS,
        'parent_freezes': {str(parent): digest(parent / 'freeze.json')
                           for parent in (CASE, PROBE, QUESTION)},
        'counterfactual_blind_holdout': False})
    freeze(root)
    return manifest


def run(root=RUN, jobs=4):
    assert_frozen(root)
    for name, expected in json.loads((root / 'code_hashes.json').read_text()).items():
        if digest(ROOT / name) != expected:
            raise ValueError('Live code differs from frozen code')
    write_new(root / 'started.json', {'calls': 24, 'jobs': jobs})
    manifest = json.loads((root / 'manifest.json').read_text())
    random.Random(20260911).shuffle(manifest)

    def call(item):
        stem = item['stem']
        result = checked_call(root / 'inputs' / (stem + '.jsonl'),
                              root / 'raw' / (stem + '.jsonl'), root / 'verify.txt',
                              root / 'inputs' / (stem + '.schema.json'), item['repeat'])
        print(json.dumps({'completed': stem}), flush=True)
        return result | {'index': item['index'], 'stage': 'verify'}

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        predictions = list(pool.map(call, manifest))
    assert_frozen(root)
    write_new(root / 'predictions.jsonl', predictions, jsonl=True)
    write_new(root / 'completion.json', {'complete': len(predictions) == 24,
        'new_calls': len(predictions), 'generation_errors': 0,
        'schema_errors': 0, 'tool_calls': 0})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare', 'run'))
    args = parser.parse_args()
    prepare() if args.command == 'prepare' else run()
