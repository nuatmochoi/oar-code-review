"""Frozen three-arm, source-matched runtime-evidence review experiment."""
import argparse
import copy
import hashlib
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'runs'))
from question_review_experiment import checked_call  # noqa: E402
from ace_review.context_necessity import EXACT_IDS, assert_frozen, digest, freeze  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402
from ace_review.obligation import render_blocks, token_count  # noqa: E402
from ace_review.protocol_hypothesis import VERIFY  # noqa: E402
from ace_review.reviewer import render_prompt  # noqa: E402

RUN = ROOT / 'runs/probe_review1'
QUESTION = ROOT / 'runs/question_review1'
SOURCE = ROOT / 'runs/answer_source1'
PROBE = ROOT / 'runs/runtime_probe1'
ARMS = ('source_only', 'environment_control', 'targeted_probe')
PROMPT = VERIFY.replace('Cite DIFF and exact CTX identifiers.', 'Cite DIFF and exact supplied evidence identifiers.') + EXACT_IDS + '''
Runtime observations, when supplied, establish only their explicitly stated
input, environment, and interception scope. They are not complete end-to-end
executions. Do not infer unobserved production paths or general safety from them.'''


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def runtime_blocks(evidence):
    observed = json.loads(evidence['content'])
    definition = ('Test: isinstance(standard binary TemporaryFile, typing.BinaryIO).' if 'predicate' in observed else
                  'Test: anyio.run with backend="trio" and a nonempty {"clock": sentinel} mapping. '
                  'First intercept the backend.run boundary; restore it and separately intercept trio.run while executing the original adapter.')
    common = {'id': 'RUNTIME-01', 'kind': 'bounded_runtime_observation', 'file': '',
              'start_line': None, 'end_line': None}
    suffix = '\nEnd runtime block.'
    target = common | {'content': definition + '\nRecorded observation: ' + evidence['content'] + '\nScope: ' + evidence['limitation'] + suffix}
    environment = {k: observed[k] for k in ('python_version', 'trio_version') if k in observed}
    control = common | {'content': 'Recorded environment metadata only: ' + json.dumps(environment) +
                                  '\nNo behavioral observation is supplied.\nLength-control padding (not observations):'}
    desired = token_count(render_blocks([target]))
    base = control['content']
    for count in range(desired + 1):
        control['content'] = base + ' .' * count + suffix
        if token_count(render_blocks([control])) == desired:
            return target, control
    raise ValueError('Cannot exactly match environment control tokens')


def compose(row, source, proposal, evidence, arm):
    if arm not in ARMS:
        raise ValueError('Unknown arm')
    output = copy.deepcopy(row)
    output.update(condition=arm, contexts=copy.deepcopy(source))
    target, control = runtime_blocks(evidence)
    if arm != 'source_only':
        output['contexts'].append(target if arm == 'targeted_probe' else control)
    output['contexts'].append({'id': 'HYPOTHESES', 'kind': 'untrusted_hypotheses', 'file': '',
                               'start_line': None, 'end_line': None,
                               'content': 'Candidate questions and hypothetical outcomes, not evidence:\n' + json.dumps(proposal, ensure_ascii=False)})
    output['context_status'] = {'context_tokens': token_count(render_blocks(output['contexts']))}
    return output


def prepare(root=RUN):
    for path in (QUESTION, SOURCE, PROBE):
        assert_frozen(path)
    root.mkdir(exist_ok=False)
    write_new(root / 'plan.md', (ROOT / 'runs/PROBE_REVIEW_PLAN_KO.md').read_text(), raw=True)
    write_new(root / 'verify.txt', PROMPT, raw=True)
    write_new(root / 'private_labels.json', (QUESTION / 'private_labels.json').read_text(), raw=True)
    rows = [json.loads(s) for s in (QUESTION / 'base_requests.jsonl').read_text().splitlines()]
    sources = {(r['task_id'], r['repeat']): r['contexts'] for r in
               (json.loads(s) for s in (SOURCE / 'selections/requests.jsonl').read_text().splitlines()) if r['condition'] == 'answer_source'}
    evidence = {r['task_id']: r for r in (json.loads(s) for s in (PROBE / 'evidence.jsonl').read_text().splitlines())}
    schema = json.loads((QUESTION / 'review.schema.json').read_text())
    manifest = []
    for index, row in enumerate(rows):
        for repeat in (1, 2, 3):
            proposal = json.loads((QUESTION / f'raw/proposal_{index:02}_r{repeat}.jsonl').read_text())['response']
            source = sources[row['task_id'], repeat]
            for arm in ARMS:
                current = compose(row, source, proposal, evidence[row['task_id']], arm)
                stem = f'{index:02}_{arm}_r{repeat}'
                write_new(root / 'inputs' / (stem + '.jsonl'), [current], jsonl=True)
                constrained = copy.deepcopy(schema)
                constrained['properties']['issues']['items']['properties']['evidence_ids']['items']['enum'] = (
                    ['DIFF'] + [b['id'] for b in current['contexts'] if b['id'] != 'HYPOTHESES'])
                write_new(root / 'inputs' / (stem + '.schema.json'), constrained)
                prompt = render_prompt(current, root / 'verify.txt')
                manifest.append({'index': index, 'repeat': repeat, 'condition': arm, 'task_id': row['task_id'],
                                 'stem': stem, 'source_sha256': sha(source), 'proposal_sha256': sha(proposal),
                                 'source_tokens': token_count(render_blocks(source)),
                                 'prompt_reference_tokens': token_count(prompt), 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()})
            group = manifest[-3:]
            if group[1]['prompt_reference_tokens'] != group[2]['prompt_reference_tokens']:
                raise ValueError('Whole prompt token matching failed')
    write_new(root / 'manifest.json', manifest)
    files = ('runs/probe_review_experiment.py', 'runs/question_review_experiment.py',
             'ace_review/reviewer.py', 'ace_review/obligation.py', 'ace_review/protocol_hypothesis.py',
             'ace_review/context_necessity.py')
    for name in files:
        write_new(root / 'code' / name, (ROOT / name).read_text(), raw=True)
    write_new(root / 'code_hashes.json', {name: digest(ROOT / name) for name in files})
    write_new(root / 'provenance.json', {'parent_freezes': {str(p): digest(p / 'freeze.json') for p in (QUESTION, SOURCE, PROBE)},
                                       'new_calls': 36, 'reused_proposals': 12, 'model': 'gpt-5.6-sol',
                                       'control': 'Token-matched environment metadata with disclosed inert padding'})
    freeze(root)
    return manifest


def run(root=RUN, jobs=4):
    assert_frozen(root)
    hashes = json.loads((root / 'code_hashes.json').read_text())
    if any(digest(ROOT / name) != expected for name, expected in hashes.items()):
        raise ValueError('Live code differs from frozen code')
    write_new(root / 'started.json', {'calls': 36, 'jobs': jobs})
    manifest = json.loads((root / 'manifest.json').read_text())
    random.Random(20260908).shuffle(manifest)
    def call(item):
        stem = item['stem']
        result = checked_call(root / 'inputs' / (stem + '.jsonl'), root / 'raw' / (stem + '.jsonl'),
                              root / 'verify.txt', root / 'inputs' / (stem + '.schema.json'), item['repeat'])
        print(json.dumps({'completed': stem}), flush=True)
        return result | {'index': item['index'], 'stage': 'verify'}
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        predictions = list(pool.map(call, manifest))
    assert_frozen(root)
    write_new(root / 'predictions.jsonl', predictions, jsonl=True)
    write_new(root / 'completion.json', {'complete': len(predictions) == 36, 'new_calls': len(predictions),
                                       'generation_errors': 0, 'schema_errors': 0, 'tool_calls': 0})
    print('All 36 final reviews complete.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare', 'run'))
    args = parser.parse_args()
    prepare() if args.command == 'prepare' else run()
