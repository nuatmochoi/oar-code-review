"""Frozen shared-proposal experiment for question-conditioned source retrieval."""
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

import jsonschema  # noqa: E402
from selector_ablation_setup import INDEX_SOURCE, load_graph  # noqa: E402
from ace_review.context_necessity import EXACT_IDS, assert_frozen, digest, freeze  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402
from ace_review.obligation import render_blocks, token_count  # noqa: E402
from ace_review.protocol_hypothesis import VERIFY  # noqa: E402
from ace_review.question_context import ARMS, PROPOSE, proposal_schema, select  # noqa: E402
from ace_review.reviewer import render_prompt, run_codex_requests  # noqa: E402

RUN = ROOT / 'runs/question_review1'
SOURCE = ROOT / 'runs/auto_context2'
CODE_FILES = ('ace_review/question_context.py', 'ace_review/auto_context.py',
              'ace_review/selector_ablation.py', 'ace_review/bm25.py',
              'ace_review/protocol_hypothesis.py', 'ace_review/obligation.py',
              'ace_review/reviewer.py', 'ace_review/context_necessity.py',
              'runs/selector_ablation_setup.py', 'runs/question_review_experiment.py')


def check_code():
    hashes = json.loads((RUN / 'code_hashes.json').read_text())
    for name, expected in hashes.items():
        if digest(ROOT / name) != expected:
            raise ValueError('Live implementation differs from frozen code: ' + name)


def prepare():
    assert_frozen(SOURCE)
    assert_frozen(INDEX_SOURCE)
    RUN.mkdir(exist_ok=False)
    write_new(RUN / 'plan.md', (ROOT / 'runs/QUESTION_REVIEW_PRIORITY_PLAN_KO.md').read_text(), raw=True)
    rows = [json.loads(line) for line in (SOURCE / 'requests.jsonl').read_text().splitlines()]
    rows = [row for row in rows if row['condition'] == 'diff']
    if len(rows) != 4:
        raise ValueError('Expected four development task variants')
    for row in rows:
        row.update(condition='questions', contexts=[], context_status={'context_tokens': 0})
    write_new(RUN / 'base_requests.jsonl', rows, jsonl=True)
    write_new(RUN / 'private_labels.json', (SOURCE / 'private_labels.json').read_text(), raw=True)
    write_new(RUN / 'proposal.txt', PROPOSE + EXACT_IDS, raw=True)
    write_new(RUN / 'verify.txt', VERIFY + EXACT_IDS, raw=True)
    write_new(RUN / 'proposal.schema.json', proposal_schema())
    write_new(RUN / 'review.schema.json', (ROOT / 'ace_review/schemas/review_output.schema.json').read_text(), raw=True)
    for index, row in enumerate(rows):
        write_new(RUN / f'proposal_inputs/{index:02}.jsonl', [row], jsonl=True)
    for name in CODE_FILES:
        write_new(RUN / 'code' / name, (ROOT / name).read_text(), raw=True)
    write_new(RUN / 'code_hashes.json', {name: digest(ROOT / name) for name in CODE_FILES})
    write_new(RUN / 'provenance.json', {
        'source_root': str(SOURCE), 'source_freeze_sha256': digest(SOURCE / 'freeze.json'),
        'index_root': str(INDEX_SOURCE), 'index_freeze_sha256': digest(INDEX_SOURCE / 'freeze.json'),
        'calls_cap': 60, 'repeats': 3, 'source_cap': 800, 'model': 'gpt-5.6-sol',
        'cohort': 'Previously observed development cases'})
    freeze(RUN)
    print('Prepared frozen proposal inputs: 4 tasks, 12 proposal calls, 48 final calls', flush=True)


def checked_call(request_path, output, prompt, schema, repeat):
    if output.exists():
        raise FileExistsError(output)
    row = json.loads(request_path.read_text())
    run_codex_requests(request_path, output, prompt, schema, model='gpt-5.6-sol',
                       jobs=1, repeat_id=repeat, timeout_seconds=300)
    pred = json.loads(output.read_text())
    if pred.get('status') != 'ok':
        raise RuntimeError('Model failure retained at ' + str(output))
    if any(pred.get(field) != row[field] for field in ('task_id', 'condition')) or pred.get('repeat') != repeat:
        raise ValueError('Model output identity mismatch')
    jsonschema.validate(pred['response'], json.loads(schema.read_text()))
    if pred.get('tool_isolation', {}).get('tool_call_count', 0):
        raise ValueError('Unexpected tool call')
    return pred


def make_final_inputs(rows, proposals):
    labels = json.loads((RUN / 'private_labels.json').read_text())
    graphs, graph_audits = {}, {}
    for row in rows:
        label = labels[row['task_id']]
        project = {'pager_stream': 'click', 'backend_options': 'anyio'}[label['family']]
        graphs[row['task_id']] = []
        for version in ('base', 'head'):
            graph, audit = load_graph(project, label['variant'] + '_' + version)
            graphs[row['task_id']].append(graph)
            graph_audits[row['task_id'] + '/' + version] = audit
    final_root = RUN / 'final_inputs'
    audit_rows, manifest = [], []
    schema = json.loads((RUN / 'review.schema.json').read_text())
    for repeat in (1, 2, 3):
        for index, row in enumerate(rows):
            proposal = proposals[index, repeat]['response']
            payload = json.dumps(proposal, ensure_ascii=False)
            for arm in ARMS:
                blocks, audit = select(*graphs[row['task_id']], row['diff'], proposal, arm)
                current = copy.deepcopy(row)
                current.update(condition=arm, contexts=blocks)
                current['contexts'].append({
                    'id': 'HYPOTHESES', 'kind': 'untrusted_hypotheses', 'file': '',
                    'start_line': None, 'end_line': None,
                    'content': 'Candidate questions and hypothetical outcomes, not evidence:\n' + payload})
                current['context_status'] = {'context_tokens': token_count(render_blocks(current['contexts']))}
                stem = f'{index:02}_{arm}_r{repeat}'
                request_path = final_root / (stem + '.jsonl')
                write_new(request_path, [current], jsonl=True)
                constrained = copy.deepcopy(schema)
                constrained['properties']['issues']['items']['properties']['evidence_ids']['items']['enum'] = (
                    ['DIFF'] + [item['id'] for item in blocks if item['id'] != 'HYPOTHESES'])
                write_new(final_root / (stem + '.schema.json'), constrained)
                audit_rows.append({'task_id': row['task_id'], 'index': index, 'repeat': repeat, **audit})
                manifest.append({'index': index, 'task_id': row['task_id'], 'condition': arm,
                                 'repeat': repeat, 'stem': stem,
                                 'shared_proposal_sha256': hashlib.sha256(payload.encode()).hexdigest(),
                                 'source_tokens': audit['source_tokens'],
                                 'prompt_sha256': hashlib.sha256(render_prompt(current, RUN / 'verify.txt').encode()).hexdigest()})
    write_new(final_root / 'source_audit.json', audit_rows)
    write_new(final_root / 'manifest.json', manifest)
    write_new(final_root / 'graph_audits.json', graph_audits)
    freeze(final_root)
    return manifest


def run(jobs=4):
    assert_frozen(RUN)
    assert_frozen(INDEX_SOURCE)
    check_code()
    write_new(RUN / 'started.json', {'model': 'gpt-5.6-sol', 'calls_cap': 60, 'jobs': jobs})
    rows = [json.loads(line) for line in (RUN / 'base_requests.jsonl').read_text().splitlines()]
    proposal_jobs = [(i, repeat) for repeat in (1, 2, 3) for i in range(len(rows))]
    random.Random(20260907).shuffle(proposal_jobs)

    def propose(job):
        index, repeat = job
        pred = checked_call(RUN / f'proposal_inputs/{index:02}.jsonl',
                            RUN / f'raw/proposal_{index:02}_r{repeat}.jsonl', RUN / 'proposal.txt',
                            RUN / 'proposal.schema.json', repeat)
        print(json.dumps({'completed': 'proposal', 'index': index, 'repeat': repeat}), flush=True)
        return job, pred

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        proposals = dict(pool.map(propose, proposal_jobs))
    check_code()
    manifest = make_final_inputs(rows, proposals)
    print('All 48 final inputs frozen; beginning final reviews', flush=True)
    random.Random(20261907).shuffle(manifest)

    def verify(item):
        stem = item['stem']
        pred = checked_call(RUN / 'final_inputs' / (stem + '.jsonl'),
                            RUN / 'raw' / ('verify_' + stem + '.jsonl'), RUN / 'verify.txt',
                            RUN / 'final_inputs' / (stem + '.schema.json'), item['repeat'])
        print(json.dumps({'completed': 'verify', 'stem': stem}), flush=True)
        return pred | {'stage': 'verify'}

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        finals = list(pool.map(verify, manifest))
    predictions = [p | {'stage': 'proposal'} for p in proposals.values()] + finals
    assert_frozen(RUN)
    assert_frozen(RUN / 'final_inputs')
    check_code()
    write_new(RUN / 'predictions.jsonl', predictions, jsonl=True)
    write_new(RUN / 'completion.json', {'complete': len(predictions) == 60, 'calls': len(predictions),
                                      'final_reviews': len(finals), 'generation_errors': 0,
                                      'schema_errors': 0, 'tool_calls': 0})
    print(json.dumps({'complete': True, 'calls': len(predictions)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare', 'run'))
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    prepare() if args.command == 'prepare' else run(args.jobs)
