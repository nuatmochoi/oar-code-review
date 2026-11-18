"""Staged development execution; frozen inputs and separate private labels."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
from pathlib import Path
import random
import sys

import jsonschema

from .auto_context import changed_query
from .context_adaptive import (ARMS, Corpus, EvidenceState, digest, retrieve_round,
    QUESTION_SCHEMA, FINAL_SCHEMA, PROPOSAL_PROMPT, CONTROLLER_PROMPT, FINAL_PROMPT,
    validate_controller)
from .counterfactual import write_new
from .isolated_review import call_packet
from .obligation import token_count
from .paired_benchmark import load_pair, validate_pair
from .python_ast_graph import build_python_ast_graph
from .delta_scip import changed_ranges
from .swing_pilot import load_graph
from .witness_runner import run_pair_witness


def scip_spec(graph):
    return {'nodes': {k: {'file': c.file, 'start': c.start, 'end': c.end} for k, c in graph.chunks.items()},
            'edges': [{'source': k, 'target': v, 'kind': 'scip_static_association'}
                      for k, neighbors in sorted(graph.edges.items()) for v in sorted(neighbors) if k < v],
            'audit': graph.audit}


def ast_spec(snapshot, version, diff):
    ranges = changed_ranges(diff)[version]
    graph = build_python_ast_graph(snapshot, revision=version, changed_files=ranges, changed_lines=ranges)
    return {'nodes': {n.id: {'file': n.file, 'start': n.start_line, 'end': n.end_line}
                      for n in graph.nodes if n.start_line is not None and n.end_line is not None},
            'edges': [{'source': e.source, 'target': e.target, 'kind': e.type}
                      for e in graph.edges if e.resolved and e.confidence > 0], 'audit': graph.metadata}


def portable_schema(schema):
    """Remove unsupported provider constraints; full validation stays local."""
    if isinstance(schema, list):
        return [portable_schema(item) for item in schema]
    if isinstance(schema, dict):
        return {key: portable_schema(value) for key, value in schema.items()
                if key not in {'allOf', 'uniqueItems', '$schema'}}
    return schema


def prepare(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    labels = json.loads((root / 'runs/auto_context2/private_labels.json').read_text())
    original = [json.loads(line) for line in (root / 'runs/auto_context2/requests.jsonl').read_text().splitlines()
                if json.loads(line)['condition'] == 'diff']
    grouped = {}
    private = {}
    for row in original:
        label = labels[row['task_id']]
        family, world = label['family'], label['variant']
        case_id = {'pager_stream': 'case01', 'backend_options': 'case02'}[family]
        project = {'pager_stream': 'click', 'backend_options': 'anyio'}[family]
        case = grouped.setdefault(case_id, {'case_id': case_id, 'repository': row['repository'],
            'scope': row['pr']['description'], 'diff': row['diff'], 'worlds': {}, 'origin': 'observed_real_repository_derived'})
        assert case['diff'] == row['diff'] and case['scope'] == row['pr']['description']
        versions, graphs = {}, {}
        for version in ('base', 'head'):
            graph, sources = load_graph(root / 'runs/auto_context_dependency_check1/indices' / project / (world + '_' + version))
            versions[version], graphs[version] = sources, scip_spec(graph)
        opaque_world = {'safe': 'a', 'unsafe': 'b'}[world]
        case['worlds'][opaque_world] = {'versions': versions, 'graphs': graphs}
        private.setdefault(case_id, {'family': family, 'origin': case['origin'], 'mechanism': label['target'], 'worlds': {}})['worlds'][opaque_world] = world
        private[case_id]['source_run'] = label['source_run']
    pair_names = ('smoke_config_provenance', 'smoke_budget_fanout')
    for index, name in enumerate(pair_names, 3):
        pair_root = root / 'runs/context_pairs_v1' / name
        pair = load_pair(pair_root / 'pair.json')
        gate = validate_pair(pair, pair_root)
        witness = run_pair_witness(pair, pair_root)
        if not gate['eligible'] or not witness['eligible']:
            raise ValueError('Invalid development pair: ' + name)
        write_new(output / 'private' / f'case{index:02}_witness.json', witness)
        case_id = f'case{index:02}'
        scope = ('Python 3.11. The deployed normalize entry point must preserve exact case of input keys. '
                 'Only supplied production functions and their ordinary calls are in scope.' if index == 3 else
                 'Python 3.11. The deployed entry point is routes.route. Its return value must preserve the '
                 'exact request-key value, including case. Direct calls to internal helpers are not deployed.')
        case = {'case_id': case_id, 'repository': 'local synthetic fixture', 'scope': scope,
                'diff': (pair_root / pair['worlds']['safe']['diff']).read_text(), 'worlds': {}, 'origin': 'observed_synthetic_control'}
        assert case['diff'] == (pair_root / pair['worlds']['unsafe']['diff']).read_text()
        for world, opaque in [('safe', 'a'), ('unsafe', 'b')]:
            versions, graphs = {}, {}
            for version in ('base', 'head'):
                snapshot = pair_root / pair['worlds'][world][version + '_snapshot']
                versions[version] = {p.relative_to(snapshot).as_posix(): p.read_text() for p in sorted(snapshot.rglob('*.py'))}
                graphs[version] = ast_spec(snapshot, version, case['diff'])
            case['worlds'][opaque] = {'versions': versions, 'graphs': graphs}
        grouped[case_id] = case
        private[case_id] = {'family': name, 'origin': case['origin'], 'mechanism': pair['gold']['mechanism'],
            'gold': pair['gold'], 'worlds': {'a': 'safe', 'b': 'unsafe'}, 'source_run': str(pair_root)}
    cases = list(grouped.values())
    write_new(output / 'cases.json', cases)
    write_new(output / 'private' / 'labels.json', private)
    schema = json.loads((root / 'paper/plans/context_adaptive_review_v1/controller.schema.json').read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    provider_schema = portable_schema(schema)
    # Structured-output backends need a portable schema; semantic constraints
    # still run locally against the full draft before accepting any controller.
    write_new(output / 'controller.schema.json', schema)
    write_new(output / 'controller.provider.schema.json', provider_schema)
    write_new(output / 'question.schema.json', QUESTION_SCHEMA)
    write_new(output / 'final.schema.json', FINAL_SCHEMA)
    amendment = '''# P0 development execution amendment — before model outputs

This batch implements and executes the seven-arm protocol on four observed cases:
two real-repository-derived Click/AnyIO families and two correlated synthetic
key-preservation controls. It is NOT the planned new 12-family / 3-repository
pilot, a fresh holdout, an official benchmark, or a paper-method reproduction.

Model gpt-5.6-sol / low; one draw; four concurrent transport calls maximum.
4 shared questions + 5 graph arms * 4 cases * 2 worlds * (2 controllers + final)
+ 8 lexical finals + 4 shared diff finals = 136 planned calls. A separate transport
smoke is excluded but charged in total accounting. No automatic retry or repair.

Both base/head source is available. Identical unchanged units are canonicalized
to head. Whole AST function/statement is L1. L0 consists of partial header/end
anchors, explicitly marked incomplete. L2 atomically requests the complete
one-hop static neighborhood; it is not a sound semantic closure. Unresolvable
dispatch remains unknown. SCIP associations (real cases) or conservative AST
relations (controls) are used as undirected retrieval links. Original edge
labels remain in audit, not interpreted as actual runtime paths.

Weighted PPR is the protocol's lexical relevance proxy, not official QAFD flow.
Graph selection interleaves three graph-ranked candidate slots and one lexical
fallback slot. Up to two novel paths per question per round, no oracle promotion.
This is a slot policy, not a guaranteed 75/25 token allocation.

Cumulative visible source, source headers and path metadata use budgets
800/1200/1600 reference tokens. L receives one pass at 1600. Sources are never
removed from the cumulative state. C conditions start at partial L0; R/S/Q use
whole L1. All original text is source-anchored; masked comments/docstrings retain
coordinates. Judge narratives are not passed to final reviewers.

Portable controller schema is used in transport; full semantic schema and
visible-ID checks gate local acceptance. A failed or invalid step is retained,
not repaired or silently dropped. No external tools are allowed in model calls.
No upper bound on output/reasoning tokens is claimed.

Initial questions and controller/final raw inputs, outputs, hashes, source/index
provenance and usage are retained. Final prompts are generated and hashed before
the final phase; arm order is randomized with fixed seed 20260929. D output is
shared between worlds. Final adjudication is author-based with explicit rules;
it cannot satisfy independent human gold or blinded independent evaluation.

Primary target-mechanism detection and safe-path alarm are reported per family.
Four cases, including correlated controls, cannot support significance tests.
New repository-disjoint 12-family P1 remains a separate data-collection gate;
this amendment does not relabel P0 as P1 or lower its requirements.
'''
    write_new(output / 'amendment.md', amendment, raw=True)
    for name in ('context_adaptive.py', 'context_adaptive_run.py', 'isolated_review.py'):
        write_new(output / 'implementation' / name, (root / 'ace_review' / name).read_text(), raw=True)
    freeze = {str(p.relative_to(root)): digest(p.read_text()) for p in (root / 'ace_review').glob('*.py')}
    freeze.update({str(p.relative_to(output)): digest(p.read_text()) for p in output.glob('*') if p.is_file()})
    write_new(output / 'freeze.json', {'source_and_input_sha256': freeze, 'planned_calls': 136,
        'model': 'gpt-5.6-sol', 'reasoning': 'low', 'private_labels_sha256': digest(private),
        'plan_scope': 'P0_observed_development_only'})
    print(json.dumps({'prepared_cases': len(cases), 'planned_calls': 136}))


def common(case):
    return '\nRepository: ' + case['repository'] + '\nDeployment scope: ' + case['scope'] + '\n[DIFF]\n' + case['diff']


def ask(output, run_id, prompt, schema, metadata):
    row = dict(metadata, run_id=run_id, prompt=prompt, prompt_sha256=digest(prompt), prompt_reference_tokens=token_count(prompt))
    write_new(output / 'inputs' / (run_id + '.json'), row)
    return call_packet(row, output / 'raw', 'gpt-5.6-sol', schema)


def batch(jobs, function, workers):
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(function, job) for job in jobs]
        for future in as_completed(futures):
            results.append(future.result())
    return results


def run(root, output, workers=4):
    root, output = Path(root).resolve(), Path(output).resolve()
    freeze = json.loads((output / 'freeze.json').read_text())
    for name, expected in freeze['source_and_input_sha256'].items():
        path = root / name if name.startswith('ace_review/') else output / name
        if digest(path.read_text()) != expected:
            raise ValueError('Frozen input/source changed: ' + name)
    (output / 'raw').mkdir(exist_ok=False)
    cases = json.loads((output / 'cases.json').read_text())
    controller_schema = json.loads((output / 'controller.provider.schema.json').read_text())
    full_controller_schema = json.loads((output / 'controller.schema.json').read_text())
    failures = []

    def propose(case):
        return ask(output, case['case_id'] + '_questions', PROPOSAL_PROMPT + common(case), QUESTION_SCHEMA,
                   {'phase': 'questions', 'case_id': case['case_id']})
    proposals = {r['case_id']: r for r in batch(cases, propose, workers)}
    write_new(output / 'proposals.json', proposals)
    corpora, states, questions, audits = {}, {}, {}, {}
    for case in cases:
        key = case['case_id']
        record = proposals[key]
        if record['status'] != 'ok':
            failures.append({'case_id': key, 'phase': 'questions', 'error': record.get('error')})
            continue
        qs = record['response']['questions']
        if [q['id'] for q in qs] != ['Q' + str(i + 1) for i in range(len(qs))]:
            failures.append({'case_id': key, 'phase': 'questions', 'error': 'invalid_question_ids'})
            continue
        questions[key] = qs
        for world, spec in case['worlds'].items():
            corpus = Corpus(spec['versions'], spec['graphs'], case['diff'])
            corpora[key, world] = corpus
            for arm in ARMS:
                if arm == 'D' and world == 'b':
                    continue
                state = EvidenceState(corpus)
                states[key, world, arm] = state
                audits[key, world, arm] = [retrieve_round(corpus, state, qs, changed_query(case['diff']), arm, 0)]
    dead = set()
    for round_index in (1, 2):
        jobs = [(case, world, arm) for case in cases for world in case['worlds'] for arm in ARMS
                if arm not in ('D', 'L') and (case['case_id'], world, arm) in states
                and (case['case_id'], world, arm) not in dead]

        def control(job):
            case, world, arm = job
            key = case['case_id']
            state = states[key, world, arm]
            prompt = CONTROLLER_PROMPT + common(case) + '\nQUESTIONS:\n' + json.dumps(questions[key])
            prompt += '\nVISIBLE EVIDENCE:\n' + (state.render() or '(none)')
            record = ask(output, f'{key}_{world}_{arm}_controller{round_index}', prompt, controller_schema,
                         {'phase': 'controller', 'round': round_index, 'case_id': key, 'world': world,
                          'arm': arm, 'evidence_tokens': state.tokens, 'evidence_sha256': digest(state.render())})
            semantic_errors = []
            if record['status'] == 'ok':
                semantic_errors = [str(e.message) for e in jsonschema.Draft202012Validator(full_controller_schema).iter_errors(record['response'])]
                semantic_errors.extend(validate_controller(record['response'], state, questions[key]))
            if record['status'] != 'ok' or semantic_errors:
                return (key, world, arm), None, {'run_id': record['run_id'], 'semantic_errors': semantic_errors,
                                              'transport_status': record['status']}
            audit = retrieve_round(corpora[key, world], state, questions[key], changed_query(case['diff']),
                                   arm, round_index, record['response'])
            return (key, world, arm), audit, None

        for key, audit, failure in batch(jobs, control, workers):
            if failure:
                dead.add(key); failures.append(failure)
            else:
                audits[key].append(audit)
        write_new(output / f'retrieval_round{round_index}.json', {'records': [
            {'case_id': k[0], 'world': k[1], 'arm': k[2], 'rounds': v} for k, v in audits.items()], 'failures': failures})
    finals = []
    for case in cases:
        cid = case['case_id']
        for world in case['worlds']:
            for arm in ARMS:
                key = cid, world, arm
                if key not in states or key in dead:
                    continue
                state = states[key]
                prompt = FINAL_PROMPT + common(case) + '\nQUESTIONS:\n' + json.dumps(questions[cid])
                prompt += '\nREPOSITORY EVIDENCE:\n' + (state.render() or '(none)')
                finals.append({'run_id': f'{cid}_{world}_{arm}_final', 'prompt': prompt,
                    'prompt_sha256': digest(prompt), 'phase': 'final', 'case_id': cid, 'world': world, 'arm': arm,
                    'evidence_tokens': state.tokens, 'evidence_sha256': digest(state.render()),
                    'evidence_ids': sorted(state.lines), 'evidence_lines': state.audit()['source_lines']})
    random.Random(20260929).shuffle(finals)
    write_new(output / 'final_packets.json', finals)
    write_new(output / 'final_freeze.json', {'final_packets_sha256': digest(finals), 'records': len(finals),
        'failures': failures, 'controller_conclusions_in_final': False})

    def review(row):
        return ask(output, row['run_id'], row['prompt'], FINAL_SCHEMA, {k: v for k, v in row.items() if k not in ('prompt', 'run_id')})
    predictions = batch(finals, review, workers)
    semantic_invalid = []
    for record in predictions:
        if record['status'] != 'ok':
            continue
        response = record['response']
        errors = []
        if (response['status'] == 'reported_issues') != bool(response['issues']):
            errors.append('status_issues_inconsistent')
        allowed = {'DIFF'} | set(record['evidence_ids'])
        for issue in response['issues']:
            if not set(issue['evidence_ids']) <= allowed:
                errors.append('unknown_final_citation')
        if errors:
            semantic_invalid.append({'run_id': record['run_id'], 'errors': errors})
    write_new(output / 'predictions.json', sorted(predictions, key=lambda x: x['run_id']))
    write_new(output / 'execution.json', {'planned_calls': 136, 'recorded_calls': len(list((output / 'raw').glob('*.json'))) // 2,
        'controller_failures': failures, 'final_semantic_invalid': semantic_invalid,
        'final_records': len(predictions), 'origin': 'observed_development_only'})
    print(json.dumps({'complete': True, 'final_records': len(predictions), 'controller_failures': len(failures),
                      'final_semantic_invalid': len(semantic_invalid)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'run'])
    parser.add_argument('--root', default='.')
    parser.add_argument('--output', required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.root, args.output)
    else:
        run(args.root, args.output, args.workers)
