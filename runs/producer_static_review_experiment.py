"""Matched producer-source versus production-runtime development ablation."""
import argparse
import ast
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
from runs.probe_review_experiment import PROMPT, sha  # noqa: E402
from runs.question_review_experiment import checked_call  # noqa: E402

RUN = ROOT / 'runs/producer_static_review1'
QUESTION = ROOT / 'runs/question_review1'
SOURCE = ROOT / 'runs/answer_source1'
LINEAGE = ROOT / 'runs/object_lineage1'
INDEX = ROOT / 'runs/auto_context_dependency_check1'
ARMS = ('retrieved_only', 'producer_static', 'production_runtime')


def extract_function(path, name):
    path = Path(path)
    source = path.read_text()
    tree = ast.parse(source)
    matches = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == name]
    if len(matches) != 1:
        raise ValueError(f'Expected one module function named {name}')
    node = matches[0]
    start = min([node.lineno] + [item.lineno for item in node.decorator_list])
    lines = source.splitlines()
    return {'file': 'src/click/_termui_impl.py', 'start_line': start,
            'end_line': node.end_lineno, 'content': '\n'.join(lines[start - 1:node.end_lineno])}


def static_blocks(snapshot):
    path = Path(snapshot) / 'src/click/_termui_impl.py'
    specs = (('STATIC-SELECTOR-01', '_pager_contextmanager'),
             ('STATIC-PRODUCER-01', '_tempfilepager'))
    blocks = []
    for block_id, name in specs:
        block = extract_function(path, name)
        blocks.append({'id': block_id, 'kind': 'exact_source', **block})
    return blocks


def production_types(observation):
    data = copy.deepcopy(observation)
    for key in ('consumer_is_producer', 'consumer_buffer_is_producer_buffer'):
        data.pop(key, None)
    for key in ('capability_checks', 'binaryio_checks'):
        for item in data[key]:
            item.pop('same_as_producer', None)
    return data


def build_arms(row, source, proposal, static, runtime, instruction):
    additions = {
        'retrieved_only': [{'id': 'CONTROL-01', 'kind': 'length_control', 'file': '',
            'start_line': None, 'end_line': None,
            'content': 'No additional behavioral or source evidence is supplied.'}],
        'producer_static': copy.deepcopy(static),
        'production_runtime': [{'id': 'RUNTIME-01', 'kind': 'bounded_runtime_observation',
            'file': '', 'start_line': None, 'end_line': None,
            'content': json.dumps(runtime, ensure_ascii=False, sort_keys=True)}],
    }
    rows = []
    for arm in ARMS:
        current = copy.deepcopy(row)
        contexts = copy.deepcopy(source) + additions[arm]
        contexts.extend([
            {'id': 'LENGTH-CONTROL', 'kind': 'length_control', 'file': '',
             'start_line': None, 'end_line': None,
             'content': 'Length-control padding (not evidence):'},
            {'id': 'HYPOTHESES', 'kind': 'untrusted_hypotheses', 'file': '',
             'start_line': None, 'end_line': None,
             'content': 'Candidate questions and hypothetical outcomes, not evidence:\n' +
                        json.dumps(proposal, ensure_ascii=False)},
        ])
        current.update(condition=arm, contexts=contexts)
        rows.append(current)
    target = max(token_count(render_prompt(item, instruction)) for item in rows) + 32
    suffix = '\nEnd length control.'
    for item in rows:
        padding = item['contexts'][-2]
        base = padding['content']
        for count in range(target + 1):
            padding['content'] = base + ' .' * count + suffix
            if token_count(render_prompt(item, instruction)) == target:
                break
        else:
            raise ValueError('Cannot match whole-prompt token counts')
        item['context_status'] = {'context_tokens': token_count(render_blocks(item['contexts']))}
    return rows


def prepare(root=RUN):
    for parent in (QUESTION, SOURCE, LINEAGE, INDEX):
        assert_frozen(parent)
    root.mkdir(exist_ok=False)
    write_new(root / 'plan.md', (ROOT / 'runs/PRODUCER_STATIC_PLAN_KO.md').read_text(), raw=True)
    write_new(root / 'verify.txt', PROMPT, raw=True)
    rows = [json.loads(line) for line in (QUESTION / 'base_requests.jsonl').read_text().splitlines()][:2]
    sources = {(item['task_id'], item['repeat']): item['contexts'] for item in
        map(json.loads, (SOURCE / 'selections/requests.jsonl').read_text().splitlines())
        if item['condition'] == 'answer_source'}
    observations = {item['cell']: item['observation'] for item in
                    json.loads((LINEAGE / 'observations.json').read_text())}
    schema = json.loads((QUESTION / 'review.schema.json').read_text())
    manifest = []
    for index, row in enumerate(rows):
        variant = 'safe' if index == 0 else 'unsafe'
        static = static_blocks(INDEX / f'indices/click/{variant}_head')
        runtime = production_types(observations[f'{variant}_head'])
        for repeat in (1, 2, 3):
            proposal = json.loads((QUESTION / f'raw/proposal_{index:02}_r{repeat}.jsonl').read_text())['response']
            source = sources[row['task_id'], repeat]
            for current in build_arms(row, source, proposal, static, runtime, root / 'verify.txt'):
                arm = current['condition']
                stem = f'{index:02}_{arm}_r{repeat}'
                write_new(root / 'inputs' / (stem + '.jsonl'), [current], jsonl=True)
                constrained = copy.deepcopy(schema)
                constrained['properties']['issues']['items']['properties']['evidence_ids']['items']['enum'] = (
                    ['DIFF'] + [b['id'] for b in current['contexts'] if b['id'] not in
                                ('HYPOTHESES', 'LENGTH-CONTROL', 'CONTROL-01')])
                write_new(root / 'inputs' / (stem + '.schema.json'), constrained)
                prompt = render_prompt(current, root / 'verify.txt')
                manifest.append({'index': index, 'repeat': repeat, 'condition': arm,
                    'task_id': row['task_id'], 'stem': stem, 'source_sha256': sha(source),
                    'proposal_sha256': sha(proposal), 'static_sha256': sha(static),
                    'runtime_sha256': sha(runtime), 'prompt_reference_tokens': token_count(prompt),
                    'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()})
    for index in (0, 1):
        for repeat in (1, 2, 3):
            group = [m for m in manifest if m['index'] == index and m['repeat'] == repeat]
            if len(group) != 3 or len({m['prompt_reference_tokens'] for m in group}) != 1:
                raise ValueError('Whole-prompt matching failed')
    write_new(root / 'manifest.json', manifest)
    files = ('runs/producer_static_review_experiment.py', 'runs/probe_review_experiment.py',
             'runs/question_review_experiment.py', 'ace_review/reviewer.py',
             'ace_review/obligation.py', 'ace_review/context_necessity.py',
             'ace_review/protocol_hypothesis.py')
    for name in files:
        write_new(root / 'code' / name, (ROOT / name).read_text(), raw=True)
    write_new(root / 'code_hashes.json', {name: digest(ROOT / name) for name in files})
    write_new(root / 'provenance.json', {'new_calls': 18, 'reused_proposals': 6,
        'model': 'gpt-5.6-sol', 'human_selected_static_functions':
        ['_pager_contextmanager', '_tempfilepager'], 'parent_freezes':
        {str(p): digest(p / 'freeze.json') for p in (QUESTION, SOURCE, LINEAGE, INDEX)}})
    freeze(root)
    return manifest


def run(root=RUN, jobs=4):
    assert_frozen(root)
    for name, expected in json.loads((root / 'code_hashes.json').read_text()).items():
        if digest(ROOT / name) != expected:
            raise ValueError('Live code differs from frozen code')
    write_new(root / 'started.json', {'calls': 18, 'jobs': jobs})
    manifest = json.loads((root / 'manifest.json').read_text())
    random.Random(20260910).shuffle(manifest)

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
    write_new(root / 'completion.json', {'complete': len(predictions) == 18,
        'new_calls': len(predictions), 'generation_errors': 0,
        'schema_errors': 0, 'tool_calls': 0})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare', 'run'))
    args = parser.parse_args()
    prepare() if args.command == 'prepare' else run()
