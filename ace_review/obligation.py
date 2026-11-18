"""Gold-blind candidate-conditioned source selection and frozen experiment inputs.

The four slots are hypotheses, not extracted contracts or proven data flow.
The tokenizer is a reproducible reference encoding, not asserted model-native.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import random
import re
from functools import lru_cache
from pathlib import Path

import tiktoken

from .bm25 import BM25Index, Document
from .io import read_jsonl
from .reviewer import render_prompt

ARMS = ('verify_diff', 'verify_generic', 'verify_claim_bm25', 'verify_obligation',
        'verify_no_base', 'verify_no_consumer', 'verify_placebo')
SLOTS = ('before', 'after', 'consumer', 'trigger')
ENCODING = tiktoken.get_encoding('o200k_base')


@lru_cache(maxsize=50000)
def token_count(text):
    return len(ENCODING.encode(text, disallowed_special=()))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def clean_request(row):
    """Explicit allowlist: gold, oracle and task metadata never enter requests."""
    return {k: copy.deepcopy(row.get(k, '' if k == 'focus_diff' else {}))
            for k in ('task_id', 'repository', 'language', 'pr', 'diff', 'focus_diff')}


def source_roles(block):
    meta = block.get('metadata', {})
    versions = {s['version'] for s in meta.get('source_spans', [])}
    versions.add(meta.get('version', 'head'))
    roles = {'trigger'}
    if 'base' in versions:
        roles.add('before')
    if 'head' in versions:
        roles.add('after')
    owner = meta.get('owner_symbol')
    anchors = meta.get('anchors', [])
    if (meta.get('identical_revisions_collapsed') and {'base', 'head'} <= versions
            and owner and anchors and owner not in anchors):
        roles.add('consumer')
    return roles


def neutral_source(block, head_only=False):
    meta = block.get('metadata', {})
    # Keep actual line-numbered source, never inferred owner/rank/edge narratives.
    lines = [line for line in block['content'].splitlines() if re.match(r'^\d+:', line)]
    if not lines:
        raise ValueError(f"No verified numbered source in {block['id']}")
    spans = meta.get('source_spans') or [dict(version=meta.get('version', 'head'),
        file=block['file'], start_line=block.get('start_line'), end_line=block.get('end_line'))]
    if head_only:
        spans = [s for s in spans if s['version'] == 'head']
    if not spans:
        raise ValueError('No eligible source revision')
    revisions = '; '.join(f"{s['version']} {s['file']}:{s['start_line']}-{s['end_line']}" for s in spans)
    return dict(id='CTX-' + hashlib.sha256(block['id'].encode()).hexdigest()[:12],
                kind='repository_source', file=block['file'],
                start_line=block.get('start_line'), end_line=block.get('end_line'),
                content='Verified source span(s): ' + revisions + '\n' + '\n'.join(lines))


def render_blocks(blocks):
    return '\n\n'.join(f"[{b['id']}] kind={b['kind']} file={b.get('file', '')} "
        f"lines={b.get('start_line')}-{b.get('end_line')}\n{b['content']}" for b in blocks)


def query_text(text):
    # Keep original compound identifiers and add camel-case components.
    expanded = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    terms = re.findall(r'[A-Za-z_][A-Za-z0-9_]+', text + ' ' + expanded)
    return ' '.join(dict.fromkeys(t.lower() for t in terms))[:8000]


def select_sources(pool, proposal, arm, budget=4096):
    if arm not in ARMS:
        raise ValueError('Unknown arm')
    if arm == 'verify_diff':
        return [], dict(rendered_tokens=0, selected_source_ids=[], slots=[], eligible=0)
    by_id = {b['id']: b for b in pool}
    if len(by_id) != len(pool):
        raise ValueError('Duplicate source ID')
    roles = {key: source_roles(b) for key, b in by_id.items()}
    eligible = [key for key in by_id
                if (arm != 'verify_no_base' or 'after' in roles[key])
                and (arm != 'verify_no_consumer' or 'consumer' not in roles[key])]
    generic = sorted(eligible, key=lambda key: (-float(by_id[key].get('metadata', {}).get('score', 0)), key))
    sources = {key: neutral_source(by_id[key], head_only=arm == 'verify_no_base') for key in eligible}
    hypotheses = proposal.get('issues', [])
    schedule = []
    if arm == 'verify_generic':
        schedule = [(key, 'structural') for key in generic]
    elif arm == 'verify_placebo':
        shuffled = sorted(eligible)
        random.Random(20260903).shuffle(shuffled)
        schedule = [(key, 'random_same_pool') for key in shuffled]
    else:
        index = BM25Index(Document(key, 0, 0, query_text(by_id[key]['file'] + ' ' +
                          sources[key]['content'])) for key in eligible)
        def ranked(query, role=None):
            scored = [doc.file for _, doc in index.search(query_text(query), top_k=len(eligible))
                      if role is None or role in roles[doc.file]]
            fallback = [key for key in generic if key not in set(scored)
                        and (role is None or role in roles[key])]
            return scored + fallback
        joint = ' '.join(str(h.get('claim', '')) + ' ' + str(h.get('failure_scenario', '')) +
                         ' ' + ' '.join(h.get('evidence_request', {}).values()) for h in hypotheses)
        if arm == 'verify_claim_bm25':
            schedule = [(key, 'joint_claim') for key in ranked(joint)]
        else:
            queues = []
            for number, h in enumerate(hypotheses, 1):
                for slot in SLOTS:
                    if (arm == 'verify_no_base' and slot == 'before') or (
                            arm == 'verify_no_consumer' and slot == 'consumer'):
                        continue
                    query = h.get('evidence_request', {}).get(slot, '') or h.get('claim', '')
                    queues.append((f'C{number}:{slot}', ranked(query, slot)))
            # Round-robin over candidate-slot queues; candidate count is shared.
            seen = set()
            for pos in range(max((len(q) for _, q in queues), default=0)):
                for label, queue in queues:
                    if pos < len(queue) and queue[pos] not in seen:
                        schedule.append((queue[pos], label))
                        seen.add(queue[pos])
            schedule += [(key, 'fallback') for key in ranked(joint) if key not in seen]
    selected, selected_ids, selected_slots = [], [], []
    current_tokens = 0
    for key, slot in schedule:
        candidate = sources[key]
        # Conservative cheap rejection; exact rendered check decides acceptance.
        if token_count(render_blocks([candidate])) > budget - current_tokens + 4:
            continue
        total = token_count(render_blocks(selected + [candidate]))
        if total <= budget:
            selected.append(candidate)
            selected_ids.append(key)
            selected_slots.append(slot)
            current_tokens = total
    return selected, dict(rendered_tokens=current_tokens, selected_source_ids=selected_ids,
                          slots=selected_slots, eligible=len(eligible),
                          role_counts={s: sum(s in roles[k] for k in selected_ids) for s in SLOTS},
                          pool_sha256=digest(pool))


def validate_proposals(requests, predictions):
    expected = {r['task_id'] for r in requests}
    if len(predictions) != len(expected) or {p['task_id'] for p in predictions} != expected:
        raise ValueError('Missing or duplicate proposals')
    result = {}
    for p in predictions:
        if p.get('status') != 'ok' or p.get('condition') != 'proposal':
            raise ValueError('Unsuccessful proposal')
        response = p.get('response')
        if not isinstance(response, dict) or not isinstance(response.get('issues'), list):
            raise ValueError('Malformed proposal response')
        if len(response['issues']) > 5:
            raise ValueError('Too many proposals')
        for h in response['issues']:
            queries = h.get('evidence_request', {})
            if set(queries) != set(SLOTS) or not all(isinstance(v, str) for v in queries.values()):
                raise ValueError('Malformed candidate slots')
        result[p['task_id']] = response
    return result


def make_requests(requests, predictions, pools, budget=4096):
    proposals = validate_proposals(requests, predictions)
    rows, audits = [], []
    for request in sorted(requests, key=lambda r: r['task_id']):
        task_id = request['task_id']
        plan = dict(id='PLAN', kind='unverified_hypotheses', file='', start_line=None,
                    end_line=None, content=json.dumps(proposals[task_id], ensure_ascii=False, sort_keys=True))
        for arm in ARMS:
            blocks, audit = select_sources(pools[task_id], proposals[task_id], arm, budget)
            row = clean_request(request) | dict(condition=arm, contexts=[plan] + blocks,
                context_status=dict(context_tokens=audit['rendered_tokens'], unavailable=[],
                                    token_budget=budget, encoding='o200k_base'))
            rows.append(row)
            audits.append(dict(task_id=task_id, condition=arm, plan_sha256=digest(plan), **audit))
    random.Random(20260903).shuffle(rows)
    return rows, audits


def write_exclusive(path, obj, jsonl=False):
    with Path(path).open('x') as handle:
        if jsonl:
            for row in obj:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        else:
            json.dump(obj, handle, ensure_ascii=False, indent=2)
            handle.write('\n')


def schemas():
    base = json.loads((Path(__file__).parent / 'schemas/review_output.schema.json').read_text())
    planner = copy.deepcopy(base)
    item = planner['properties']['issues']['items']
    item['required'].append('evidence_request')
    item['properties']['evidence_request'] = dict(type='object', additionalProperties=False,
        required=list(SLOTS), properties={s: {'type': 'string'} for s in SLOTS})
    verifier = copy.deepcopy(base)
    item = verifier['properties']['issues']['items']
    item['required'].append('origin_id')
    item['properties']['origin_id'] = dict(type='string', enum=['C1', 'C2', 'C3', 'C4', 'C5', 'NEW'])
    verifier['required'].append('assessments')
    properties = dict(candidate_id={'type': 'string'}, verdict=dict(type='string',
        enum=['confirmed', 'refuted', 'unresolved']), explanation={'type': 'string'},
        support_evidence_ids=dict(type='array', items={'type': 'string'}),
        counterevidence_ids=dict(type='array', items={'type': 'string'}), missing_facts={'type': 'string'})
    verifier['properties']['assessments'] = dict(type='array', maxItems=5,
        items=dict(type='object', additionalProperties=False, required=list(properties), properties=properties))
    return planner, verifier


def prepare(source, output):
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError(f'Preserve existing run: {output}')
    rows = [clean_request(r) | dict(condition='proposal', contexts=[])
            for r in read_jsonl(source / 'requests.jsonl') if r['condition'] == 'diff']
    tasks = list(read_jsonl(source / 'manifest.jsonl'))
    if len(rows) != 15 or {r['task_id'] for r in rows} != {t['id'] for t in tasks}:
        raise ValueError('Expected frozen 15-task cohort')
    output.mkdir(parents=True)
    write_exclusive(output / 'manifest.jsonl', tasks, True)
    write_exclusive(output / 'proposal_requests.jsonl', rows, True)
    planner, verifier = schemas()
    write_exclusive(output / 'proposal.schema.json', planner)
    write_exclusive(output / 'verification.schema.json', verifier)
    config = dict(stage='exploratory_obligation_verification', source=str(source), tasks=15,
        conditions=list(ARMS), budget=4096, reference_encoding='o200k_base', repeats=1,
        reviewer='gpt-5.6-sol', judge='gpt-5.4', seed=20260903,
        corpus='runs/delta_scip_v2_final17/candidates',
        source_requests_sha256=hashlib.sha256((source / 'requests.jsonl').read_bytes()).hexdigest())
    write_exclusive(output / 'config.json', config)
    names = ['obligation.py', 'reviewer.py', 'bm25.py', 'io.py', 'schema.py', 'batch_judge.py',
             'judge.py', 'statistics.py', 'evaluation.py', 'scip_screening.py',
             'prompts/obligation_proposal.txt', 'prompts/obligation_verify.txt',
             'schemas/batch_semantic_match.schema.json', 'schemas/review_output.schema.json']
    hashes = {}
    for name in names:
        payload = (Path(__file__).parent / name).read_bytes()
        target = output / 'source/ace_review' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as handle:
            handle.write(payload)
        hashes[name] = hashlib.sha256(payload).hexdigest()
    plan = Path('runs/OBLIGATION_EXPERIMENT_PLAN_KO.md').read_bytes()
    with (output / 'PRE_RUN_PLAN_KO.md').open('xb') as handle:
        handle.write(plan)
    write_exclusive(output / 'source_hashes.json', hashes)
    print(json.dumps(dict(prepared=str(output), proposal_requests=len(rows))))


def build(output):
    output = Path(output)
    if (output / 'requests.jsonl').exists():
        raise FileExistsError('Preserve existing verification inputs')
    config = json.loads((output / 'config.json').read_text())
    requests = list(read_jsonl(output / 'proposal_requests.jsonl'))
    predictions = list(read_jsonl(output / 'proposal_predictions.jsonl'))
    pools = {}
    for r in requests:
        with gzip.open(Path(config['corpus']) / (r['task_id'] + '.json.gz'), 'rt') as handle:
            pools[r['task_id']] = json.load(handle)['bundle_sources']
    rows, audits = make_requests(requests, predictions, pools, config['budget'])
    prompt = output / 'source/ace_review/prompts/obligation_verify.txt'
    for r in rows:
        r['context_status']['rendered_prompt_reference_tokens'] = token_count(render_prompt(r, prompt))
    for a in audits:
        r = next(r for r in rows if r['task_id'] == a['task_id'] and r['condition'] == a['condition'])
        a['prompt_sha256'] = hashlib.sha256(render_prompt(r, prompt).encode()).hexdigest()
        a['rendered_prompt_reference_tokens'] = r['context_status']['rendered_prompt_reference_tokens']
    write_exclusive(output / 'requests.jsonl', rows, True)
    write_exclusive(output / 'selection_audit.json', audits)
    print(json.dumps(dict(requests=len(rows), output=str(output))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'build'])
    parser.add_argument('--source', default='runs/scip_review_screening15')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.source, args.output)
    else:
        build(args.output)


if __name__ == '__main__':
    main()
