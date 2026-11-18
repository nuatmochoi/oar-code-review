"""Fixed two-stage reviewer with per-input evidence enums and frozen controls."""
import argparse
import copy
import hashlib
import json
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jsonschema

from .counterfactual import write_new
from .obligation import render_blocks, token_count
from .protocol_hypothesis import GENERIC, VERIFY, clean_row, hypothesis_payload, proposal_schema
from .reviewer import render_prompt, run_codex_requests

ARMS = ('diff', 'irrelevant', 'relevant')
EXACT_IDS = '\nUse evidence_ids only from the supplied JSON schema enum. Cite DIFF literally, not a path-qualified variant. Hypotheses are not evidence. The input is a production-source patch, not necessarily a complete PR. Do not assume missing code is absent from the repository.'


def constrain_schema(schema, row):
    schema = copy.deepcopy(schema)
    name = 'hypotheses' if 'hypotheses' in schema['properties'] else 'issues'
    ids = ['DIFF'] + [b['id'] for b in row['contexts'] if b['id'] != 'HYPOTHESES']
    schema['properties'][name]['items']['properties']['evidence_ids']['items']['enum'] = sorted(set(ids))
    return schema


def make_arms(row, irrelevant):
    rows = []
    for arm, blocks in [('diff', []), ('irrelevant', irrelevant), ('relevant', row['contexts'])]:
        result = clean_row(row) | {'condition': arm}
        result['contexts'] = copy.deepcopy(blocks)
        result['context_status'] = {'context_tokens': token_count(render_blocks(blocks))}
        rows.append(result)
    return rows


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(root):
    root = Path(root)
    files = {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob('*')) if p.is_file()}
    write_new(root / 'freeze.json', {'files': files})


def assert_frozen(root):
    root = Path(root)
    changed = [name for name, sha in json.loads((root / 'freeze.json').read_text())['files'].items()
               if not (root / name).is_file() or digest(root / name) != sha]
    if changed:
        raise ValueError('Frozen files changed: ' + str(changed))


def prepare(cases, output):
    root = Path(output); root.mkdir(parents=True, exist_ok=False)
    rows = [json.loads(line) for line in Path(cases).read_text().splitlines()]
    if not rows or len({(r['task_id'], r['condition']) for r in rows}) != len(rows):
        raise ValueError('Missing or duplicate cases')
    write_new(root / 'requests.jsonl', rows, jsonl=True)
    write_new(root / 'proposal.txt', GENERIC + EXACT_IDS, raw=True)
    write_new(root / 'verify.txt', VERIFY + EXACT_IDS, raw=True)
    review_schema = json.loads((Path(__file__).parent / 'schemas/review_output.schema.json').read_text())
    manifest = []
    for index, row in enumerate(rows):
        prefix = f'inputs/{index:03}'
        write_new(root / f'{prefix}.jsonl', [row], jsonl=True)
        for stage, schema in [('proposal', proposal_schema()), ('verify', review_schema)]:
            write_new(root / f'{prefix}_{stage}.schema.json', constrain_schema(schema, row))
        manifest.append({'index': index, 'task_id': row['task_id'], 'condition': row['condition'],
                         'source_tokens': token_count(render_blocks(row['contexts'])),
                         'proposal_prompt_sha256': hashlib.sha256(render_prompt(row, root / 'proposal.txt').encode()).hexdigest()})
    write_new(root / 'input_audit.json', manifest)
    write_new(root / 'frozen_runner.py', Path(__file__).read_text(), raw=True)
    for name in ('private_labels.json', 'case_manifest.json', 'plan.md', 'source_audit.json',
                 'frozen_cases.py', 'frozen_protocol.py', 'frozen_reviewer.py', 'dependencies.json'):
        source = Path(cases).parent / name
        if source.is_file():
            write_new(root / name, source.read_text(), raw=True)
    freeze(root)
    return len(rows)


def run(root, jobs=4):
    root = Path(root)
    assert_frozen(root)
    # Exclusive marker prevents accidental reruns even after partial failures.
    write_new(root / 'started.json', {'model': 'gpt-5.6-sol', 'repeats': 3, 'jobs': jobs})
    rows = [json.loads(line) for line in (root / 'requests.jsonl').read_text().splitlines()]
    all_predictions = []

    def call(item):
        index, repeat, stage, request_path = item
        output = root / f'raw/{stage}_{index:03}_r{repeat}.jsonl'
        if output.exists():
            raise FileExistsError(output)
        schema_path = root / f'inputs/{index:03}_{stage}.schema.json'
        run_codex_requests(request_path, output, root / f'{stage}.txt', schema_path,
                           model='gpt-5.6-sol', timeout_seconds=300, jobs=1, repeat_id=repeat)
        records = [json.loads(line) for line in output.read_text().splitlines()]
        if len(records) != 1:
            raise RuntimeError('Expected one raw record: ' + str(output))
        pred = records[0]
        errors = []
        if (pred.get('task_id'), pred.get('condition'), pred.get('repeat')) != (rows[index]['task_id'], rows[index]['condition'], repeat):
            errors.append('identity mismatch')
        if pred.get('status') != 'ok':
            errors.append('generation error')
        else:
            errors.extend(e.message for e in jsonschema.Draft202012Validator(json.loads(schema_path.read_text())).iter_errors(pred.get('response')))
        if pred.get('tool_isolation', {}).get('tool_call_count', 0):
            errors.append('unexpected model tool use')
        write_new(root / f'validation/{stage}_{index:03}_r{repeat}.json', {'valid': not errors, 'errors': errors})
        if errors:
            raise RuntimeError('Invalid model output retained: ' + str(output))
        return index, pred | {'stage': stage}

    for repeat in (1, 2, 3):
        order = list(range(len(rows))); random.Random(20260903 + repeat).shuffle(order)
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            proposals = dict(pool.map(call, [(i, repeat, 'proposal', root / f'inputs/{i:03}.jsonl') for i in order]))
        all_predictions.extend(proposals.values())
        print(json.dumps({'stage': 'proposal', 'repeat': repeat, 'completed': len(proposals)}), flush=True)
        audit = []
        for index, row in enumerate(rows):
            payload, selection = hypothesis_payload(proposals[index]['response'])
            final = copy.deepcopy(row)
            final['contexts'].append({'id': 'HYPOTHESES', 'kind': 'untrusted_hypotheses', 'file': '',
                'start_line': None, 'end_line': None,
                'content': 'Candidate questions, not evidence:\n' + json.dumps(payload, ensure_ascii=False)})
            final['context_status']['context_tokens'] = token_count(render_blocks(final['contexts']))
            write_new(root / f'verify_inputs/{index:03}_r{repeat}.jsonl', [final], jsonl=True)
            audit.append({'task_id': row['task_id'], 'condition': row['condition'], 'repeat': repeat, **selection,
                          'prompt_sha256': hashlib.sha256(render_prompt(final, root / 'verify.txt').encode()).hexdigest()})
        write_new(root / f'hypothesis_audit_r{repeat}.json', audit)
        random.Random(20261903 + repeat).shuffle(order)
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            finals = dict(pool.map(call, [(i, repeat, 'verify', root / f'verify_inputs/{i:03}_r{repeat}.jsonl') for i in order]))
        all_predictions.extend(finals.values())
        print(json.dumps({'stage': 'verify', 'repeat': repeat, 'completed': len(finals)}), flush=True)
    assert_frozen(root)
    write_new(root / 'predictions.jsonl', all_predictions, jsonl=True)
    result = {'complete': len(all_predictions) == len(rows) * 6, 'calls': len(all_predictions),
              'final_reviews': len(rows) * 3, 'generation_errors': 0, 'schema_errors': 0,
              'tool_calls': sum(p.get('tool_isolation', {}).get('tool_call_count', 0) for p in all_predictions)}
    write_new(root / 'completion.json', result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['prepare', 'run']); p.add_argument('--root', required=True)
    p.add_argument('--cases'); p.add_argument('--jobs', type=int, default=4)
    a = p.parse_args()
    print(prepare(a.cases, a.root) if a.command == 'prepare' else run(a.root, a.jobs))
