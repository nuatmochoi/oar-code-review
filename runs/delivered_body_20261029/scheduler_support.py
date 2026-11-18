#!/usr/bin/env python3
"""OAR product pilot executor: preflight / prepare / test / smoke / freeze / run / status / inspect (+ internal worker).

Order of use (see RUN_GUIDE_KO.md):  preflight -> prepare -> test -> smoke -> (parent code review) -> freeze ->
run --launch-model -> status / inspect.  ``run`` refuses to start without ``--launch-model``, without prepared
inputs, without passing tests+smoke for the CURRENT code hash, or without a freeze that still matches every
hash. Model calls only ever happen in ``run --launch-model``; every other command is model-free.
"""
from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if sys.pycache_prefix is None:
    sys.pycache_prefix = str(HERE / '.pycache')
sys.path.insert(0, str(HERE))

import cli_transport as ct  # noqa: E402
import oar_controller as cc  # noqa: E402
import oar_core as oc  # noqa: E402
import oar_fixture as fx  # noqa: E402
import oar_retrieval as rt  # noqa: E402
import oar_product_adapter as ad  # noqa: E402

CASES = [f'N{i:02d}' for i in range(1, 13)]
WORLDS = ('a', 'b')
ARM_ORDER = tuple(ad.V3_ARMS)          # B0 diff-only + L (= V2 AR), C, B, CB; historical arms remain fixtures only.
SHUFFLE_SEED = 2026102630              # fresh V3 job order (V2 used 20261026)
CODE_FILES = ('oar_core.py', 'cli_transport.py', 'oar_product_adapter.py', 'oar_controller.py', 'oar_retrieval.py',
              'oar_fixture.py', 'oar_experiment.py', 'fake_cli_child.py', 'v3_checks/run_scripted.py', 'v3_checks/compare.py')
TEST_FILES = ('test_oar_core.py', 'test_cli_transport.py', 'test_oar_product_adapter.py', 'test_oar_experiment.py',
              'test_oar_controller.py', 'test_oar_retrieval.py', 'test_oar_v3_arms.py', 'test_oar_b0.py')
DOC_FILES = ('PROTOCOL_KO.md', 'INTEGRATION.md', 'V3_IMPLEMENTATION_TASK.md', 'V3_IMPLEMENTATION_REPORT.md', 'RUN_GUIDE_KO.md',
             'parent_audit/INTEGRATION_INPUT_NOTES.md', 'parent_audit/product_snapshot_20261026.json',
             'parent_audit/run_lineage.json', 'RESUME_B0_IMPLEMENTATION_TASK_20261029.md',
             'PARENT_REVIEW_REQUIRED.md', 'V3_REVIEW_REPAIR_TASK.md',
             'parent_audit/GRADING_BOUNDARIES.md', 'parent_audit/GRADING_EXECUTION_KO.md')
SMOKE_KEYS = ('N01_a', 'N08_a')
WORKERS = 2
MAX_WORKERS = 2           # hard cap for any run; also enforced by worker slot locks, whoever starts the workers
CLI_TIMEOUT = 300


class ExperimentError(RuntimeError):
    pass


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json_new(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ct.write_new(path, value)


def write_or_verify(path: Path, value) -> str:
    """Exclusive create, or identical content already there. Different content never overwrites."""
    text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    if path.exists():
        if path.read_text(encoding='utf-8') != text:
            raise ExperimentError(f'{path} exists with different content; refusing to overwrite')
        return 'identical'
    path.parent.mkdir(parents=True, exist_ok=True)
    ct.write_new(path, text)
    return 'created'


def code_hashes() -> dict:
    return {name: sha(HERE / name) for name in (*CODE_FILES, *TEST_FILES) if (HERE / name).exists()}


def ace_dependency_files() -> dict:
    """The reused ace_review modules and every ace_review module they import (transitively): relative path -> sha256."""
    package = Path(ad.ACE_PARENT) / 'ace_review'
    todo, seen = ['isolated_review', 'reviewer', 'protocol_hypothesis'], set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        path = package / f'{name}.py'
        if not path.is_file():
            raise ExperimentError(f'ace_review dependency {name} not found at {path}')
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                names = [node.module.split('.')[0]] if node.module else [alias.name for alias in node.names]
                todo.extend(n for n in names if (package / f'{n}.py').is_file())
    if (package / '__init__.py').is_file():
        seen.add('__init__')
    return {f'ace_review/{name}.py': sha(package / f'{name}.py') for name in sorted(seen)}


def code_sha() -> str:
    return oc._sha(sorted((n, h) for n, h in code_hashes().items() if n in CODE_FILES or n in TEST_FILES))


def directions() -> list:
    return [f'{c}_{w}' for c in CASES for w in WORLDS]


class Experiment:
    """All locations of one experiment root. ``official`` is the real pilot (the folder this file lives in)."""

    def __init__(self, root=None):
        self.root = Path(root or HERE).resolve()
        self.official = self.root == HERE
        self.prepared = self.root / 'prepared'
        self.verification = self.root / 'verification'
        self.freeze_path = self.root / 'freeze.json'
        self.results = self.root / 'results'
        self.reviews = self.results / 'reviews'
        self.gate_dir = self.results / 'gate'

    def jobs(self) -> list:
        path = self.prepared / 'jobs.json'
        if not path.exists():
            raise ExperimentError('prepared/jobs.json is missing: run `prepare` first')
        return json.loads(path.read_text())['jobs']


# ============================================================ preflight
def preflight() -> dict:
    report = {'python': sys.version.split()[0], 'checks': {}}
    checks = report['checks']

    def check(name, fn):
        try:
            checks[name] = {'ok': True, **(fn() or {})}
        except Exception as exc:
            checks[name] = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
    check('product_snapshot', lambda: {k: v for k, v in ad.verify_snapshot().items() if k != 'modules'})
    check('product_import', lambda: {'orchestrator': ad.load_product().orchestrator.__file__,
                                     'registry': ad.load_product().registry.__file__})
    check('pyright_langserver', lambda: {'path': str(ad.PYRIGHT_BIN / 'pyright-langserver'),
                                         'exists': (ad.PYRIGHT_BIN / 'pyright-langserver').exists(),
                                         'node': shutil.which('node')})
    check('codex_binary_present', lambda: {'path': shutil.which('codex'), 'note': 'only looked up, never executed'})
    check('transport_command_pins_model', lambda: {'ok': 'model_reasoning_effort="low"' in ct.command_for('/x', ct.MODEL)})
    check('tokenizer', lambda: {'o200k_base_tokens_of_hello': oc.count_reference_tokens('hello world')})
    check('mask_source', lambda: {'ok': ad.MaskedSources(HERE)._mask('x = 1  # c\n') == 'x = 1\n'})
    report['config_delta'] = ad.config_delta()
    report['ok'] = all(c['ok'] for c in checks.values()) and all(
        c.get('exists', True) for c in checks.values())
    return report


# ============================================================ prepare
def make_jobs() -> list:
    rng = random.Random(SHUFFLE_SEED)
    blocks = [(c, w) for c in CASES for w in WORLDS]
    rng.shuffle(blocks)
    jobs = []
    for case, world in blocks:
        arms = list(ARM_ORDER)
        rng.shuffle(arms)
        for arm in arms:
            jobs.append({'review_id': f'{case}_{world}_{arm}', 'key': f'{case}_{world}', 'case': case, 'world': world,
                         'arm': arm, 'order': len(jobs)})
    return jobs


def prepare_direction(key: str) -> dict:
    product = ad.load_product()
    holder = {}

    def factory(root):
        masked = ad.MaskedSources(root)
        holder['masked'] = masked
        state = types_ns()
        cls = ad.make_registry_class(product)
        reg = cls(root, packet_id=key, account=oc.EvidenceAccount(oc.Budgets()), masked=masked,
                  accounting=ad.QueryAccounting(), state=state, config=ad.pilot_config(product))
        holder['registry'] = reg
        return reg

    inputs = ad.build_inputs(key, factory, product)
    data, masked, registry = inputs['data'], holder['masked'], holder['registry']
    workspace = ad.verify_workspace(data['workspace_root'], data['workspace_sources'])
    agreement = ad.seed_agreement(inputs, masked)
    account = oc.EvidenceAccount(oc.Budgets())
    account.add_seed(inputs['seed_evidence'])
    seed_tokens = account.exposed_tokens()
    packet = inputs['packet']
    head_packet = ad.head_only_packet(packet, inputs)
    gd = product.gap_detector

    def pre_gap(pkt):
        return [*gd.detect_public_reference_gaps(pkt, registry.code_index),
                *gd.detect_related_test_gaps(inputs['diff'], pkt, registry.test_index),
                *gd.detect_terraform_graph_gaps(inputs['diff'], pkt, enabled=False)]
    with ad.seed_rendering(inputs, product):
        prompt_ctx = product.prompt_context.select_prompt_context(packet)
        complete = (len(prompt_ctx) == len(packet.snippets) and not any(p.stub for p in prompt_ctx)
                    and not any('<truncated>' in p.content for p in prompt_ctx))
        system, shared, prompt, tail = product.llm_reviewer._assemble_prompt_prefix(
            inputs['diff'], packet, additional_evidence=None, review_context=inputs['review_context'])
    initial_text = '\n'.join([system, shared, prompt, tail])
    core_ids = [e.id for e in inputs['seed_evidence']]
    rendered_ids = ad.rendered_seed_ids(initial_text)
    id_invariant = (len(rendered_ids) == len(set(rendered_ids)) and sorted(rendered_ids) == sorted(core_ids)
                    and [p.evidence_id for p in prompt_ctx] == [i for i in rendered_ids])
    lines_supplied = sorted((e.snapshot, e.file, n, t) for e in inputs['seed_evidence'] for n, t in e.lines)
    lines_original = sorted((b['evidence'].snapshot, b['evidence'].file, n, t)
                            for b in ad.seed_blocks(data['seed_source']) for n, t in b['evidence'].lines)
    # what the real detectors ask for before the first model call (no tool is executed here), with head-only coverage ...
    gaps = pre_gap(head_packet)
    # ... and, for audit only, what they would ask if base snippets also counted as head coverage (the rejected behaviour)
    gaps_base_counted = pre_gap(packet)
    cfg = ad.pilot_config(product)
    sliced = gaps[:min(cfg.agentic_requests_per_iteration, cfg.agentic_max_requests)]
    problems = []
    if agreement['mismatched']:
        problems.append('seed_masked_workspace_mismatch')
    if not complete:
        problems.append('seed_not_fully_rendered_by_product_prompt_context')
    if not id_invariant:
        problems.append('seed_core_ids_differ_from_rendered_ids')
    if lines_supplied != lines_original:
        problems.append('seed_lines_not_preserved')
    if seed_tokens > ad.LIMITS['source_union_reference_tokens']:
        problems.append('seed_over_source_cap')
    return {
        'key': key, 'ok': not problems, 'problems': problems,
        'case': {k: data['case'][k] for k in ('case_id', 'world', 'repository', 'changed_files')},
        'workspace': workspace,
        'authoritative': {**data['authoritative'], 'workspace_tree_sha256': data['workspace_tree_sha256']},
        'seed': {
            'blocks': inputs['seed_blocks'], 'historical_to_product_ids': inputs['seed_id_map'],
            'source_sha256': data['seed_source_sha256'], 'union_reference_tokens': seed_tokens,
            'head_lines_checked_against_masked_workspace': agreement['head_lines_checked'],
            'mismatched_lines': agreement['mismatched'], 'all_snippets_rendered_in_prompt': complete,
            'id_rule': 'context:<key>:<path>:<start>-<end>@<snapshot> for every seed snippet',
            'core_evidence_ids': core_ids, 'rendered_ids_in_initial_prompt': rendered_ids,
            'core_ids_equal_rendered_ids_no_duplicates': id_invariant,
            'snapshot_by_id': {r['id']: r['snapshot'] for r in inputs['seed_snippet_index'].values()},
            'lines_supplied': len(lines_supplied), 'lines_in_historical_seed': len(lines_original),
            'head_snippets': len(head_packet.snippets), 'base_snippets': len(packet.snippets) - len(head_packet.snippets)},
        'diff': {'files': [f.path for f in inputs['diff'].files], 'sha256': oc._sha(inputs['sections']['DIFF'])},
        'packet': {'objective_sha256': oc._sha(packet.objective), 'seed_symbols': [
            {'path': s.path, 'name': s.name, 'kind': s.kind, 'visibility': s.visibility, 'lines': [s.line_start, s.line_end]}
            for s in packet.seed_symbols], 'seed_literals': packet.seed_literals, 'risk_tags': packet.risk_tags},
        'pre_gap': {'detected': [{'id': r.id, 'operation': r.operation, 'path': r.path, 'query': r.query,
                                  'rule_id': r.rule_id} for r in gaps],
                    'coverage_basis': 'head-only seed snippets (base snippets are shown to the model but do not count as read head content)',
                    'detected_if_base_counted': [{'id': r.id, 'operation': r.operation, 'path': r.path, 'query': r.query}
                                                 for r in gaps_base_counted],
                    'executed_after_product_slice': [r.id for r in sliced],
                    'note': 'executed by the product loop before the first model call; counted in the 6-lookup total; '
                            'their results are part of the first model input (not a pure seed)'},
        'initial_prompt_without_prestep': {'reference_tokens_o200k': oc.count_reference_tokens(initial_text),
                                           'sha256': oc._sha(initial_text)},
    }


def types_ns():
    import types
    return types.SimpleNamespace(batch_label=lambda: 'prepare')


def prepare(exp: Experiment) -> dict:
    results, created = {}, {}
    for key in directions():
        results[key] = prepare_direction(key)
        created[key] = write_or_verify(exp.prepared / 'inputs' / f'{key}.json', results[key])
    jobs = make_jobs()
    created['jobs'] = write_or_verify(exp.prepared / 'jobs.json', {
        'seed': SHUFFLE_SEED, 'arms': list(ARM_ORDER), 'count': len(jobs), 'jobs': jobs,
        'order_rule': 'direction blocks shuffled with random.Random(seed); arms shuffled inside each block'})
    manifest = {'directions': {k: {'ok': v['ok'], 'problems': v['problems'], 'sha256': sha(exp.prepared / 'inputs' / f'{k}.json')}
                               for k, v in results.items()},
                'jobs_sha256': sha(exp.prepared / 'jobs.json'), 'job_count': len(jobs),
                'all_ok': all(v['ok'] for v in results.values()),
                'product_tree_sha256': ad.verify_snapshot()['tree_sha256']}
    created['manifest'] = write_or_verify(exp.prepared / 'manifest.json', manifest)
    return {'directions': len(results), 'all_ok': manifest['all_ok'], 'jobs': len(jobs), 'files': created,
            'problems': {k: v['problems'] for k, v in results.items() if v['problems']},
            'pre_gap_counts': {k: len(v['pre_gap']['executed_after_product_slice']) for k, v in results.items()}}


# ============================================================ test + smoke (model-free)
def archive_failed(path: Path) -> None:
    """A verification record for this code hash may be replaced only when it FAILED; the failed one is kept aside."""
    path = Path(path)
    if not path.exists():
        return
    if path.is_file() and json.loads(path.read_text()).get('passed'):
        raise ExperimentError(f'{path.name} already passed for this code hash; not overwritten')
    path.rename(path.with_name(f'{path.name}.failed.{int(time.time())}'))


def run_tests(exp: Experiment) -> dict:
    digest = code_sha()[:12]
    path = exp.verification / f'tests_{digest}.json'
    junit = exp.verification / f'tests_{digest}.junit.xml'
    exp.verification.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'pytest', *[str(HERE / t) for t in TEST_FILES if (HERE / t).exists()], '-q',
               '-p', 'no:cacheprovider', f'--junitxml={junit}']
    env = {**os.environ, 'PYTHONPYCACHEPREFIX': str(HERE / '.pycache')}
    started = time.time()
    done = subprocess.run(command, cwd=HERE, env=env, capture_output=True, text=True, timeout=1500)
    tail = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ''
    record = {'code_sha256_prefix': digest, 'code_hashes': code_hashes(), 'returncode': done.returncode,
              'summary': tail, 'passed': done.returncode == 0, 'seconds': time.time() - started,
              'command': command, 'stdout_tail': done.stdout[-3000:], 'stderr_tail': done.stderr[-1000:]}
    archive_failed(path)
    ct.write_new(path, record)
    return {k: record[k] for k in ('passed', 'summary', 'seconds', 'code_sha256_prefix')}


def check_invariants(result: dict) -> list:
    """Counters that must hold on every review, read from the real trace. Returns violation strings."""
    bad = []
    q, caps = result['queries'], ad.LIMITS
    if q['total'] > caps['queries_total']:
        bad.append(f'queries_total {q["total"]}')
    for b in q['batches']:
        if b['count'] > caps['queries_per_batch']:
            bad.append(f'batch {b["label"]} has {b["count"]}')
    if len([b for b in q['batches'] if b['label'] != 'pre_gap']) > caps['rounds_after_initial']:
        bad.append('too many rounds')
    if result['model_calls'] > caps['model_calls']:
        bad.append(f'model calls {result["model_calls"]}')
    if result['source_exposure']['union_reference_tokens'] > caps['source_union_reference_tokens']:
        bad.append('source union over cap')
    metadata_running = 0         # admitted metadata stays in every later prompt, so the running sum must stay within the cap
    for entry in result['tool_trace']:
        for n in entry.get('normalized', []):
            if n.get('kind') != 'source_lines' and n.get('admitted'):
                metadata_running += n.get('metadata_tokens', 0)
                if metadata_running > caps['metadata_tokens_per_call']:
                    bad.append(f'metadata over cap {n["evidence_id"]} (cumulative {metadata_running})')
            if n.get('native') and n.get('provenance') != 'native_lsp':
                bad.append(f'native flag on non-native {n["evidence_id"]}')
            if n.get('provenance') == 'ast_fallback' and n.get('native'):
                bad.append(f'fallback labelled native {n["evidence_id"]}')
    for stage in result['stages']:
        block = stage['state_block']
        if block.get('tokens', 0) + block.get('error_tokens', 0) > caps['state_tokens_per_call']:
            bad.append(f'state block over cap at {stage["stage"]}')
    expected_calls = ('run_llm_review_result', '_context_requests_from_payload', '_parse_json') if result.get('arm') == 'B0' else (
                 'run_llm_review_result', 'detect_public_reference_gaps', 'detect_related_test_gaps',
                 'detect_declaration_evidence_gaps', 'detect_observation_path_gaps', '_context_requests_from_payload',
                 '_parse_json')
    for name in expected_calls:
        if name not in result['product_calls'] and result['model_calls'] > 0:
            bad.append(f'product function {name} was never called')
    if result['seed_agreement']['mismatched']:
        bad.append('seed/masked workspace mismatch')
    exposure = result['source_exposure']
    if exposure.get('protected_seed_ids_intact') is False:
        bad.append('a protected seed evidence id was lost')
    if exposure.get('consistency_blocks'):
        bad.append(f'{len(exposure["consistency_blocks"])} tool results disagreed with already exposed source lines')
    bad += query_reconciliation(result)
    if 'b_closure' in result:
        bad += b_invariants(result)
    if 'controller' in result:
        bad += c_invariants(result)
    if result.get('arm') == 'B0':
        if result['model_calls'] != 1 or q['total'] or result['tool_trace'] or result['lsp_status']:
            bad.append('B0 did not use exactly one judgement and zero lookups/native services')
        if exposure['seed_tokens'] or exposure['union_reference_tokens'] or result['rendered_seed_ids'] or result['ledger']:
            bad.append('B0 received source seed or ledger state')
        if result.get('b0_input_audit', {}).get('only_diff_evidence') is not True:
            bad.append('B0 actual input was not verified diff-only')
    return bad


def query_reconciliation(result: dict) -> list:
    """Every executed lookup (product-requested or automatic) is charged exactly once and has exactly one event."""
    bad, q = [], result['queries']
    if sum(b['count'] for b in q['batches']) != q['total']:
        bad.append('batch counts do not add up to the query total')
    trace = result.get('product_trace')
    if trace is not None and len(trace.get('tool_events', [])) != q['total']:
        bad.append(f'{len(trace["tool_events"])} tool events for {q["total"]} charged queries')
    for b in q['batches']:
        if 'auto' in b and b['count'] != len(b['primary']) + len(b['auto']):
            bad.append(f'batch {b["label"]}: count {b["count"]} != primary {len(b["primary"])} + automatic {len(b["auto"])}')
    if trace is not None:
        primaries = [r['id'] for r in trace.get('requests', [])]
        charged = [r for b in q['batches']
                   for r in [*b.get('primary', b.get('requests', [])), *b.get('scheduled_auto', [])]]
        if primaries != charged:
            bad.append('requests in the product trace differ from the charged primary requests')
    return bad


def b_invariants(result: dict) -> list:
    """B: automatic reads are real, charged, located, never native, never beyond the batch's free slots."""
    bad = []
    executed_before, auto_seen = set(), set()
    autos = {e['request']['id']: e for e in result['tool_trace'] if e.get('auto')}
    for b in result['queries']['batches']:
        executed_before |= set(b.get('primary', []))
        for rid, locator in zip(b.get('auto', []), b.get('auto_locators', [])):
            entry = autos.get(rid)
            if entry is None:
                bad.append(f'auto read {rid} charged but not in the tool trace')
                continue
            auto_seen.add(rid)
            if locator not in executed_before:
                bad.append(f'auto read {rid}: locator {locator} was not executed before it')
            if entry['batch'] != b['label']:
                bad.append(f'auto read {rid} executed in {entry["batch"]} but charged to {b["label"]}')
            if entry['request']['operation'] != 'read_lines' or entry.get('range_basis') != 'python-ast':
                bad.append(f'auto read {rid} is not a python-ast ranged read_lines')
            for n in entry.get('normalized', []):
                if n.get('native') or n.get('provenance') != 'read_lines':
                    bad.append(f'auto read {rid} provenance {n.get("provenance")} native={n.get("native")}')
        executed_before |= set(b.get('auto', []))
    if set(autos) - auto_seen:
        bad.append(f'auto reads executed but never charged: {sorted(set(autos) - auto_seen)}')
    return bad


def c_invariants(result: dict) -> list:
    """C: handles are a bijection over exposed ids, every returned handle is traced, no generated reason is invented."""
    bad, ctl = [], result['controller']
    rows = ctl['handles']['handles']
    if len({r['handle'] for r in rows}) != len(rows) or len({r['canonical_id'] for r in rows}) != len(rows):
        bad.append('evidence handle table is not one-to-one')
    provided = {s['stage']: set(s['provided_evidence_ids']) for s in result['stages']}
    by_handle = {r['handle']: r['canonical_id'] for r in rows}
    for stage, info in ctl['handles']['stages'].items():
        for h in info['handles']:
            if by_handle.get(h) not in provided.get(stage, set()):
                bad.append(f'{stage}: handle {h} names evidence that was not provided to that call')
    if ctl.get('final_untraced'):
        bad.append(f'returned handles without a prompt occurrence: {ctl["final_untraced"]}')
    raw_reasons = set()
    for payload in ctl['raw_payloads']:
        for c in payload.get('candidates') or []:
            if isinstance(c, dict):
                raw_reasons.add(str(c.get('reason', '')).strip())
                for key in ('obligations', 'routes', 'challenges', 'remove'):
                    raw_reasons |= {str(x.get('reason', '')).strip() for x in c.get(key) or [] if isinstance(x, dict)}
    for report in ctl['reports']:
        for rec in report.get('generated_records', []):
            r = rec.get('record')
            if r is not None and (not r.get('reason') or r['reason'] not in raw_reasons):
                bad.append(f'generated record for {rec["target"]} carries a reason the model did not write')
    return bad


def smoke_script_for(arm: str):
    if arm == 'B0':
        return fx.smoke_script_b0
    return fx.smoke_script_c if ad.POLICIES[arm][2] else fx.smoke_script


def smoke(exp: Experiment, keys=SMOKE_KEYS, arms=ARM_ORDER) -> dict:
    digest = code_sha()[:12]
    folder = exp.verification / f'smoke_{digest}'
    report_path = exp.verification / f'smoke_{digest}.json'
    archive_failed(report_path)
    if folder.exists():
        folder.rename(folder.with_name(f'{folder.name}.failed.{int(time.time())}'))
    product = ad.load_product()
    rows = []
    for key in keys:
        for arm in arms:
            out = folder / 'reviews'
            client = fx.FixtureClient(out_dir=out, review_id=f'{key}_{arm}', script=smoke_script_for(arm))
            result = ad.run_review(key, arm, client, product=product)
            violations = check_invariants(result)
            native_ev = [n for e in result['tool_trace'] for n in e.get('normalized', []) if n.get('native') and n.get('admitted')]
            fallback_ev = [n for e in result['tool_trace'] for n in e.get('normalized', []) if n.get('provenance') == 'ast_fallback' and n.get('admitted')]
            write_json_new(folder / 'results' / f'{key}_{arm}.json', result)
            rows.append({'key': key, 'arm': arm, 'status': result['status'], 'end_reason': result['end_reason'],
                         'violations': violations, 'model_calls': result['model_calls'],
                         'queries_total': result['queries']['total'],
                         'batches': [(b['label'], b['count']) for b in result['queries']['batches']],
                         'union_tokens': result['source_exposure']['union_reference_tokens'],
                         'native_lsp_evidence': len(native_ev), 'ast_fallback_evidence': len(fallback_ev),
                         'native_backends': sorted({n['backend'] for n in native_ev}),
                         'lsp_state': {k: v['state'] for k, v in result['lsp_status'].items()},
                         'product_calls': result['product_calls'],
                         'ledger_rejections': len((result['ledger'] or {}).get('rejections', [])) if result['ledger'] else None,
                         'refused_retries': result['refused_retries'],
                         'schedule': result['schedule'], 'initial_input': result['initial_input'],
                         'citation_audit': result['citation_audit'],
                         'b_auto_reads': (result['b_closure']['auto_reads_by_batch'] if 'b_closure' in result else None),
                         'c_generated_records': ([r for rep in result['controller']['reports'] for r in rep.get('generated_records', [])]
                                                 if 'controller' in result else None),
                         'c_final_handle_trace': result['controller']['final_handle_trace'] if 'controller' in result else None})
    ok = all(not r['violations'] and r['status'] == 'completed' for r in rows) and any(r['native_lsp_evidence'] for r in rows)
    # the smoke must exercise the interventions, not pass on no-op arms
    exercised = {'B0_diff_only': all(r['model_calls'] == 1 and r['queries_total'] == 0 and r['union_tokens'] == 0
                                    for r in rows if r['arm'] == 'B0') if 'B0' in arms else None,
                 'B_auto_read': any(sum(len(v) for v in (r['b_auto_reads'] or {}).values()) for r in rows
                                   if ad.POLICIES[r['arm']][3]) if any(ad.POLICIES[a][3] for a in arms) else None,
                 'C_generated_record': any(any(g.get('record') for g in (r['c_generated_records'] or [])) for r in rows
                                           if ad.POLICIES[r['arm']][2]) if any(ad.POLICIES[a][2] for a in arms) else None,
                 'C_handles_traced': all(r['c_final_handle_trace'] and all(t['traced'] for t in r['c_final_handle_trace'])
                                         for r in rows if ad.POLICIES[r['arm']][2]) if any(ad.POLICIES[a][2] for a in arms) else None}
    ok = ok and all(v is not False for v in exercised.values())
    report = {'code_sha256_prefix': digest, 'passed': ok, 'fixture_not_a_model': True, 'exercised': exercised, 'rows': rows,
              'product_snapshot': {k: v for k, v in ad.verify_snapshot().items() if k != 'modules'},
              'note': 'no model was called; fixture client text is scripted and is not a performance result'}
    ct.write_new(report_path, report)
    return {'passed': ok, 'report': str(report_path), 'exercised': exercised, 'rows': [{k: r[k] for k in (
            'key', 'arm', 'status', 'violations', 'queries_total', 'batches', 'union_tokens', 'native_lsp_evidence',
            'ast_fallback_evidence', 'b_auto_reads')} for r in rows]}


# ============================================================ freeze
def latest_verification(exp: Experiment, prefix: str) -> tuple:
    digest = code_sha()[:12]
    path = exp.verification / f'{prefix}_{digest}.json'
    if not path.exists():
        raise ExperimentError(f'{path.name} is missing: run `{prefix.replace("tests", "test")}` for the current code first')
    record = json.loads(path.read_text())
    if not record.get('passed'):
        raise ExperimentError(f'{path.name} did not pass')
    return path, record


def prepared_inputs(exp: Experiment, manifest: dict) -> tuple:
    """Re-hash every prepared/inputs file against the manifest and the authoritative source files against the
    hashes recorded inside it. Returns (disk hashes, authoritative hashes) or raises."""
    disk, authoritative, bad = {}, {}, []
    for key, entry in manifest['directions'].items():
        path = exp.prepared / 'inputs' / f'{key}.json'
        if not path.is_file():
            bad.append(f'{key}: prepared input file missing')
            continue
        disk[key] = sha(path)
        if disk[key] != entry['sha256']:
            bad.append(f'{key}: prepared file differs from prepared/manifest.json')
            continue
        recorded = json.loads(path.read_text(encoding='utf-8')).get('authoritative')
        if not recorded:
            bad.append(f'{key}: prepared file records no authoritative input hashes')
            continue
        case_id, world = key.split('_')
        actual = ad.authoritative_hashes(case_id, world)
        authoritative[key] = {**actual, 'workspace_tree_sha256': recorded['workspace_tree_sha256']}
        if any(actual[n] != recorded[n] for n in ad.AUTHORITATIVE):
            bad.append(f'{key}: case/seed/workspace manifest differs from what was prepared')
    if bad:
        raise ExperimentError('prepared inputs are inconsistent: ' + '; '.join(bad[:6]))
    return disk, authoritative


def freeze_state(exp: Experiment) -> dict:
    missing = [n for n in (*CODE_FILES, *TEST_FILES, *DOC_FILES) if not (HERE / n).is_file()]
    if missing:
        raise ExperimentError(f'files named in the freeze lists are missing: {missing}')
    manifest_path = exp.prepared / 'manifest.json'
    if not manifest_path.exists():
        raise ExperimentError('prepared/manifest.json is missing: run `prepare` first')
    manifest = json.loads(manifest_path.read_text())
    if not manifest['all_ok']:
        raise ExperimentError('prepare reported problems; see prepared/manifest.json')
    disk, authoritative = prepared_inputs(exp, manifest)
    tests_path, _ = latest_verification(exp, 'tests')
    smoke_path, _ = latest_verification(exp, 'smoke')
    return {
        'code': code_hashes(), 'docs': {n: sha(HERE / n) for n in DOC_FILES if (HERE / n).exists()},
        'algorithm_dependencies': ace_dependency_files(),
        'product': {k: v for k, v in ad.verify_snapshot().items()},
        'prepared_manifest_sha256': sha(manifest_path), 'jobs_sha256': sha(exp.prepared / 'jobs.json'),
        'directions_sha256': disk, 'authoritative_inputs': authoritative,
        'verification': {'tests': {'file': tests_path.name, 'sha256': sha(tests_path)},
                         'smoke': {'file': smoke_path.name, 'sha256': sha(smoke_path)}},
        'settings': {'model': ct.MODEL, 'reasoning_effort': 'low', 'workers': WORKERS, 'max_workers': MAX_WORKERS,
                     'min_start_interval_seconds': ct.MIN_START_INTERVAL,
                     'cli_timeout_seconds': CLI_TIMEOUT, 'limits': ad.LIMITS, 'state_caps': ad.STATE_CAPS,
                     'response_schema_sha256': oc._sha(ad.RESPONSE_SCHEMA), 'config_delta': ad.config_delta(),
                     'patches': ad.PATCHES, 'shuffle_seed': SHUFFLE_SEED, 'arms': ad.V3_ARMS, 'arm_order': ARM_ORDER,
                     'c_response_schema_sha256': oc._sha(ad.C_RESPONSE_SCHEMA),
                     'prompt_blocks_sha256': {'common_contract': oc._sha(ad.COMMON_CONTRACT), 'c_contract': oc._sha(ad.C_CONTRACT),
                                              'ledger_rules': oc._sha(ad.LEDGER_RULES), 'c_state_rules': oc._sha(cc.C_STATE_RULES),
                                              'b_note': oc._sha(ad.B_NOTE)},
                     'b_constants': {'read_max_lines': rt.READ_MAX_LINES, 'max_units_per_locator': rt.MAX_UNITS_PER_LOCATOR,
                                     'max_declarations': rt.MAX_DECLARATIONS}},
    }


def freeze(exp: Experiment) -> dict:
    state = freeze_state(exp)
    record = {'frozen_at': time.time(), 'state': state, 'state_sha256': oc._sha(state)}
    ct.write_new(exp.freeze_path, record)
    return {'freeze': str(exp.freeze_path), 'state_sha256': record['state_sha256'], 'code_files': len(state['code']),
            'jobs': exp.jobs() and len(exp.jobs())}


def verify_freeze(exp: Experiment) -> dict:
    if not exp.freeze_path.exists():
        raise ExperimentError('freeze.json is missing: run `freeze` (after the parent code review)')
    frozen = json.loads(exp.freeze_path.read_text())
    now = freeze_state(exp)
    if oc._sha(now) != frozen['state_sha256']:
        diff = [k for k in now if oc._sha(now[k]) != oc._sha(frozen['state'].get(k))]
        raise ExperimentError(f'code/inputs/protocol changed since freeze: {diff}')
    return frozen


# ============================================================ review execution (worker side)
def make_client(exp: Experiment, review_id: str, fixture: bool, arm: str = 'L'):
    """C/CB use the C response schema (only `candidates` differs); every other arm the V2 schema."""
    c_format = ad.POLICIES[arm][2]
    if fixture:
        return fx.FixtureClient(out_dir=exp.reviews, review_id=review_id,
                                script=fx.smoke_script_b0 if arm == 'B0' else fx.generic_script_c if c_format else fx.generic_script)
    gate = ct.AdmissionGate(exp.gate_dir)
    return ct.CliCompletionClient(out_dir=exp.reviews, review_id=review_id, schema=ad.response_schema(arm), gate=gate,
                                  timeout=CLI_TIMEOUT)


def transport_summary(folder: Path) -> dict:
    stages = {}
    for stage in ad.STAGES:
        result = folder / f'{stage}.result.json'
        claim = folder / f'{stage}.claim.json'
        if result.exists():
            r = json.loads(result.read_text())
            stages[stage] = {'claimed': True, 'status': r.get('status'), 'failure_kinds': r.get('failure_kinds'),
                             'actual_usage': r.get('actual_usage'), 'actual_tool_calls': r.get('actual_tool_calls', 0),
                             'timing': r.get('timing'), 'reference_tokens_o200k_input': r.get('reference_tokens_o200k_input'),
                             'resource_failure': r.get('resource_failure'), 'fixture': r.get('fixture_not_a_model', False)}
        elif claim.exists():
            stages[stage] = {'claimed': True, 'status': 'claimed_incomplete'}
    totals = {}
    for s in stages.values():
        for key, value in ((s.get('actual_usage') or {}).get('total') or {}).items():
            totals[key] = totals.get(key, 0) + value
    return {'stages': stages, 'usage_total': totals, 'actual_tool_calls_total': sum(s.get('actual_tool_calls', 0) for s in stages.values()),
            'claims': len([s for s in stages.values() if s.get('claimed')])}


def authorize(exp: Experiment, *, launch_model: bool, fixture: bool) -> None:
    """The one gate for scheduler AND worker: exactly one model mode, official root only for real calls, current freeze."""
    if launch_model == fixture:
        raise ExperimentError('run needs exactly one of --launch-model (real Codex CLI) or --fixture-model (tests only)')
    if fixture and exp.official:
        raise ExperimentError('--fixture-model is refused for the official experiment root')
    if launch_model:
        if not exp.official:
            raise ExperimentError('--launch-model is only allowed for the official root')
        verify_freeze(exp)


def hold_lock(path: Path):
    """Non-blocking exclusive flock; returns the open fd (held until the process exits) or None."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def acquire_worker_slot(exp: Experiment) -> tuple:
    for slot in range(MAX_WORKERS):
        fd = hold_lock(exp.results / 'slots' / f'slot{slot}.lock')
        if fd is not None:
            return slot, fd
    raise ExperimentError(f'no free worker slot: {MAX_WORKERS} workers already run on this result root')


def prepared_expected(exp: Experiment, key: str):
    """Authoritative input hashes recorded at preparation (and covered by the freeze) for this direction."""
    path = exp.prepared / 'inputs' / f'{key}.json'
    if path.is_file():
        return json.loads(path.read_text(encoding='utf-8'))['authoritative']
    if exp.official:
        raise ExperimentError(f'{path} is missing for the official root')
    return None                      # synthetic fixture roots only: no prepared hashes to compare with


def run_worker(exp: Experiment, review_id: str, fixture: bool, launch_model: bool = False) -> dict:
    authorize(exp, launch_model=launch_model, fixture=fixture)        # before any claim file or client exists
    job = next((j for j in exp.jobs() if j['review_id'] == review_id), None)
    if job is None:
        raise ExperimentError(f'unknown review id {review_id}')
    expected = prepared_expected(exp, job['key'])
    slot, _slot_fd = acquire_worker_slot(exp)
    folder = exp.reviews / review_id
    folder.mkdir(parents=True, exist_ok=True)
    ct.write_new(folder / 'review.started.json', {'review_id': review_id, 'pid': os.getpid(), 'at': time.time(),
                                                   'fixture': fixture, 'launch_model': launch_model, 'slot': slot})
    if fixture and os.environ.get('OAR_FIXTURE_CRASH') == review_id:       # test hook: scripted-client runs only
        os._exit(3)
    client = make_client(exp, review_id, fixture, job['arm'])
    try:
        result = ad.run_review(job['key'], job['arm'], client, expected_inputs=expected)
    except BaseException as exc:                                              # nothing is retried or swallowed
        result = {'review_id': review_id, 'key': job['key'], 'arm': job['arm'], 'status': 'operational_failure',
                  'end_reason': f'worker_exception:{type(exc).__name__}',
                  'error': {'type': type(exc).__name__, 'message': str(exc)[:2000]}}
    summary = transport_summary(folder)
    result['transport'] = summary
    result['worker_slot'] = slot
    if summary['actual_tool_calls_total']:
        result['status'] = 'invalid_model_tool_use'
    ct.write_new(folder / 'review.result.json', result)
    return {'review_id': review_id, 'status': result['status'], 'end_reason': result['end_reason']}


# ============================================================ scheduler
def job_state(exp: Experiment, review_id: str) -> str:
    folder = exp.reviews / review_id
    if (folder / 'review.result.json').exists():
        return 'complete'
    if (folder / 'review.crash.json').exists():
        return 'crashed'
    if folder.exists() and any(folder.iterdir()):
        return 'incomplete_claim'
    return 'pending'


def run(exp: Experiment, *, launch_model: bool, fixture: bool, workers: int = WORKERS, only=None, poll=0.5) -> dict:
    authorize(exp, launch_model=launch_model, fixture=fixture)
    if not 1 <= workers <= MAX_WORKERS:
        raise ExperimentError(f'--workers must be between 1 and {MAX_WORKERS} (got {workers})')
    exp.reviews.mkdir(parents=True, exist_ok=True)
    scheduler_fd = hold_lock(exp.results / 'scheduler.lock')       # one scheduler per result root at a time
    if scheduler_fd is None:
        raise ExperimentError('another scheduler is running on this result root; two schedulers would exceed the worker cap')
    try:
        return _schedule(exp, fixture=fixture, workers=workers, only=only, poll=poll)
    finally:
        os.close(scheduler_fd)


def _schedule(exp: Experiment, *, fixture: bool, workers: int, only, poll: float) -> dict:
    gate = ct.AdmissionGate(exp.gate_dir)
    jobs = [j for j in exp.jobs() if only is None or j['review_id'] in only]
    log_path = exp.results / 'run_log.jsonl'
    log = lambda event: log_path.open('a', encoding='utf-8').write(json.dumps({'t': time.time(), **event}) + '\n')
    queue, skipped = [], []
    for job in jobs:
        state = job_state(exp, job['review_id'])
        if state == 'pending':
            queue.append(job)
        else:
            skipped.append({'review_id': job['review_id'], 'state': state})
            log({'event': 'not_admitted', 'review_id': job['review_id'], 'state': state})
    running: dict = {}
    peak = 0
    log({'event': 'scheduler_start', 'workers': workers, 'max_workers': MAX_WORKERS, 'pid': os.getpid(),
         'queue': [j['review_id'] for j in queue]})
    stopped = False
    unattempted = []
    env = {**os.environ, 'PYTHONPYCACHEPREFIX': str(HERE / '.pycache')}
    while queue or running:
        for review_id, (proc, handles) in list(running.items()):
            if proc.poll() is None:
                continue
            for handle in handles:
                handle.close()
            running.pop(review_id)
            folder = exp.reviews / review_id
            if not (folder / 'review.result.json').exists():
                ct.write_new(folder / 'review.crash.json', {'returncode': proc.returncode, 'at': time.time(),
                                                            'note': 'worker exited without a result; never re-run automatically'})
            log({'event': 'worker_exit', 'review_id': review_id, 'returncode': proc.returncode})
        if gate.is_stopped() and not stopped:
            stopped = True
            log({'event': 'global_stop', 'detail': gate.is_stopped(), 'running': list(running)})
        if stopped and queue:
            for job in queue:
                unattempted.append(job['review_id'])
                gate.record_unattempted(job['review_id'], 'global stop: no new admission')
            log({'event': 'unattempted', 'review_ids': unattempted})
            queue = []
        while queue and len(running) < workers and not stopped:
            job = queue.pop(0)
            folder = exp.reviews / job['review_id']
            folder.mkdir(parents=True, exist_ok=True)
            out, err = (open(folder / 'worker.stdout.log', 'x'), open(folder / 'worker.stderr.log', 'x'))
            command = [sys.executable, '-B', str(HERE / 'oar_experiment.py'), '--root', str(exp.root), 'worker',
                       '--review-id', job['review_id']] + (['--fixture-model'] if fixture else ['--launch-model'])
            proc = subprocess.Popen(command, stdout=out, stderr=err, env=env, cwd=HERE)
            running[job['review_id']] = (proc, (out, err))
            peak = max(peak, len(running))
            log({'event': 'admitted', 'review_id': job['review_id'], 'pid': proc.pid, 'concurrent': len(running)})
        if running or queue:
            time.sleep(poll)
    log({'event': 'scheduler_end', 'peak_concurrency': peak})
    return {'admitted_or_finished': len(jobs) - len(skipped) - len(unattempted), 'not_admitted': skipped,
            'workers_requested': workers, 'peak_concurrency': peak,
            'unattempted_after_stop': unattempted, 'stopped': bool(gate.is_stopped())}


# ============================================================ status / inspect
def status(exp: Experiment) -> dict:
    jobs = exp.jobs()
    counts, per_arm, rows = {}, {}, []
    usage, claims, calls_ok, tool_calls = {}, 0, 0, 0
    for job in jobs:
        state = job_state(exp, job['review_id'])
        detail = None
        if state == 'complete':
            r = json.loads((exp.reviews / job['review_id'] / 'review.result.json').read_text())
            detail = r['status']
            t = r.get('transport', {})
            claims += t.get('claims', 0)
            tool_calls += t.get('actual_tool_calls_total', 0)
            for k, v in (t.get('usage_total') or {}).items():
                usage[k] = usage.get(k, 0) + v
            calls_ok += sum(1 for s in t.get('stages', {}).values() if s.get('status') == 'ok')
            state = f'complete:{detail}'
        elif state == 'incomplete_claim':
            folder = exp.reviews / job['review_id']
            claims += len(list(folder.glob('*.claim.json')))
        counts[state] = counts.get(state, 0) + 1
        per_arm.setdefault(job['arm'], {})
        per_arm[job['arm']][state] = per_arm[job['arm']].get(state, 0) + 1
    gate = ct.AdmissionGate(exp.gate_dir) if exp.gate_dir.exists() else None
    starts = gate.starts() if gate else []
    return {'root': str(exp.root), 'jobs': len(jobs), 'states': counts, 'per_arm': per_arm,
            'model_calls_claimed': claims, 'model_calls_ok': calls_ok, 'actual_tool_calls': tool_calls,
            'usage_total': usage, 'cli_starts': len(starts), 'min_gap_seconds': ct.min_gap(starts),
            'global_stop': gate.is_stopped() if gate else None,
            'unattempted': ([json.loads(l) for l in gate.unattempted_path.read_text().splitlines()]
                            if gate and gate.unattempted_path.exists() else []),
            'cap_total_calls': 3 * len(jobs)}


def inspect(exp: Experiment, review_id: str, full: bool = False) -> dict:
    path = exp.reviews / review_id / 'review.result.json'
    if not path.exists():
        return {'review_id': review_id, 'state': job_state(exp, review_id),
                'files': sorted(p.name for p in (exp.reviews / review_id).glob('*')) if (exp.reviews / review_id).exists() else []}
    r = json.loads(path.read_text())
    if full:
        return r
    return {'review_id': review_id, 'arm': r.get('arm'), 'status': r.get('status'), 'end_reason': r.get('end_reason'),
            'error': r.get('error'), 'model_calls': r.get('model_calls'), 'transport': r.get('transport'),
            'queries': r.get('queries'), 'source_exposure': {k: v for k, v in (r.get('source_exposure') or {}).items() if k != 'log'},
            'initial_input': r.get('initial_input'), 'native_summary': r.get('native_summary'),
            'lsp_status': {k: v.get('state') for k, v in (r.get('lsp_status') or {}).items()},
            'schedule': r.get('schedule'), 'stages': [{k: v for k, v in s.items() if k != 'provided_evidence_ids'} for s in r.get('stages', [])],
            'findings': [{'title': f.get('title'), 'path': f.get('path'), 'lines': [f.get('line_start'), f.get('line_end')],
                          'candidate_id': f.get('candidate_id')} for f in r.get('findings_raw', [])],
            'citation_audit': r.get('citation_audit'),
            'ledger': None if not r.get('ledger') else {'status': r['ledger'].get('status'), 'reportable': r['ledger'].get('reportable'),
                                                       'unresolved': r['ledger'].get('unresolved'),
                                                       'rejections': len(r['ledger'].get('rejections', []))}}


# ============================================================ cli
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='oar_experiment.py', description=__doc__.split('\n')[0])
    parser.add_argument('--root', default=None, help='experiment root (default: this folder = the official pilot)')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('preflight', 'prepare', 'test', 'freeze', 'status'):
        sub.add_parser(name)
    p = sub.add_parser('smoke')
    p.add_argument('--keys', default=','.join(SMOKE_KEYS))
    p.add_argument('--arms', default=','.join(ARM_ORDER))
    p = sub.add_parser('run')
    p.add_argument('--launch-model', action='store_true', help='REQUIRED for real Codex CLI calls')
    p.add_argument('--fixture-model', action='store_true', help='scripted client; refused for the official root')
    p.add_argument('--workers', type=int, default=WORKERS)
    p.add_argument('--only', default=None, help='comma separated review ids')
    p = sub.add_parser('inspect')
    p.add_argument('review_id')
    p.add_argument('--full', action='store_true')
    p = sub.add_parser('worker', help='internal: started by `run`; refuses to start without the same gates')
    p.add_argument('--review-id', required=True)
    p.add_argument('--launch-model', action='store_true')
    p.add_argument('--fixture-model', action='store_true')
    args = parser.parse_args(argv)
    exp = Experiment(args.root)
    try:
        if args.command == 'preflight':
            out = preflight()
        elif args.command == 'prepare':
            out = prepare(exp)
        elif args.command == 'test':
            out = run_tests(exp)
        elif args.command == 'smoke':
            out = smoke(exp, tuple(args.keys.split(',')), tuple(args.arms.split(',')))
        elif args.command == 'freeze':
            out = freeze(exp)
        elif args.command == 'run':
            out = run(exp, launch_model=args.launch_model, fixture=args.fixture_model, workers=args.workers,
                      only=set(args.only.split(',')) if args.only else None)
        elif args.command == 'status':
            out = status(exp)
        elif args.command == 'inspect':
            out = inspect(exp, args.review_id, args.full)
        else:
            out = run_worker(exp, args.review_id, args.fixture_model, args.launch_model)
    except ExperimentError as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get('ok', out.get('passed', out.get('all_ok', True))) else 1


if __name__ == '__main__':
    sys.exit(main())
