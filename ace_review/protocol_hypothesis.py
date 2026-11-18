"""Protocol hypothesis ablation with frozen, gold-blind evidence and stage inputs."""
from __future__ import annotations

import argparse
import ast
import copy
import io
import json
import random
import tokenize
from pathlib import Path

from .counterfactual import sha, write_new
from .obligation import render_blocks, token_count
from .reviewer import render_prompt, run_codex_requests

FIELDS = ('id', 'claim', 'protocol', 'companion', 'failure_scenario', 'refutation', 'evidence_ids')
INITIAL = ('general', 'checklist', 'generic_proposal', 'protocol_proposal')
FINAL = ('general', 'checklist', 'generic_two_stage', 'protocol_two_stage', 'oracle_hypothesis')
CHECKLIST = '''Consider these protocol families when supported by the patch and runtime:
1. Representation conversion and reconstruction: encoding/decoding, copying,
   serialization, process transfer, constructor/state preservation.
2. Identity-based storage: write/read/delete/invalidate must agree on keys,
   namespaces, normalization, versions, and freshness.
3. Callback invocation: parameter names/positions, return and exception contracts.
4. Resource lifecycle: acquire/use/release, iteration termination and cancellation.
5. Configuration consumers: parsing, validation, defaults and deployment use.
6. Concurrent state: ownership, publication, ordering and failure cleanup.
These are questions, not findings. Discard irrelevant families; do not invent a
consumer or assert that all possible protocols must be supported.'''
COMMON = '''Review this patch for concrete correctness regressions in the stated runtime.
Supplied code is the current repository evidence, not a remembered upstream version.
Comments and documentation strings may be masked; executable code is preserved
except documentation string values. Report changed or unchanged companion locations.
Do not report style issues, unsupported possibilities or pre-existing failures.
No tools or external information are available. Cite DIFF and exact CTX identifiers.
Return the required JSON. If no concrete regression is established, return issues=[].'''
VERIFY = COMMON + '''
Evaluate the supplied hypotheses against the source. Hypotheses are NOT evidence,
and may be wrong or incomplete. Explicitly consider refutations and safe behavior.
Return only supported final issues; an empty hypothesis list does not establish
safety. You may report a new concrete issue found in the same supplied source.'''
GENERIC = '''Propose at most five distinct correctness regressions introduced by this
patch, using the current source and runtime. These are hypotheses, not final findings.
For each state claim, failure scenario, implicated companion location, evidence IDs,
and an observation that would refute it. Set protocol to a short descriptive label.
Do not use external tools or assume a remembered repository version. Return the
required hypotheses JSON; do not include unsupported style concerns.'''
PROTOCOL = GENERIC + '\n' + CHECKLIST + '''
For each candidate connect: changed value/type/configuration -> downstream protocol
operation -> behavior that must be preserved -> companion implementation -> falsifier.
Prioritize concrete reachable chains over a generic list. If a boundary's existence
is unknown, make that uncertainty explicit instead of treating it as established.'''


def mask_source(source):
    """Keep original line coordinates; preserve non-documentation string literals."""
    lines = source.splitlines(keepends=True)
    chars = [list(line) for line in lines]

    def char_col(lineno, byte_offset):
        return len(lines[lineno - 1].encode()[:byte_offset].decode())

    def blank(start, end, replacement=''):
        for lineno in range(start[0], end[0] + 1):
            left = start[1] if lineno == start[0] else 0
            right = end[1] if lineno == end[0] else len(chars[lineno - 1])
            for col in range(left, right):
                if chars[lineno - 1][col] not in '\r\n':
                    chars[lineno - 1][col] = ' '
        for offset, char in enumerate(replacement):
            chars[start[0] - 1][start[1] + offset] = char

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                value = first.value
                blank((value.lineno, char_col(value.lineno, value.col_offset)),
                      (value.end_lineno, char_col(value.end_lineno, value.end_col_offset)), '""')
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            blank(tok.start, tok.end)
    result = ''.join(''.join(line).rstrip(' \t\r\n') + ('\n' if lines[i].endswith('\n') else '')
                     for i, line in enumerate(chars))
    ast.parse(result)
    return result


def clean_row(row):
    return {k: copy.deepcopy(row[k]) for k in
            ('task_id', 'repository', 'language', 'pr', 'diff', 'contexts', 'context_status')}


def build_initial(rows):
    return [clean_row(row) | {'condition': arm} for arm in INITIAL for row in rows]


def hypothesis_payload(response, budget=1200):
    if not isinstance(response, dict) or not isinstance(response.get('hypotheses'), list):
        raise ValueError('Malformed hypothesis response')
    kept, dropped = [], []
    for index, h in enumerate(response['hypotheses']):
        if not isinstance(h, dict) or any(field not in h for field in FIELDS):
            raise ValueError('Malformed hypothesis item')
        item = {field: copy.deepcopy(h[field]) for field in FIELDS}
        if len(kept) < 5 and token_count(json.dumps({'hypotheses': kept + [item]}, ensure_ascii=False)) <= budget:
            kept.append(item)
        else:
            dropped.append(index)
    return {'hypotheses': kept}, {'kept': len(kept), 'dropped_indices': dropped,
                                 'payload_reference_tokens': token_count(json.dumps({'hypotheses': kept}, ensure_ascii=False))}


def prepare_final(row, prediction, arm):
    expected = {'generic_two_stage': 'generic_proposal', 'protocol_two_stage': 'protocol_proposal',
                'oracle_hypothesis': 'oracle_proposal'}[arm]
    if prediction.get('task_id') != row['task_id'] or prediction.get('status') != 'ok' or prediction.get('condition') != expected:
        raise ValueError('Failed or mismatched proposal')
    payload, audit = hypothesis_payload(prediction['response'])
    result = clean_row(row) | {'condition': arm}
    result['contexts'].append({'id': 'HYPOTHESES', 'kind': 'untrusted_hypotheses', 'file': '',
                               'start_line': None, 'end_line': None,
                               'content': 'Candidate questions, not evidence:\n' + json.dumps(payload, ensure_ascii=False)})
    result['context_status']['context_tokens'] = token_count(render_blocks(result['contexts']))
    return result, audit


def proposal_schema():
    properties = {field: {'type': 'string'} for field in FIELDS if field != 'evidence_ids'}
    properties['evidence_ids'] = {'type': 'array', 'items': {'type': 'string'}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['hypotheses'],
            'properties': {'hypotheses': {'type': 'array', 'maxItems': 5, 'items': {
                'type': 'object', 'additionalProperties': False, 'required': list(FIELDS), 'properties': properties}}}}


def prepare(cases, output):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    rows = [json.loads(line) for line in Path(cases).read_text().splitlines()]
    initial = build_initial(rows)
    write_new(root / 'base_requests.jsonl', rows, jsonl=True)
    write_new(root / 'initial_requests.jsonl', initial, jsonl=True)
    prompts = {'general': COMMON, 'checklist': COMMON + '\n' + CHECKLIST,
               'generic_proposal': GENERIC, 'protocol_proposal': PROTOCOL, 'verify': VERIFY}
    for name, prompt in prompts.items():
        write_new(root / (name + '.txt'), prompt, raw=True)
    write_new(root / 'proposal_schema.json', proposal_schema())
    write_new(root / 'review_schema.json', (Path(__file__).parent / 'schemas/review_output.schema.json').read_text(), raw=True)
    write_new(root / 'input_audit.json', [{'task_id': row['task_id'], 'condition': row['condition'],
              'prompt_sha256': sha(render_prompt(row, root / (row['condition'] + '.txt'))),
              'prompt_reference_tokens': token_count(render_prompt(row, root / (row['condition'] + '.txt')))} for row in initial])
    write_new(root / 'frozen_method.py', Path(__file__).read_text(), raw=True)
    return len(initial)


def run(root, oracle_path, jobs=4):
    root = Path(root)
    base = [json.loads(line) for line in (root / 'base_requests.jsonl').read_text().splitlines()]
    oracles = json.loads(Path(oracle_path).read_text())
    for repeat in (1, 2, 3):
        rng = random.Random(20260903 + repeat)
        order = list(INITIAL); rng.shuffle(order)
        for arm in order:
            output = root / f'{arm}_r{repeat}.jsonl'
            if output.exists():
                raise FileExistsError(output)
            summary = run_codex_requests(root / 'initial_requests.jsonl', output, root / (arm + '.txt'),
                root / ('proposal_schema.json' if arm.endswith('_proposal') else 'review_schema.json'),
                condition=arm, model='gpt-5.6-sol', timeout_seconds=300, jobs=jobs, repeat_id=repeat)
            print(json.dumps(summary), flush=True)
            if summary['failed']:
                raise RuntimeError('Model call failed; outputs retained')
        finals, audit = [], []
        for arm in ('generic_two_stage', 'protocol_two_stage', 'oracle_hypothesis'):
            proposals = ({r['task_id']: {'task_id': r['task_id'], 'condition': 'oracle_proposal',
                          'status': 'ok', 'response': oracles[r['task_id']]} for r in base} if arm == 'oracle_hypothesis' else
                         {p['task_id']: p for p in [json.loads(line) for line in
                          (root / f'{arm.replace("_two_stage", "_proposal")}_r{repeat}.jsonl').read_text().splitlines()]})
            for row in base:
                final, selection = prepare_final(row, proposals[row['task_id']], arm)
                finals.append(final)
                audit.append({'task_id': row['task_id'], 'condition': arm, **selection,
                              'prompt_sha256': sha(render_prompt(final, root / 'verify.txt'))})
        rng.shuffle(finals)
        write_new(root / f'verify_requests_r{repeat}.jsonl', finals, jsonl=True)
        write_new(root / f'hypothesis_audit_r{repeat}.json', audit)
        output = root / f'verify_r{repeat}.jsonl'
        if output.exists():
            raise FileExistsError(output)
        summary = run_codex_requests(root / f'verify_requests_r{repeat}.jsonl', output, root / 'verify.txt',
                    root / 'review_schema.json', model='gpt-5.6-sol', timeout_seconds=300, jobs=jobs, repeat_id=repeat)
        print(json.dumps(summary), flush=True)
        if summary['failed']:
            raise RuntimeError('Verifier failed; outputs retained')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--cases', required=True); p.add_argument('--output', required=True)
    p = sub.add_parser('run'); p.add_argument('--root', required=True); p.add_argument('--oracles', required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        print(prepare(args.cases, args.output))
    else:
        run(args.root, args.oracles)


if __name__ == '__main__':
    main()
