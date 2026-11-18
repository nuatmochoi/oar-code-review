"""Frozen, explicitly post-hoc runtime disclosure; no target-defect hint."""
import argparse
import json
from pathlib import Path

from .counterfactual import sha, write_new
from .obligation import token_count
from .reviewer import render_prompt

RUNTIME = ('Deployment scope: CPython 3.14.6, using the standard-library json backend; '
           'simplejson is not installed. Python 2 is not a supported deployment. '
           'Review correctness only for behavior reachable in this runtime.')


def prepare_followup(source, output):
    source, output = Path(source), Path(output)
    execution = json.loads((source / 'execution.json').read_text())
    if not execution['gate']['eligible']:
        raise ValueError('Execution gate failed')
    for cell in execution['cells'].values():
        if cell['backend'] != 'json' or not cell['python'].startswith('3.14.6'):
            raise ValueError('Runtime facts do not match frozen follow-up')
    output.mkdir(parents=True, exist_ok=False)
    rows = [json.loads(line) for line in (source / 'requests.jsonl').read_text().splitlines()]
    for name in ('prompt.txt', 'schema.json', 'private_labels.json'):
        write_new(output / name, (source / name).read_text(), raw=True)
    audit = []
    for row in rows:
        row['pr'] = {'title': '', 'description': RUNTIME}
        prompt = render_prompt(row, output / 'prompt.txt')
        audit.append({'task_id': row['task_id'], 'condition': row['condition'],
                      'prompt_sha256': sha(prompt), 'prompt_reference_tokens': token_count(prompt)})
    write_new(output / 'requests.jsonl', rows, jsonl=True)
    write_new(output / 'input_audit.json', audit)
    write_new(output / 'provenance.json', {'source_run': str(source.resolve()),
              'post_hoc_exploratory': True, 'sole_input_change': 'common deployment facts',
              'runtime_text': RUNTIME, 'source_requests_sha256': sha((source / 'requests.jsonl').read_text())})
    return len(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(prepare_followup(args.source, args.output))
