"""Adapter between the verbatim product snapshot and the OAR core / CLI transport.

Nothing in the product snapshot is edited. The real ``run_agentic_review`` loop, ``SafeToolRegistry``,
gap detectors, prompt renderer and request parser are imported from the hash-verified snapshot and
called. This module only (1) builds the inputs, (2) subclasses the registry to normalise tool output into
the masked-source representation and enforce the pilot budgets, (3) wraps the CompletionClient, and
(4) applies per-process, scoped monkeypatches whose exact list is ``PATCHES`` (documented in INTEGRATION.md).
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import hashlib
import json
import os
import re
import sys
import threading
import time
import types
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
if sys.pycache_prefix is None:
    sys.pycache_prefix = str(HERE / '.pycache')
ROOT = HERE.parent.parent
SNAPSHOT = HERE / 'product_snapshot_20261026'
MANIFEST = HERE / 'parent_audit' / 'product_snapshot_20261026.json'
SRC_RUN = ROOT / 'runs' / 'lsp_symbol_graph_20261025'
PYRIGHT_BIN = ROOT / 'tools' / 'pyright-lsp-20261023' / 'node_modules' / '.bin'
ACE_PARENT = os.environ.get('OAR_ACE_PARENT', str(ROOT))
if ACE_PARENT not in sys.path:
    sys.path.insert(0, ACE_PARENT)

import oar_core as oc  # noqa: E402

ARMS = {'P': (False, False), 'A': (True, False), 'R': (False, True), 'AR': (True, True)}   # arm -> (A, R)
STAGES = ('initial', 'update_1', 'update_2')
LIMITS = {'queries_total': 6, 'queries_per_batch': 3, 'rounds_after_initial': 2, 'model_calls': 3,
          'source_union_reference_tokens': 5200, 'metadata_tokens_per_call': 800,
          'state_tokens_per_call': 1600, 'error_tokens_in_state_block': 200}
STATE_CAPS = dict(max_candidates=3, max_obligations_per_candidate=5, max_routes_per_candidate=3,
                  max_challenges_per_candidate=3, max_notes_per_candidate=3)
PILOT_OPERATIONS = ('read_lines', 'grep_literal', 'related_tests', 'go_to_definition', 'find_references',
                    'find_implementations', 'incoming_calls', 'outgoing_calls', 'document_symbols',
                    'symbol_search')
# product modules whose bytes decide what "the product" means for this pilot (all are also in the manifest)
PRODUCT_MODULES = ('review_agent/agents/orchestrator.py', 'review_agent/agents/gap_detector.py',
                   'review_agent/agents/llm_reviewer.py', 'review_agent/agents/prompt_context.py',
                   'review_agent/agents/context_consumption.py', 'review_agent/agents/evidence_catalog.py',
                   'review_agent/tools/registry.py', 'review_agent/config.py', 'review_agent/models.py',
                   'review_agent/diff/parser.py', 'review_agent/diff/seeds.py', 'review_agent/context/packet.py',
                   'review_agent/lsp/client.py', 'review_agent/code_intelligence/tree_sitter_index.py',
                   'prompts/code-review-system.json', 'review_agent/sops/mr-code-review.sop.md')


class AdapterError(RuntimeError):
    pass


class BudgetViolation(AdapterError):
    pass


class ToolResultTruncated(AdapterError):
    """A structured tool result arrived cut off or malformed; the lookup counts as failed (and is still charged)."""


class StateBlocked(AdapterError):
    """The ledger state cannot be rendered inside the per-call state cap; the review stops (cap never raised)."""


def sha_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ref_tokens(text: str) -> int:
    return oc.count_reference_tokens(text)


# ============================================================ product snapshot
def verify_snapshot() -> dict:
    """Re-hash every file named in the parent manifest; raise on any difference."""
    manifest = json.loads(MANIFEST.read_text())
    bad, hashes = [], {}
    for entry in manifest['files']:
        path = SNAPSHOT / entry['path']
        digest = sha_file(path) if path.is_file() else None
        hashes[entry['path']] = digest
        if digest != entry['sha256']:
            bad.append(entry['path'])
    if bad:
        raise AdapterError(f'product snapshot differs from manifest: {bad[:5]} ({len(bad)} files)')
    return {'snapshot': str(SNAPSHOT), 'manifest': str(MANIFEST), 'manifest_sha256': sha_file(MANIFEST),
            'files': len(hashes), 'tree_sha256': oc._sha(sorted(hashes.items())),
            'modules': {m: hashes.get(m) for m in PRODUCT_MODULES}, 'source_of_copy': manifest['source']}


_PRODUCT: Optional[types.SimpleNamespace] = None


def load_product() -> types.SimpleNamespace:
    """Import the verbatim product snapshot (idempotent). Fails if another ``review_agent`` is loaded."""
    global _PRODUCT
    if _PRODUCT is not None:
        return _PRODUCT
    verify_snapshot()
    existing = sys.modules.get('review_agent')
    if existing is not None and not str(Path(existing.__file__).resolve()).startswith(str(SNAPSHOT)):
        raise AdapterError(f'a different review_agent is already imported: {existing.__file__}')
    if str(SNAPSHOT) not in sys.path:
        sys.path.insert(0, str(SNAPSHOT))
    # the product's own environment switches; fixed here so no ambient setting changes the experiment
    os.environ['REVIEW_AGENT_SCIP_TOOL_DIR'] = str(PYRIGHT_BIN)
    os.environ['REVIEW_AGENT_CONTEXT_BUDGET'] = 'fixed'            # keep the whole seed in the prompt
    os.environ['REVIEW_AGENT_STRUCTURAL_CACHE'] = 'false'
    os.environ['REVIEW_AGENT_INCREMENTAL_REVIEW'] = 'false'
    import review_agent.agents.context_consumption as context_consumption
    import review_agent.agents.gap_detector as gap_detector
    import review_agent.agents.llm_reviewer as llm_reviewer
    import review_agent.agents.orchestrator as orchestrator
    import review_agent.agents.prompt_context as prompt_context
    import review_agent.config as config
    import review_agent.context.packet as packet_mod
    import review_agent.diff.parser as diff_parser
    import review_agent.diff.seeds as diff_seeds
    import review_agent.models as models
    import review_agent.tools.registry as registry
    for module in (orchestrator, registry, gap_detector, llm_reviewer):
        if not str(Path(module.__file__).resolve()).startswith(str(SNAPSHOT)):
            raise AdapterError(f'{module.__name__} was not imported from the snapshot: {module.__file__}')
    _PRODUCT = types.SimpleNamespace(
        orchestrator=orchestrator, registry=registry, gap_detector=gap_detector, llm_reviewer=llm_reviewer,
        prompt_context=prompt_context, context_consumption=context_consumption, config=config, packet=packet_mod, diff_parser=diff_parser,
        diff_seeds=diff_seeds, models=models)
    return _PRODUCT


def pilot_config(product=None):
    """ReviewConfig with the pilot's common budget changes (every deviation is listed in CONFIG_DELTA)."""
    product = product or load_product()
    return replace(
        product.config.ReviewConfig(),
        agentic_exploration_enabled=True, agentic_gap_detection_enabled=True,
        agentic_max_iterations=2, agentic_requests_per_iteration=3, agentic_max_requests=6,
        agentic_context_token_budget=10 ** 9,        # the pilot enforces 5200 o200k source tokens itself
        agentic_tool_timeout_seconds=90,             # first pyright request includes server start (~6 s)
        agentic_max_evidence_chars=10_000_000,       # no product-side JSON truncation; the pilot caps listings itself
        lsp_enabled=True, scip_enabled=False, scip_auto_generate=False, structural_cache_enabled=False,
        terraform_graph_enabled=False, incremental_review_enabled=False, llm_prompt_cache_enabled=False)


def config_delta(product=None) -> dict:
    product = product or load_product()
    default, pilot = product.config.ReviewConfig(), pilot_config(product)
    return {f.name: {'product_default': getattr(default, f.name), 'pilot': getattr(pilot, f.name)}
            for f in dataclasses.fields(default) if getattr(default, f.name) != getattr(pilot, f.name)}


# ============================================================ masked source
class MaskError(AdapterError):
    pass


class MaskedSources:
    """Whole-file ``mask_source`` normalisation of the workspace; only requested ranges ever leave this class."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self._cache: dict = {}
        from ace_review.protocol_hypothesis import mask_source
        self._mask = mask_source

    def lines(self, rel: str) -> list:
        if rel in self._cache:
            cached = self._cache[rel]
            if isinstance(cached, MaskError):
                raise cached
            return cached
        path = (self.root / rel).resolve()
        try:
            if not path.is_relative_to(self.root) or not path.is_file():
                raise MaskError(f'{rel}: not a regular file inside the workspace')
            if path.suffix != '.py':
                raise MaskError(f'{rel}: only .py sources have the seed masking convention; not provided')
            masked = self._mask(path.read_text(encoding='utf-8', errors='replace'))
            result = masked.splitlines()
        except MaskError as exc:
            self._cache[rel] = exc
            raise
        except Exception as exc:          # SyntaxError / tokenize errors: never fall back to the raw text
            error = MaskError(f'{rel}: mask_source failed ({type(exc).__name__}: {str(exc)[:120]}); raw text not provided')
            self._cache[rel] = error
            raise error from exc
        self._cache[rel] = result
        return result

    def range(self, rel: str, start: int, end: int) -> dict:
        lines = self.lines(rel)
        return {n: lines[n - 1] for n in range(max(1, start), min(end, len(lines)) + 1)}


def render_lines(lines: dict) -> str:
    return '\n'.join(f'{n}: {text}' for n, text in sorted(lines.items()))


# ============================================================ case inputs
SECTION = re.compile(r'^\[(REPOSITORY|SCOPE|DIFF)\]\n', re.M)
HEADER = re.compile(r'^\[(E-[A-Za-z0-9_.\-]+)\]\s+(head|base)\s+(.+?);')


def split_common(common: str) -> dict:
    parts = SECTION.split(common)
    sections = {parts[i]: parts[i + 1].rstrip('\n') for i in range(1, len(parts) - 1, 2)}
    if set(sections) != {'REPOSITORY', 'SCOPE', 'DIFF'}:
        raise AdapterError(f'common text sections: {sorted(sections)}')
    return sections


def seed_blocks(source_text: str) -> list:
    """Verbatim per-block text of a historical seed (header excluded), with parsed core Evidence."""
    evidence = {e.id: e for e in oc.parse_seed_source(source_text)}
    blocks, current = [], None
    for raw in source_text.splitlines():
        header = HEADER.match(raw)
        if header:
            current = {'historical_id': header.group(1), 'snapshot': header.group(2),
                       'file': header.group(3).strip(), 'text': []}
            blocks.append(current)
        elif current is not None:
            current['text'].append(raw)
    for block in blocks:
        while block['text'] and not block['text'][-1].strip():
            block['text'].pop()
        block['text'] = '\n'.join(block['text'])
        block['evidence'] = evidence[block['historical_id']]
    return blocks


def split_seed_runs(block: dict, ev) -> list:
    """[(core Evidence, verbatim numbered text)] for each contiguous run of a seed block; no line added or dropped."""
    runs, current, last = [], None, None
    for raw in block['text'].splitlines():
        m = oc._NUMBERED.match(raw)
        if not m:
            last = None                         # '... omitted ...' / blank: the run ends
            continue
        number = int(m.group(1))
        if current is None or last is None or number != last + 1:
            current = {'lines': [], 'text': []}
            runs.append(current)
        current['lines'].append((number, m.group(2) or ''))
        current['text'].append(raw)
        last = number
    out = [(dataclasses.replace(ev, lines=tuple(r['lines'])), '\n'.join(r['text'])) for r in runs]
    if [(e.start_line, e.end_line) for e, _ in out] != ev.runs() or tuple(l for e, _ in out for l in e.lines) != ev.lines:
        raise AdapterError(f'{ev.id}: supplied runs do not reproduce the seed block lines')
    return out


AUTHORITATIVE = ('case_sha256', 'seed_sha256', 'workspace_manifest_sha256')


def case_files(case_id: str, world: str) -> dict:
    key = f'{case_id}_{world}'
    return {'case_sha256': SRC_RUN / 'cases' / f'{key}.json',
            'workspace_manifest_sha256': SRC_RUN / 'workspace_manifests' / f'{key}.json',
            'seed_sha256': SRC_RUN / 'packets' / 'seed' / f'{key}_initial.json'}


def authoritative_hashes(case_id: str, world: str) -> dict:
    return {name: sha_file(path) for name, path in case_files(case_id, world).items()}


def load_case(case_id: str, world: str, expected: Optional[dict] = None) -> dict:
    """Case, seed and workspace manifest read ONCE as bytes. With ``expected`` (the prepared/frozen hashes) every byte
    string is verified before it is parsed, so what a worker uses is exactly what the freeze covers."""
    key = f'{case_id}_{world}'
    raw = {name: path.read_bytes() for name, path in case_files(case_id, world).items()}
    actual = {name: hashlib.sha256(b).hexdigest() for name, b in raw.items()}
    if expected is not None:
        bad = [name for name in AUTHORITATIVE if actual[name] != expected.get(name)]
        if bad:
            raise AdapterError(f'{key}: authoritative input changed since preparation/freeze: {bad}')
    case = json.loads(raw['case_sha256'])
    allowed = {k: case[k] for k in ('case_id', 'world', 'instance_id', 'repository', 'common', 'changed_files')}
    manifest = json.loads(raw['workspace_manifest_sha256'])
    seed = json.loads(raw['seed_sha256'])
    return {'key': key, 'case': allowed, 'workspace_root': manifest['root'], 'workspace_sources': manifest['sources'],
            'seed_source': seed['source'], 'seed_source_sha256': oc._sha(seed['source']), 'authoritative': actual,
            'workspace_tree_sha256': oc._sha(sorted(manifest['sources'].items()))}


def verify_workspace(root, sources: dict) -> dict:
    """Hash every source file named in the workspace manifest (read-only)."""
    bad = []
    for rel, digest in sources.items():
        path = Path(root) / rel
        if not path.is_file() or sha_file(path) != digest:
            bad.append(rel)
    if bad:
        raise AdapterError(f'workspace differs from manifest: {bad[:5]} ({len(bad)} files)')
    return {'root': str(root), 'files': len(sources), 'tree_sha256': oc._sha(sorted(sources.items()))}


def snippet_id(packet_id: str, path: str, start: int, end: int, snapshot: str) -> str:
    """Seed snippet id. The product's own id (``context:<packet>:<path>:<start>-<end>``) cannot tell a base snippet from
    a head snippet with the same range, so EVERY seed snippet gets an ``@<snapshot>`` suffix (one rule, no collision case)."""
    return f'context:{packet_id}:{path}:{start}-{end}@{snapshot}'


def snippet_key(snippet) -> tuple:
    return (snippet.path, snippet.start, snippet.end, snippet.reason, snippet.content)


def product_snippet_id(packet_id: str, path: str, start: int, end: int) -> str:
    return f'context:{packet_id}:{path}:{start}-{end}'              # = product evidence_catalog.snippet_evidence_id


def build_inputs(key: str, registry_factory: Callable[[str], Any] | None = None, product=None,
                 expected: Optional[dict] = None) -> dict:
    """Everything a review needs, built with product functions. ``registry_factory(root)`` -> SafeToolRegistry."""
    product = product or load_product()
    case_id, world = key.split('_')
    data = load_case(case_id, world, expected)
    sections = split_common(data['case']['common'])
    diff = product.diff_parser.parse_unified_diff(sections['DIFF'] + '\n')
    changed = [f.path for f in diff.files if f.path]
    if sorted(changed) != sorted(data['case']['changed_files']):
        raise AdapterError(f'{key}: diff files {changed} != case changed_files {data["case"]["changed_files"]}')
    registry = registry_factory(data['workspace_root']) if registry_factory else None
    packet_id = key
    blocks = seed_blocks(data['seed_source'])
    snippets, seed_evidence, id_map, used_ids, snippet_index = [], [], {}, set(), {}
    for block in blocks:
        ev = block['evidence']
        kind = 'historical seed (base revision)' if ev.snapshot == 'base' else 'historical seed (head revision)'
        id_map[block['historical_id']] = []
        # One snippet per CONTIGUOUS supplied run: the product gap detector treats start..end of a snippet as read, so a
        # min..max wrapper around a block with omitted spans would invent coverage for code the model never saw.
        for run_ev, text in split_seed_runs(block, ev):
            sid = snippet_id(packet_id, ev.file, run_ev.start_line, run_ev.end_line, ev.snapshot)
            if sid in used_ids:
                raise AdapterError(f'{key}: seed snippet id collision {sid}')
            used_ids.add(sid)
            id_map[block['historical_id']].append(sid)
            snippet = product.models.ContextSnippet(
                path=ev.file, start=run_ev.start_line, end=run_ev.end_line, content=text, score=0.0,
                reason=f'{kind}: numbered supplied lines only; omitted code unknown')
            if snippet_key(snippet) in snippet_index:
                raise AdapterError(f'{key}: two seed snippets are indistinguishable ({sid})')
            snippet_index[snippet_key(snippet)] = {
                'id': sid, 'snapshot': ev.snapshot, 'product_id': product_snippet_id(packet_id, ev.file, run_ev.start_line, run_ev.end_line),
                'historical_id': block['historical_id']}
            snippets.append(snippet)
            seed_evidence.append(dataclasses.replace(run_ev, id=sid))
    if registry is not None:
        seeds = product.diff_seeds.extract_seeds(data['workspace_root'], diff, code_index=registry.code_index)
        seed_args = dict(seed_symbols=seeds.changed_symbols, seed_literals=seeds.literals, risk_tags=seeds.risk_tags)
    else:
        seed_args = dict(seed_symbols=[], seed_literals=[], risk_tags=[])
    packet = product.packet.make_packet(packet_id, changed, strategy='single', snippets=snippets, max_depth=1,
                                        token_budget=30_000, **seed_args)
    packet = replace(packet, objective=packet.objective + '\n' + sections['SCOPE'])
    review_context = {'repository': {'name': sections['REPOSITORY']}}
    return {'key': key, 'data': data, 'sections': sections, 'diff': diff, 'packet': packet,
            'review_context': review_context, 'seed_evidence': seed_evidence, 'seed_id_map': id_map,
            'seed_snippet_index': snippet_index,
            'seed_blocks': [{k: v for k, v in b.items() if k != 'evidence'} for b in blocks]}


# ============================================================ seed ids as rendered, and head-only gap coverage
def _product_aliases(product, name: str) -> list:
    """Every loaded ``review_agent`` module that holds a name bound to the product function ``prompt_context.<name>``."""
    original = getattr(product.prompt_context, name)
    return [(module, attr) for module_name, module in list(sys.modules.items())
            if module_name.startswith('review_agent') and module is not None
            for attr, value in list(vars(module).items()) if value is original]


def rewrite_seed_items(items: list, inputs: dict) -> list:
    """Wrap the ORIGINAL selected ``PromptContextItem`` list: swap the product's range-only evidence id for the supplied
    snippet's snapshot-qualified id (item.evidence_id and the ``evidence_id="..."`` attribute in item.rendered).
    Selection, order, budget decisions and every other character of the rendering are the product's."""
    index, out = inputs['seed_snippet_index'], []
    for item in items:
        row = index.get(snippet_key(item.snippet))
        if row is None:
            raise AdapterError(f'prompt item {item.evidence_id} is not a supplied seed snippet')
        old, new = item.evidence_id, row['id']
        attr_old, attr_new = f'evidence_id="{old}"', f'evidence_id="{new}"'
        if item.rendered.count(attr_old) != 1:
            raise AdapterError(f'{old}: expected exactly one rendered id attribute')
        out.append(dataclasses.replace(item, evidence_id=new, rendered=item.rendered.replace(attr_old, attr_new, 1)))
    return out


_SEED_RENDER_LOCK = threading.Lock()


@contextlib.contextmanager
def seed_rendering(inputs: dict, product=None):
    """Scoped: ``select_prompt_context`` (prompt renderer, context-consumption accounting and any other alias) returns the
    snapshot-qualified seed ids. Used by every preparation and review path; restored on exit."""
    product = product or load_product()
    if not _SEED_RENDER_LOCK.acquire(blocking=False):
        raise AdapterError('seed rendering patch is already active in this process')
    original = product.prompt_context.select_prompt_context
    aliases = _product_aliases(product, 'select_prompt_context')

    def select(packet):
        return rewrite_seed_items(original(packet), inputs)
    select.__wrapped__ = original
    try:
        for module, attr in aliases:
            setattr(module, attr, select)
        yield aliases
    finally:
        for module, attr in aliases:
            setattr(module, attr, original)
        _SEED_RENDER_LOCK.release()


def head_only_packet(packet, inputs: dict):
    """Packet whose snippets are the HEAD seed snippets only. Handed to the real gap detectors (they check path/line
    ranges without a snapshot, so a base-revision snippet would count as read head content); the model prompt keeps both."""
    index = inputs['seed_snippet_index']
    return replace(packet, snippets=[s for s in packet.snippets if index[snippet_key(s)]['snapshot'] == 'head'])


RENDERED_SEED_ID = re.compile(r'<file evidence_id="(context:[^"]*)"')


def rendered_seed_ids(text: str) -> list:
    return RENDERED_SEED_ID.findall(text)


def check_rendered_seed_ids(inputs: dict, text: str) -> list:
    """Exact invariant: ids the prompt actually contains == core seed evidence ids, once each. Returns the rendered ids."""
    rendered, core = rendered_seed_ids(text), [e.id for e in inputs['seed_evidence']]
    if len(rendered) != len(set(rendered)) or sorted(rendered) != sorted(core):
        raise AdapterError(f'{inputs["key"]}: rendered seed ids differ from core seed ids: '
                           f'missing {sorted(set(core) - set(rendered))[:3]}, extra {sorted(set(rendered) - set(core))[:3]}, '
                           f'duplicates {len(rendered) - len(set(rendered))}')
    return rendered


DIFF_LINE_SIDES = {'add': {'head'}, 'delete': {'base'}, 'context': {'base', 'head'}}


def diff_core_evidence(diff, product=None) -> list:
    """Core Evidence for the ``diff:<path>`` ids the product renders (text, not counted in the source union)."""
    out = []
    for f in diff.files:
        if not f.path:
            continue
        lines = []
        sides = set()                           # revisions the parsed hunk lines actually show
        for hunk in f.hunks:
            lines.append(f'@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@ {hunk.section}')
            lines += [{'add': '+', 'delete': '-', 'context': ' '}[l.kind] + l.text for l in hunk.lines]
            for l in hunk.lines:
                sides |= DIFF_LINE_SIDES[l.kind]
        # an empty diff shows neither side: keep the legacy 'head' tag and claim nothing more
        represented = tuple(s for s in ('base', 'head') if s in sides) if sides else ('head',)
        out.append(oc.Evidence(f'diff:{f.path}', oc.snapshot_tag(represented), f.path,
                               text='\n'.join(lines) or '(empty diff)',
                               provenance='public_diff', content_kind='diff_hunk',
                               snapshots=represented if len(represented) > 1 or sides else ()))
    return out


def seed_agreement(inputs: dict, masked: MaskedSources) -> dict:
    """Every seed line must equal the whole-file masked workspace line (INTEGRATION_INPUT_NOTES.md)."""
    checked = mismatched = 0
    problems = []
    for ev in inputs['seed_evidence']:
        if ev.snapshot != 'head':
            continue
        lines = masked.lines(ev.file)
        for n, text in ev.lines:
            checked += 1
            if n > len(lines) or lines[n - 1] != text:
                mismatched += 1
                problems.append((ev.file, n))
    return {'head_lines_checked': checked, 'mismatched': mismatched, 'examples': problems[:5]}


# ============================================================ tool output normalisation
LSP_LABEL = re.compile(r'^lsp[:/]')


def is_native(label: str) -> bool:
    return bool(LSP_LABEL.match(label or ''))


def fit_listing(header: str, rows: list, cap: int) -> tuple:
    """Header + rows within ``cap`` reference tokens; trailing rows are dropped with an explicit marker."""
    kept = list(rows)
    while True:
        omitted = len(rows) - len(kept)
        text = '\n'.join([header, *kept] + ([f'... {omitted} more rows omitted (metadata cap {cap} tokens)'] if omitted else []))
        if ref_tokens(text) <= cap or not kept:
            return text, omitted
        kept.pop()


@dataclasses.dataclass
class Normalized:
    items: list                  # product ContextEvidence (raw ids) handed to the product loop
    core: list                   # core Evidence (final ids), parallel to items
    kinds: list                  # 'source' | 'metadata' per item
    notes: list                  # audit notes (excluded files, omitted rows ...)
    native: dict                 # {'native_items': n, 'total_items': n, 'backend': ...}


class QueryAccounting:
    """Counts every executed request (pre-gap included) and refuses to exceed the pilot's query caps."""

    def __init__(self, limits=LIMITS):
        self.limits, self.batches, self.total = limits, [], 0
        self.seen_keys: list = []

    def record(self, label: str, requests: list) -> None:
        if len(requests) > self.limits['queries_per_batch']:
            raise BudgetViolation(f'{label}: {len(requests)} requests > {self.limits["queries_per_batch"]} per batch')
        if self.total + len(requests) > self.limits['queries_total']:
            raise BudgetViolation(f'{label}: total would be {self.total + len(requests)} > {self.limits["queries_total"]}')
        self.total += len(requests)
        self.batches.append({'label': label, 'requests': [r.id for r in requests],
                             'operations': [r.operation for r in requests], 'count': len(requests),
                             'running_total': self.total})
        self.seen_keys.extend(r.dedup_key for r in requests)

    def summary(self) -> dict:
        return {'total': self.total, 'batches': self.batches, 'limits': {k: self.limits[k] for k in
                ('queries_total', 'queries_per_batch', 'rounds_after_initial')}}


def make_registry_class(product):
    base = product.registry.SafeToolRegistry
    ContextEvidence = product.models.ContextEvidence

    class PilotRegistry(base):
        """SafeToolRegistry whose results are normalised into the masked representation and budgeted.

        Differences from the product registry (all in this class): operations outside PILOT_OPERATIONS are
        denied; raw tool output is NOT forwarded - source text is re-derived from the whole-file
        ``mask_source`` result for exactly the lines the tool reported; the source union / metadata caps
        are enforced; one tool call may yield several evidence elements (positions vs source bodies).
        """

        def __init__(self, root, *, packet_id, account, masked, accounting, state, **kw):
            super().__init__(root, **kw)
            self.packet_id, self.account, self.masked = packet_id, account, masked
            self.accounting, self.state = accounting, state
            self.trace: list = []
            self.meta_used = 0            # metadata tokens rendered so far; the loop re-sends all admitted evidence every call
            self._pending_meta = 0        # tokens of metadata elements built for the request being normalised
            self.meta_log: list = []
            self.native_summary = {'native_evidence': 0, 'fallback_evidence': 0}

        # -- gate: unsupported operations never reach a tool
        def _dispatch(self, request):
            if request.operation not in PILOT_OPERATIONS:
                raise product.registry.ToolDenied(f'operation {request.operation} is not enabled in this pilot')
            return super()._dispatch(request)

        def execute(self, requests):
            label = self.state.batch_label()
            self.accounting.record(label, list(requests))
            batch = super().execute(requests)
            by_request: dict = {}
            for item in batch.evidence:
                by_request.setdefault(item.request_id, []).append(item)
            out_items, events = [], []
            for request, event in zip(requests, batch.events):
                raw = by_request.get(request.id, [])
                entry = {'batch': label, 'request': dataclasses.asdict(request), 'product_event': dataclasses.asdict(event),
                         'raw': [{'id': r.id, 'source': r.source, 'chars': len(r.content), 'truncated': r.truncated,
                                  'sha256': oc._sha(r.content), 'metadata': r.metadata,
                                  'content': r.content} for r in raw]}      # raw tool output, local audit only
                if event.status != 'completed':
                    entry['normalized'] = []
                    events.append(event)
                    self.trace.append(entry)
                    continue
                self._pending_meta = 0
                try:
                    norm = self._normalize(request, raw)
                except Exception as exc:       # fail closed: nothing from this request reaches the model
                    entry['normalized'], entry['error'] = [], f'{type(exc).__name__}: {exc}'
                    entry['normalization_failed'] = True
                    events.append(replace(event, status='failed', error=f'{type(exc).__name__}: {exc}'[:300], output_chars=0))
                    self.trace.append(entry)
                    continue
                shown, admissions = [], []
                for item, ev, kind in zip(norm.items, norm.core, norm.kinds):
                    admission = self._admit(request, item, ev, kind)
                    admissions.append(admission)
                    if admission['admitted']:
                        shown.append(item)
                entry['normalized'] = admissions
                entry['notes'], entry['native'] = norm.notes, norm.native
                if norm.core:
                    if not shown:
                        events.append(replace(event, status='denied', error='all evidence blocked by pilot budget/consistency',
                                              output_chars=0))
                    else:
                        events.append(replace(event, output_chars=sum(len(i.content) for i in shown)))
                else:
                    events.append(event)
                out_items.extend(shown)
                self.trace.append(entry)
            return product.registry.ToolExecutionBatch(evidence=out_items, events=events)

        # -- admission against the pilot budgets; registers evidence under the id the model will see
        def _admit(self, request, item, ev, kind) -> dict:
            final = ev.id
            row = {'raw_id': item.id, 'evidence_id': final, 'kind': ev.content_kind, 'provenance': ev.provenance,
                   'backend': ev.backend, 'file': ev.file, 'chars': len(item.content), 'native': ev.native,
                   'runtime_validity': ev.runtime_validity, 'content': item.content,
                   'content_sha256': oc._sha(item.content)}
            if kind == 'metadata':
                tokens = ref_tokens(item.content)
                cap = self.account.budgets.metadata_tokens_per_call
                before = self.meta_used
                ok = before + tokens <= cap
                row.update(metadata_tokens=tokens, metadata_cumulative_before=before,
                           metadata_cumulative_after=before + tokens if ok else before, metadata_cap=cap, admitted=ok,
                           blocked_by=None if ok else 'metadata_allocation_exhausted')
                self.meta_log.append({'request': request.id, 'evidence_id': final, 'tokens': tokens, 'admitted': ok,
                                      'cumulative': row['metadata_cumulative_after'], 'cap': cap})
                if ok:
                    self.meta_used += tokens
                    self.account.store.check([ev])
                    self.account.store.add(ev)
                    self._count_native(ev)
                return row
            runs = [list(r) for r in ev.runs()]
            result = self.account.admit_unit(oc.EvidenceUnit(final, (oc.UnitPart('source', ev),)))
            row.update(runs=runs, line_count=len(ev.lines), admitted=result.admitted, blocked_by=result.blocked_by,
                       marginal_source_tokens=result.marginal_tokens, detail=result.detail)
            if result.admitted:
                self._count_native(ev)
            return row

        def _count_native(self, ev):
            self.native_summary['native_evidence' if ev.native else 'fallback_evidence'] += 1

        # -- metadata allocation: every admitted metadata element stays in all later prompts, so the allocation is
        #    CUMULATIVE over the review (<= metadata_tokens_per_call in total, hence <= the cap at every call)
        def _meta_text(self, header, rows, stub):
            cap = self.account.budgets.metadata_tokens_per_call
            remaining = max(0, cap - self.meta_used - self._pending_meta)
            text, omitted = fit_listing(header, rows, remaining)      # whole rows only, explicit omission marker
            if ref_tokens(text) > remaining:
                text, omitted = f'{stub}: listing omitted, metadata allocation of {cap} tokens is used up ({len(rows)} rows)', len(rows)
            self._pending_meta += ref_tokens(text)
            return text, omitted

        @staticmethod
        def _json(item):
            """Parsed product JSON; truncated or malformed output is a failed lookup, never repaired or completed."""
            if item.truncated:
                raise ToolResultTruncated(f'{item.source}: product output was cut at its character limit; '
                                          'no field of this lookup is provided and nothing was re-fetched')
            try:
                return json.loads(item.content)
            except json.JSONDecodeError as exc:
                raise ToolResultTruncated(f'{item.source}: tool output is not valid JSON ({exc.msg} at char {exc.pos})') from exc

        # -- per operation normalisation
        def _ids(self, request, suffix=''):
            raw = f'evidence-{request.id}{suffix}'
            return raw, f'agentic:{self.packet_id}:{raw}'

        def _normalize(self, request, raw) -> Normalized:
            op = request.operation
            handler = {'read_lines': self._n_read_lines, 'grep_literal': self._n_grep,
                       'related_tests': self._n_related_tests, 'go_to_definition': self._n_definition,
                       'find_references': self._n_listing, 'find_implementations': self._n_listing,
                       'incoming_calls': self._n_listing, 'outgoing_calls': self._n_listing,
                       'document_symbols': self._n_symbols, 'symbol_search': self._n_symbols}[op]
            return handler(request, raw)

        def _source_item(self, request, suffix, source, file, lines, provenance, backend, text_prefix=''):
            raw_id, final = self._ids(request, suffix)
            ev = oc.Evidence(final, 'head', file, lines=tuple(sorted(lines.items())), provenance=provenance,
                             backend=backend)
            content = text_prefix + render_lines(lines)
            return (ContextEvidence(id=raw_id, request_id=request.id, operation=request.operation, source=source,
                                    content=content, truncated=False, metadata={'normalized': 'mask_source'}), ev)

        def _n_read_lines(self, request, raw):
            item = raw[0]
            file, rng = item.source.rsplit(':', 1)
            start, end = (int(x) for x in rng.split('-'))
            lines = self.masked.range(file, start, end)
            if not lines:
                return Normalized([], [], [], [f'{file}:{start}-{end} is empty'], {})
            it, ev = self._source_item(request, '', f'{file}:{min(lines)}-{max(lines)}', file, lines, 'read_lines', None)
            it = replace(it, metadata={**it.metadata, 'start': min(lines), 'end': max(lines)})
            return Normalized([it], [ev], ['source'], [], {})

        def _n_grep(self, request, raw):
            item = raw[0]
            query = item.metadata['query']
            per_file: dict = {}
            dropped_masked, excluded_files = 0, {}
            for row in item.content.splitlines():
                if row == '(no matches)':
                    continue
                m = re.match(r'^(.*?):(\d+): ', row)
                if not m:
                    continue
                file, number = m.group(1), int(m.group(2))
                try:
                    text = self.masked.lines(file)[number - 1]
                except MaskError as exc:
                    excluded_files[file] = str(exc)[:160]
                    continue
                if query not in text:
                    dropped_masked += 1          # the match was inside a comment/docstring the seed convention masks
                    continue
                per_file.setdefault(file, {})[number] = text
            items, core = [], []
            for k, (file, lines) in enumerate(sorted(per_file.items())):
                it, ev = self._source_item(request, f'-g{k + 1}', f'grep:{query}', file, lines, 'grep_literal', None)
                items.append(it)
                core.append(ev)
            notes = [f'grep matches dropped because masked text no longer contains the query: {dropped_masked}',
                     f'files excluded (not maskable): {excluded_files}',
                     f'product reported hit_limit={item.truncated}']
            if not items:
                raw_id, final = self._ids(request, '')
                text = f'grep {query!r}: no visible matches ({dropped_masked} masked-only, {len(excluded_files)} files not provided)'
                meta = ContextEvidence(id=raw_id, request_id=request.id, operation=request.operation,
                                       source=f'grep:{query}', content=text, metadata={})
                ev = oc.Evidence(final, 'head', request.path or 'workspace', text=text, provenance='grep_literal',
                                 content_kind='member_listing')
                return Normalized([meta], [ev], ['metadata'], notes, {})
            return Normalized(items, core, ['source'] * len(items), notes, {})

        def _n_related_tests(self, request, raw):
            item = raw[0]
            _, rel = self._safe_file(request.path)
            found = product.registry.related_test_candidates(rel, self._test_index)      # the product's own mapper
            payload = {'source_path': rel, 'candidate_count': len(found),
                       'candidates': [{'path': self._safe_file(c)[1]} for c in found[:4]]}
            items, core, kinds, notes = [], [], [], []
            names = [c['path'] for c in payload['candidates']]
            for k, cand in enumerate(payload['candidates']):
                file = cand['path']
                total = len((self.root / file).read_text(encoding='utf-8', errors='replace').splitlines())
                count = min(total, self.config.agentic_read_max_lines)       # same excerpt rule as the product tool
                try:
                    lines = self.masked.range(file, 1, count)
                except MaskError as exc:
                    notes.append(f'{file}: {exc}')
                    continue
                it, ev = self._source_item(request, f'-t{k + 1}', file, file, lines, 'related_tests', None)
                items.append(it)
                core.append(ev)
                kinds.append('source')
            raw_id, final = self._ids(request)
            listing = (f'related_tests for {payload["source_path"]}: candidate_count={payload["candidate_count"]}; '
                       f'excerpts_considered={names}; excerpts_provided={[i.source for i in items]}')
            text, omitted = self._meta_text(listing, [], f'related_tests for {payload["source_path"]}')
            items.insert(0, ContextEvidence(id=raw_id, request_id=request.id, operation=request.operation,
                                            source=payload['source_path'], content=text, metadata={}))
            core.insert(0, oc.Evidence(final, 'head', payload['source_path'], text=text, provenance='related_tests',
                                       content_kind='member_listing'))
            kinds.insert(0, 'metadata')
            return Normalized(items, core, kinds, notes, {})

        def _locations_of(self, request, payload):
            return payload

        def _n_definition(self, request, raw):
            item = raw[0]
            payload = self._json(item)
            sources = item.metadata.get('resolution_sources') or []
            lsp_items = payload.get('lsp_locations', [])
            native = bool(lsp_items) and any(is_native(s) for s in sources)
            backend = next((s for s in sources if is_native(s)), 'tree-sitter') if native else 'tree-sitter'
            rows = [f'definition {d["name"]} ({d["kind"]}) {d["path"]}:{d["line_start"]}-{d["line_end"]} '
                    f'visibility={d["visibility"]} [{d["source"]}]' for d in payload.get('definitions', [])]
            rows += [f'lsp_location {l["path"]}:{l["line_start"]}-{l["line_end"]} col {l["column_start"]}-{l["column_end"]} '
                     f'[{l["source"]}]' for l in lsp_items]
            header = (f'go_to_definition {item.source.split(":", 1)[1]}: positions only (no code body in this element); '
                      f'resolution_sources={sources}; fallback_used={item.metadata.get("fallback_used")}')
            text, omitted = self._meta_text(header, rows, f'go_to_definition {item.source.split(":", 1)[1]}')
            raw_id, final = self._ids(request)
            first = (payload.get('definitions') or lsp_items or [{}])[0]
            ev = oc.Evidence(final, 'head', first.get('path') or request.path or 'workspace', text=text,
                             provenance='native_lsp' if native else 'ast_fallback', content_kind='definition_location',
                             backend=backend, anchor_line=first.get('line_start'))
            items = [ContextEvidence(id=raw_id, request_id=request.id, operation=request.operation, source=item.source,
                                     content=text, metadata={'resolution_sources': sources})]
            core, kinds, notes = [ev], ['metadata'], [f'listing rows omitted: {omitted}']
            for k, decl in enumerate(payload.get('declarations', [])):
                try:
                    lines = self.masked.range(decl['path'], decl['line_start'], decl['line_end'])
                except MaskError as exc:
                    notes.append(f'declaration body {decl["path"]}: {exc}')
                    continue
                if not lines:
                    continue
                it, dev = self._source_item(request, f'-d{k + 1}', f'declaration:{decl["path"]}:{decl["line_start"]}-{decl["line_end"]}',
                                            decl['path'], lines, 'ast_fallback', 'tree-sitter')
                items.append(it)
                core.append(dev)
                kinds.append('source')
            return Normalized(items, core, kinds, notes, {'native_items': len(lsp_items), 'total_items': len(lsp_items) + len(payload.get('definitions', [])),
                                                         'backend': backend})

        def _n_listing(self, request, raw):
            items, core, kinds, notes = [], [], [], []
            native_items = total = 0
            for k, item in enumerate(raw):
                rows_in = self._json(item)
                rows, anchor_file, anchor_line = [], None, None
                sources = item.metadata.get('resolution_sources') or []
                for r in rows_in:
                    line = r.get('line_start') or r.get('line') or 0
                    tags = r.get('sources') or [r.get('source', '')]
                    total += 1
                    native_items += any(is_native(t) for t in tags)
                    if anchor_file is None:
                        anchor_file, anchor_line = r.get('path'), line
                    extra = f' -> {r["target"]}' if r.get('target') else ''
                    rows.append(f'{r.get("path")}:{line}{extra} [{",".join(t for t in tags if t)}]'
                                + (' ambiguous' if r.get('ambiguity') else ''))
                header = (f'{request.operation} {item.source}: reference positions only (no code in this element); '
                          f'resolution_sources={sources}; fallback_used={item.metadata.get("fallback_used")}; '
                          f'ambiguity_reasons={item.metadata.get("ambiguity_reasons")}; rows={len(rows_in)}')
                text, omitted = self._meta_text(header, rows, f'{request.operation} {item.source}')
                native = any(is_native(s) for s in sources)
                raw_id, final = self._ids(request, '' if k == 0 else f'-{k + 1}')
                ev = oc.Evidence(final, 'head', anchor_file or request.path or 'workspace', text=text,
                                 provenance='native_lsp' if native else 'ast_fallback', content_kind='reference_edge',
                                 backend=next((s for s in sources if is_native(s)), 'tree-sitter'), anchor_line=anchor_line or None)
                items.append(ContextEvidence(id=raw_id, request_id=request.id, operation=request.operation,
                                             source=item.source, content=text, metadata={'resolution_sources': sources}))
                core.append(ev)
                kinds.append('metadata')
                notes.append(f'{item.source}: rows omitted by metadata cap: {omitted}')
            return Normalized(items, core, kinds, notes, {'native_items': native_items, 'total_items': total})

        def _n_symbols(self, request, raw):
            item = raw[0]
            rows_in = self._json(item)
            sources = item.metadata.get('resolution_sources') or []
            rows = [f'{r.get("name")} ({r.get("kind")}) {r.get("path")}:{r.get("line_start")}-{r.get("line_end")} [{r.get("source")}]'
                    for r in rows_in]
            header = f'{request.operation} {item.source}: symbol positions only; resolution_sources={sources}; rows={len(rows_in)}'
            text, omitted = self._meta_text(header, rows, f'{request.operation} {item.source}')
            native = any(is_native(s) for s in sources)
            raw_id, final = self._ids(request)
            first = rows_in[0] if rows_in else {}
            ev = oc.Evidence(final, 'head', first.get('path') or request.path or 'workspace', text=text,
                             provenance='native_lsp' if native else 'ast_fallback', content_kind='ast_outline',
                             backend=next((s for s in sources if is_native(s)), 'tree-sitter'),
                             anchor_line=first.get('line_start'))
            return Normalized([ContextEvidence(id=raw_id, request_id=request.id, operation=request.operation,
                                               source=item.source, content=text, metadata={})], [ev], ['metadata'],
                              [f'rows omitted by metadata cap: {omitted}'], {})

    return PilotRegistry


# ============================================================ output schema + prompt contract
def _obj(props: dict) -> dict:
    return {'type': 'object', 'additionalProperties': False, 'required': list(props), 'properties': props}


def _arr(item) -> dict:
    return {'type': 'array', 'items': item}


S = {'type': 'string'}
I = {'type': 'integer'}
B = {'type': 'boolean'}
NI = {'type': ['integer', 'null']}
NS = {'type': ['string', 'null']}
STRS = _arr(S)
OPERATIONS = ['read_lines', 'grep_literal', 'related_tests', 'go_to_definition', 'find_references',
              'find_implementations', 'incoming_calls', 'outgoing_calls', 'document_symbols', 'symbol_search']
_EVIDENCE = _obj({'evidence_id': S, 'path': S, 'lines': S, 'note': S})
def _enum(values, nullable=False) -> dict:
    return {'type': ['string', 'null'] if nullable else 'string', 'enum': [*values, None] if nullable else list(values)}


_OBLIGATION = _obj({'id': S, 'predicate': S, 'required': B, 'requires': _enum(oc.REQUIRES),
                    'fact_kind': _enum(oc.FACT_KINDS, True), 'snapshot': _enum(['head', 'base'], True),
                    'status': _enum(oc.STATUSES), 'evidence_ids': STRS, 'depends_on': STRS})
_CHANGE = _obj({'kind': _enum(oc.CHANGE_KINDS), 'affected_ids': STRS, 'reason': S, 'basis': S,
                'evidence_ids': STRS, 'claim_effect_if_true': S, 'claim_effect_if_false': S})
_CANDIDATE = _obj({
    'id': S, 'claim': S, 'scope': S, 'origin': S, 'origin_evidence_ids': STRS,
    'obligations': _arr(_OBLIGATION),
    'routes': _arr(_obj({'id': S, 'obligation_ids': STRS})),
    'challenges': _arr(_obj({'id': S, 'text': S, 'material': B, 'state': _enum(oc.CHALLENGE_STATES),
                             'evidence_ids': STRS, 'target': NS})),
    'unresolved': _arr(_obj({'id': S, 'text': S})),
    'remove_obligations': STRS, 'remove_routes': STRS, 'resolve_unresolved': STRS, 'withdraw': B,
    'changes': _arr(_CHANGE)})
RESPONSE_SCHEMA = _obj({
    'review': _obj({
        'summary': S, 'reviewed_areas': STRS,
        'observations': _arr(_obj({
            'title': S, 'classification': _enum(['finding', 'operational_check', 'reference']),
            'category': S, 'severity': _enum(['info', 'minor', 'major', 'critical']), 'confidence': I,
            'paths': STRS, 'evidence': _arr(_EVIDENCE), 'body': S, 'recommendation': S})),
        'context_assessment': _obj({
            'cross_file_conclusion': _enum(['impact_found', 'no_additional_impact', 'insufficient_context']),
            'used_context': _arr(_obj({'evidence_id': S, 'conclusion': S})),
            'dismissed_context': _arr(_obj({'evidence_id': S, 'reason': S}))})}),
    'findings': _arr(_obj({
        'title': S, 'severity': _enum(['minor', 'major', 'critical']), 'confidence': I, 'introduced_by_mr': B,
        'category': S, 'path': S, 'line_start': I, 'line_end': I, 'candidate_id': S,
        'trigger': S, 'mechanism': S, 'impact': S, 'evidence': _arr(_EVIDENCE), 'why_it_matters': S})),
    'context_requests': _arr(_obj({
        'id': S, 'operation': _enum(OPERATIONS), 'path': S, 'query': S, 'start': NI, 'end': NI, 'reason': S,
        'candidate_id': S, 'obligation_id': S, 'challenge_id': S})),
    'candidates': _arr(_CANDIDATE)})

COMMON_CONTRACT = f"""<oar_response_contract>
This block is identical for every experiment condition and overrides the product prompt where they differ.
- Budget for additional read-only lookups (shared by all conditions): at most {LIMITS['queries_total']} in total, at most {LIMITS['queries_per_batch']} per round, at most {LIMITS['rounds_after_initial']} rounds after this response. Some lookups were already run automatically before your first response and count against the total. Return at most {LIMITS['queries_per_batch']} `context_requests` (not 4). Allowed operations: {', '.join(OPERATIONS)}.
- Source text is shown with comments and docstrings blanked; only the line ranges that appear in this prompt have been supplied. Position-only lookup results (definitions, references, symbols) show where something is, not what the code says.
- Cite only evidence ids that appear in this prompt (`context:...`, `diff:...`, `agentic:...`). Never invent ids.
- `candidates` lists at most 3 defect hypotheses you are currently considering, with at most 5 obligations, 3 routes, 3 challenges and 3 unresolved notes each. A candidate has: `claim` (the defect), `scope`, `origin`, `origin_evidence_ids`; `obligations` = facts that must hold for the claim (`requires`: name = a name/position suffices, value = the value or expression must be seen, body = the code body must be seen, runtime = an executed observation, which this experiment never supplies; `status` is your own assessment: unknown|supported|contradicted|conflicted; `evidence_ids` are the ids you rely on); `routes` = alternative sets of obligations that each would suffice for the claim; `challenges` = concrete counter-arguments; `unresolved` = open facts. Use empty lists for what does not apply and `changes: []` unless you changed an existing candidate.
- Every item in `findings` must give the exact location (`path`, `line_start`, `line_end`), `candidate_id`, `trigger` (the concrete input/state that makes it fail), `mechanism` (the code path that produces the failure), `impact` (the observable wrong behaviour), the `evidence` ids it relies on, and the usual fields. A finding that restates a hypothesis without trigger, mechanism and impact must be left out. If you cannot support a defect, return no findings.
- `context_requests` entries may also name the `candidate_id`/`obligation_id`/`challenge_id` they are meant to settle (empty string if none).
- Fields of the product prompt's JSON shape that the output schema does not contain (for example `claim_scope`, `practice_basis`, `suggested_fix`, `verification`) are not produced; the output schema wins.
- Return the single JSON object required by the output schema; nothing outside it.
</oar_response_contract>"""

LEDGER_RULES = f"""<oar_state_rules>
Your candidate state is kept and checked between responses (limits: 3 candidates; 5 obligations, 3 routes, 3 challenges, 3 notes per candidate).
- The state below is what has been accepted so far. Candidates, obligations, routes, challenges and notes you do not mention are kept unchanged; omission never withdraws or resolves anything.
- Every status other than `unknown` must cite evidence ids; ids must exist and any id you newly cite must be present in the current prompt. Source-bearing obligations (`requires` = value or body) need source lines, not position-only results.
- Any change to an existing claim, scope, obligation (added, removed, required, status), route, challenge or note needs an entry in that candidate's `changes` with `kind`, `affected_ids`, a `reason`, and where applicable `basis` (interpretation_error | new_premise | evidence_invalidated | not_necessary_for_claim | scope_revised | contradicted | superseded | refuted_in_scope | duplicate | irrelevant_to_claim), `evidence_ids`, `claim_effect_if_true`, `claim_effect_if_false`. Correcting an earlier misreading needs no new source: use basis interpretation_error and say what was misread.
- A route is dead only if one of its required obligations is contradicted. An alternative route that remains open keeps the candidate alive. Unrelated unknowns do not remove support that is already established.
- An update that fails these checks is rejected as a whole, the previous state stays in force, and the reasons are shown to you next time.
- Reportability: a finding in `findings` is reportable only if its `candidate_id` names a candidate in the state whose verdict is `supported` after your update in that same response, i.e. at least one route whose every required obligation is `supported` (dependencies included) and which no pending material challenge targets (a challenge without target counts against every route), and every evidence id it cites appears in this prompt. A finding that does not meet this is recorded as non-reportable and the review ends as an invalid final report. If you cannot reach it, return no findings.
</oar_state_rules>"""


def clean_candidates(payload: dict) -> dict:
    """Drop empty optional strings the strict output schema forced the model to emit (keys the core treats as absent)."""
    out = copy.deepcopy({'candidates': payload.get('candidates', [])})
    for cand in out['candidates']:
        for change in cand.get('changes', []):
            for key in ('basis', 'claim_effect_if_true', 'claim_effect_if_false'):
                if change.get(key) in ('', None):
                    change.pop(key, None)
        for ch in cand.get('challenges', []):
            if ch.get('target') in ('', None):
                ch['target'] = None
    return out


DIGEST_PRIORITY = ('exact duplicates (candidates by id, findings by content)',
                   'context_requests_in_that_response (administrative: their results are already in the prompt)',
                   'candidates no finding refers to, last first',
                   'candidates a finding refers to, last first (the finding itself stays)',
                   'findings, last first (blocked state)')


def digest_previous(payload: dict, cap: int) -> tuple:
    """P/A: the model's own previous candidates + findings (verbatim fields), trimmed whole-item-wise to ``cap`` tokens.

    Neutral loss order (DIGEST_PRIORITY): reported findings, with the candidate that supports them, survive longest; the
    previous trigger/mechanism/impact is never dropped before unlinked hypotheses or administrative material. Every loss
    is counted in ``omitted``; ``omitted['findings'] > 0`` or ``blocked`` means even the findings alone did not fit.
    No ledger transition rule is applied: P/A keep free-form state.
    """
    candidates, seen_ids = [], set()
    for c in payload.get('candidates', []):
        if c.get('id') in seen_ids:
            continue
        seen_ids.add(c.get('id'))
        candidates.append({k: c[k] for k in ('id', 'claim', 'scope', 'origin_evidence_ids', 'obligations', 'routes',
                                             'challenges', 'unresolved') if k in c})
    findings, seen_findings = [], set()
    for f in payload.get('findings', []):
        row = {k: f.get(k) for k in ('title', 'path', 'line_start', 'line_end', 'candidate_id', 'trigger', 'mechanism',
                                     'impact', 'evidence')}
        sig = oc._sha(row)
        if sig not in seen_findings:
            seen_findings.add(sig)
            findings.append(row)
    requests = [{k: r.get(k) for k in ('operation', 'path', 'query', 'start', 'end', 'candidate_id')}
                for r in payload.get('context_requests', [])]
    duplicates = (len(payload.get('candidates', [])) - len(candidates)) + (len(payload.get('findings', [])) - len(findings))
    parts = {'candidates': candidates, 'findings': findings, 'context_requests_in_that_response': requests}
    omitted = {'candidates': 0, 'findings': 0, 'context_requests_in_that_response': 0, 'duplicates_removed': duplicates}

    def render():
        return json.dumps({**parts, 'omitted_items': omitted}, ensure_ascii=False, separators=(',', ':'))

    def drop_candidate(linked: bool) -> bool:
        cited = {f.get('candidate_id') for f in parts['findings']}
        for i in range(len(parts['candidates']) - 1, -1, -1):
            if (parts['candidates'][i].get('id') in cited) == linked:
                del parts['candidates'][i]
                omitted['candidates'] += 1
                return True
        return False
    def drop_last(name: str) -> bool:
        if not parts[name]:
            return False
        parts[name].pop()
        omitted[name] += 1
        return True
    steps = (lambda: drop_last('context_requests_in_that_response'), lambda: drop_candidate(False),
             lambda: drop_candidate(True), lambda: drop_last('findings'))
    while True:
        text = render()
        tokens = ref_tokens(text)
        if tokens <= cap:
            return text, tokens, omitted
        if not any(step() for step in steps):
            return text, tokens, omitted


# ============================================================ per-review policy state
class ReviewState:
    """Everything the patched product hooks and the wrapped client share for one review."""

    def __init__(self, *, arm, inputs, account, masked, accounting, client_inner, product):
        self.arm = arm
        self.use_a, self.use_r = ARMS[arm]
        self.inputs, self.account, self.masked = inputs, account, masked
        self.accounting, self.inner, self.product = accounting, client_inner, product
        self.store = account.store
        self.ledger = oc.Ledger(self.store, oc.StateCaps(**STATE_CAPS)) if self.use_r else None
        self.calls = 0
        self.stage_records: list = []
        self.pending_requests: list = []
        self.pending_extras: dict = {}
        self.schedule_trace: list = []
        self.last_update: Optional[oc.UpdateResult] = None
        self.last_errors: list = []
        self.last_payload: Optional[dict] = None
        self.prev_payload: Optional[dict] = None
        self.review_context = None
        self.payloads: list = []
        self.refused_retries: list = []
        self.product_calls: dict = {}
        self.provided_ids: list = []
        self.state_block: dict = {}
        self.registry = None
        self.rendered_seed_ids: list = []

    def batch_label(self) -> str:
        return 'pre_gap' if self.calls == 0 else f'round_{self.calls}'

    def count(self, name: str) -> None:
        self.product_calls[name] = self.product_calls.get(name, 0) + 1

    # -- prompt additions
    def budget_line(self) -> str:
        """Exact lookup allowance at this call; identical rule and wording for every condition."""
        used, rounds_left = self.accounting.total, LIMITS['rounds_after_initial'] - (self.calls - 1)
        allowed = max(0, min(LIMITS['queries_per_batch'], LIMITS['queries_total'] - used)) if rounds_left > 0 else 0
        final = ' This is your final response: no further lookup can run, so answer from the evidence you have.' if allowed == 0 else ''
        return (f'<lookup_budget>lookups already run (automatic ones included): {used} of {LIMITS["queries_total"]}; '
                f'rounds left after this response: {max(0, rounds_left)}; lookups that can still run after this response: '
                f'{allowed}. A response asking for more than {allowed} lookups may have the whole batch skipped by the '
                f'review runner when the total would be exceeded.{final}</lookup_budget>')

    def suffix_extra(self) -> str:
        stage = self.calls - 1
        parts = ['\n' + COMMON_CONTRACT, self.budget_line()]
        if self.use_r:
            parts.append(LEDGER_RULES)
            errors = ''
            if self.last_update is not None and not self.last_update.accepted:
                errors = json.dumps(self.last_update.errors[:6], ensure_ascii=False, separators=(',', ':'))
                if ref_tokens(errors) > LIMITS['error_tokens_in_state_block']:
                    errors = errors[:700] + ' ...[errors truncated to the 200-token block cap]'
            cap = LIMITS['state_tokens_per_call'] - (ref_tokens(errors) if errors else 0)
            render = self.ledger.render_state(cap)
            if render.blocked:
                raise StateBlocked(f'ledger state needs {render.tokens} tokens > cap {render.cap}')
            self.account.check_state_tokens(render.tokens + (ref_tokens(errors) if errors else 0), f'stage_{stage}')
            self.state_block = {'kind': 'ledger', 'tokens': render.tokens, 'error_tokens': ref_tokens(errors) if errors else 0,
                                'tier': render.tier, 'compacted_fields': render.compacted_fields,
                                'state_sha256': self.ledger.state_hash()}
            if stage > 0:
                parts.append('<review_state>\n' + (f'last_update_rejected={errors}\n' if errors else '')
                             + (render.text or '[]') + '\n</review_state>')
        elif self.prev_payload is not None and stage > 0:
            text, tokens, omitted = digest_previous(self.prev_payload, LIMITS['state_tokens_per_call'])
            if not self.account.check_state_tokens(tokens, f'stage_{stage}'):
                raise StateBlocked(f'previous-response digest needs {tokens} tokens > cap {LIMITS["state_tokens_per_call"]}')
            self.state_block = {'kind': 'previous_response_digest', 'tokens': tokens, 'omitted': omitted}
            parts.append('<previous_response>\n' + text + '\n</previous_response>')
        else:
            self.state_block = {'kind': 'none'}
        return '\n'.join(parts) + '\n'

    # -- request conversion for ranking (A/AR)
    def core_request(self, req, extras: dict) -> dict:
        op = req.operation
        tool = {'read_lines': 'read_lines', 'grep_literal': 'grep_literal', 'related_tests': 'related_tests',
                'go_to_definition': 'definition', 'find_references': 'references', 'find_implementations': 'implementations',
                'incoming_calls': 'calls', 'outgoing_calls': 'calls', 'document_symbols': 'ast_index',
                'symbol_search': 'ast_index'}[op]
        args = {}
        if op == 'read_lines':
            start = req.start or 1
            args = {'file': req.path, 'start_line': start, 'end_line': req.end or start + 199}
        elif op in ('related_tests', 'document_symbols'):
            args = {'file': req.path}
        else:
            args = {'query': req.query, 'file': req.path}
        link = extras.get(req.dedup_key, {})
        return {'id': req.id, 'tool': tool, 'args': args, 'snapshot': 'head',
                'purpose': 'model_request' if req.origin != 'gap-detector' else 'peripheral', **link}

    def rank_for_a(self, candidates: list, label: str) -> list:
        """Order ALL candidate requests (before the loop's slice) by current obligations/challenges, else keep order."""
        seen = set(self.accounting.seen_keys)
        pool, skipped = [], []
        for req in candidates:
            if req.dedup_key in seen or any(req.dedup_key == p.dedup_key for p in pool):
                skipped.append({'id': req.id, 'reason': 'already_executed_or_duplicate'})
            else:
                pool.append(req)
        ledger = self.ledger
        basis, fallback_errors = 'persistent_ledger', None
        if ledger is None:
            ledger = oc.Ledger(self.store, oc.StateCaps(**STATE_CAPS))
            payload = clean_candidates(self.last_payload or {})
            result = ledger.apply_update(payload, round_index=max(0, self.calls - 1),
                                         provided_evidence_ids=list(self.store.by_id))
            basis = 'scratch_ledger_from_current_response'
            if not result.accepted:
                # candidates did not parse: rank against an EMPTY ledger. Nothing is linked, so the product order is
                # kept, but the core's skip decisions (already read / duplicate) still apply to every request.
                ledger = oc.Ledger(self.store, oc.StateCaps(**STATE_CAPS))
                basis, fallback_errors = 'empty_ledger_candidates_did_not_parse', result.errors[:6]
        mapped = {r.id: r for r in pool}
        ranking = oc.rank_requests(ledger, [self.core_request(r, self.pending_extras) for r in pool],
                                   limit=len(pool), store=self.store)
        # Requests tied to a pending counter-argument or a required open obligation (tiers 0/1) move to the front in
        # the core's order. Everything else keeps the product's own order: A never reshuffles unlinked requests.
        linked = [mapped[row['id']] for row in ranking['selected'] if row['tier'] <= 1]
        chosen = {row['id'] for row in ranking['selected']}
        ordered = linked + [r for r in pool if r.id in chosen and r.id not in {x.id for x in linked}]
        # A/AR pack the batch to the remaining total allowance. The product loop would instead discard the WHOLE batch
        # when it overflows the total (P/R keep that original behaviour).
        allowance = max(0, min(LIMITS['queries_per_batch'], LIMITS['queries_total'] - self.accounting.total))
        cut = [r.id for r in ordered[allowance:]]
        ordered = ordered[:allowance]
        self.schedule_trace.append({
            'batch_after_call': self.calls, 'policy': 'A', 'basis': basis, 'tier_counts': ranking['tier_counts'],
            'input_order': [r.id for r in candidates], 'ranked_order': [r.id for r in ordered],
            'ranked_tiers': [{'id': row['id'], 'tier': row['tier_name'], 'notes': row['notes']} for row in ranking['selected']],
            'linked_moved_to_front': [r.id for r in linked], 'cut_to_remaining_allowance': cut,
            'remaining_allowance': allowance,
            'skipped': skipped + [{'id': row['request']['id'], 'reason': row['reason']} for row in ranking['skipped']],
            'scratch_ledger_errors': fallback_errors, 'loop_slice_after_ranking': self.product_limit()})
        return ordered

    def product_limit(self) -> int:
        return LIMITS['queries_per_batch']


# ============================================================ scoped monkeypatching
_PATCH_LOCK = threading.Lock()

PATCHES = [
    ('review_agent.agents.orchestrator.run_llm_review_result',
     'wrapped: begins the transport stage, then calls the REAL product function (prompt renderer, JSON parser, request parser); '
     'afterwards applies the R/AR ledger update and relabels request ids; for A/AR hands the model requests to the declaration hook'),
    ('review_agent.agents.orchestrator.detect_declaration_evidence_gaps',
     'P/R: real function (head-only packet), ids relabelled r<round>-. A/AR: real function + real observation detector + the model requests are '
     'ranked together BEFORE the loop slices to 3'),
    ('review_agent.agents.orchestrator.detect_observation_path_gaps',
     'P/R: real function, ids relabelled. A/AR: returns [] (its output was merged into the ranked list above)'),
    ('review_agent.agents.llm_reviewer.needs_context_reflection',
     'returns False: the product reflection call would be an extra model call (cap = 3 calls per review)'),
    ('review_agent.agents.orchestrator.detect_public_reference_gaps / detect_related_test_gaps / detect_terraform_graph_gaps',
     'counting wrappers; the real function receives the packet with HEAD seed snippets only (base source is not head coverage)'),
    ('every review_agent alias of prompt_context.select_prompt_context (prompt_context, llm_reviewer, context_consumption)',
     'seed_rendering(): wraps the original selected PromptContextItem list; evidence_id / rendered id attribute become the '
     'snapshot-qualified seed id (context:<key>:<path>:<s>-<e>@base|head); nothing else in the item changes'),
    ('review_agent.agents.llm_reviewer._context_requests_from_payload / _parse_json / _findings_from_payload',
     'counting wrappers only (behaviour unchanged)'),
]


@contextlib.contextmanager
def scoped_patches(state: ReviewState, product=None):
    """Per-process monkeypatch of product module attributes; restored on exit; never concurrent."""
    product = product or load_product()
    if not _PATCH_LOCK.acquire(blocking=False):
        raise AdapterError('scoped product patches are already active in this process (no concurrent reviews per process)')
    orch, llm, gap = product.orchestrator, product.llm_reviewer, product.gap_detector
    originals = {
        (orch, 'run_llm_review_result'): orch.run_llm_review_result,
        (orch, 'detect_declaration_evidence_gaps'): orch.detect_declaration_evidence_gaps,
        (orch, 'detect_observation_path_gaps'): orch.detect_observation_path_gaps,
        (orch, 'detect_public_reference_gaps'): orch.detect_public_reference_gaps,
        (orch, 'detect_related_test_gaps'): orch.detect_related_test_gaps,
        (orch, 'detect_terraform_graph_gaps'): orch.detect_terraform_graph_gaps,
        (llm, 'needs_context_reflection'): llm.needs_context_reflection,
        (llm, '_context_requests_from_payload'): llm._context_requests_from_payload,
        (llm, '_parse_json'): llm._parse_json,
        (llm, '_findings_from_payload'): llm._findings_from_payload,
    }
    real_run = originals[(orch, 'run_llm_review_result')]
    real_decl = originals[(orch, 'detect_declaration_evidence_gaps')]
    real_obs = originals[(orch, 'detect_observation_path_gaps')]

    def relabel(requests, prefix):
        seen, out = set(), []
        for i, r in enumerate(requests):
            new = f'{prefix}-{r.id}'
            if new in seen:
                new = f'{new}-{i + 1}'
            seen.add(new)
            out.append(replace(r, id=new))
        return out

    def head(packet):
        return head_only_packet(packet, state.inputs)

    def counting(name, fn, packet_arg=None):
        """Counting wrapper; with ``packet_arg`` the real detector is handed the head-only packet (see head_only_packet)."""
        def wrapper(*args, **kwargs):
            state.count(name)
            if packet_arg is not None:
                args = list(args)
                args[packet_arg] = head(args[packet_arg])
            return fn(*args, **kwargs)
        wrapper.__wrapped__ = fn
        return wrapper

    def run_llm(diff, packet, client, additional_evidence=None, review_context=None, system_prompt=None,
                sdk_workflow_context=None):
        state.count('run_llm_review_result')
        stage = STAGES[state.calls]                       # IndexError would mean a 4th call: impossible by cap
        state.inner.begin_stage(stage)
        state.calls += 1
        state.provided_ids = [*state.inputs['provided_base_ids'], *[e.id for e in (additional_evidence or [])]]
        state.prev_payload, state.last_payload = state.last_payload, None
        result = real_run(diff, packet, client, additional_evidence=additional_evidence, review_context=review_context,
                          system_prompt=system_prompt, sdk_workflow_context=sdk_workflow_context)
        state.review_context = review_context
        payload = state.last_payload
        if state.use_r and payload is not None:
            update = state.ledger.apply_update(clean_candidates(payload), round_index=state.calls - 1,
                                               provided_evidence_ids=state.provided_ids)
            state.last_update = update
            state.last_errors = update.errors
        requests = relabel(result.context_requests, f'r{state.calls}')
        extras = {}
        raw_requests = (payload or {}).get('context_requests', []) if isinstance(payload, dict) else []
        for raw in raw_requests:
            key = (raw.get('operation'), (raw.get('path') or '').strip() or None, (raw.get('query') or '').strip() or None,
                   raw.get('start'), raw.get('end'))
            link = {k: raw.get(k) for k in ('candidate_id', 'obligation_id', 'challenge_id') if raw.get(k)}
            if link:
                extras[key] = link
        state.pending_extras = extras
        state.pending_requests = requests
        state.stage_records.append({'stage': stage, 'provided_evidence_ids': list(state.provided_ids),
                                    'state_block': dict(state.state_block),
                                    'ledger_accepted': None if state.last_update is None or not state.use_r else state.last_update.accepted,
                                    'model_requests': [dataclasses.asdict(r) for r in requests]})
        if state.use_a:
            return replace(result, context_requests=[])      # requests travel through the declaration hook (ranked pre-slice)
        return replace(result, context_requests=requests)

    def decl(review_notes, packet, code_index, additional_evidence=None):
        state.count('detect_declaration_evidence_gaps')
        found = real_decl(review_notes, head(packet), code_index, additional_evidence)
        found = relabel(found, f'r{state.calls}')
        if not state.use_a:
            return found
        state.count('detect_observation_path_gaps')
        obs = relabel(real_obs(review_notes, head(packet), state.registry.root, state.review_context, additional_evidence),
                      f'r{state.calls}')
        decl_paths = {r.path for r in found if r.path}
        obs = [r for r in obs if r.path not in decl_paths]
        return state.rank_for_a([*found, *obs, *state.pending_requests], state.batch_label())

    def obs(review_notes, packet, repo_root, review_context=None, additional_evidence=None):
        if state.use_a:
            return []
        state.count('detect_observation_path_gaps')
        return relabel(real_obs(review_notes, head(packet), repo_root, review_context, additional_evidence), f'r{state.calls}')

    def parse_json(text):
        value = originals[(llm, '_parse_json')](text)
        if isinstance(value, dict):
            state.last_payload = value
            state.payloads.append(value)
        return value

    patches = {
        (orch, 'run_llm_review_result'): run_llm,
        (orch, 'detect_declaration_evidence_gaps'): decl,
        (orch, 'detect_observation_path_gaps'): obs,
        (orch, 'detect_public_reference_gaps'): counting('detect_public_reference_gaps', originals[(orch, 'detect_public_reference_gaps')], 0),
        (orch, 'detect_related_test_gaps'): counting('detect_related_test_gaps', originals[(orch, 'detect_related_test_gaps')], 1),
        (orch, 'detect_terraform_graph_gaps'): counting('detect_terraform_graph_gaps', originals[(orch, 'detect_terraform_graph_gaps')], 1),
        (llm, 'needs_context_reflection'): lambda payload, packet, review_context=None: False,
        (llm, '_context_requests_from_payload'): counting('_context_requests_from_payload', originals[(llm, '_context_requests_from_payload')]),
        (llm, '_parse_json'): counting('_parse_json', parse_json),
        (llm, '_findings_from_payload'): counting('_findings_from_payload', originals[(llm, '_findings_from_payload')]),
    }
    try:
        for (module, name), fn in patches.items():
            setattr(module, name, fn)
        yield
    finally:
        for (module, name), fn in originals.items():
            setattr(module, name, fn)
        _PATCH_LOCK.release()


# ============================================================ completion client wrapper
class OarClient:
    """CompletionClient handed to the product. Adds the arm's prompt block, captures the raw JSON, never retries."""

    def __init__(self, inner, state: ReviewState):
        self.inner, self.state = inner, state

    def complete(self, *, system: str, prompt: str, suffix: str = '', shared: str = '') -> str:
        stage_calls = getattr(self.inner, '_stage_calls', 0)
        if stage_calls >= 1:                                   # product format retry / reflection: not issued
            try:
                self.inner.complete(system=system, shared=shared, prompt=prompt, suffix=suffix)
            except Exception as exc:                           # RetryRefused (logged by the transport)
                self.state.refused_retries.append({'stage': getattr(self.inner, '_stage', None), 'error': type(exc).__name__})
            return ''                                          # empty text: the product parser fails closed (format error)
        extra = self.state.suffix_extra()
        # exact-id invariant on the ACTUAL prompt text, before any model call
        self.state.rendered_seed_ids = check_rendered_seed_ids(self.state.inputs, '\n'.join([system, shared, prompt, suffix + extra]))
        return self.inner.complete(system=system, shared=shared, prompt=prompt, suffix=suffix + extra)


# ============================================================ one review
def native_status(registry) -> dict:
    try:
        status = registry.lsp_status()
    except Exception as exc:  # pragma: no cover - defensive
        return {'error': f'{type(exc).__name__}: {exc}'}
    return {lang: {k: v for k, v in info.items() if k in ('command', 'available', 'state', 'error', 'requests',
                                                         'resolved_items', 'initialization_ms', 'operations')}
            for lang, info in status.items()}


def citation_audit(findings: list, provided: set) -> list:
    out = []
    for i, f in enumerate(findings):
        cited = [e.get('evidence_id') for e in f.get('evidence', []) if isinstance(e, dict)]
        out.append({'index': i, 'cited': cited, 'valid': [c for c in cited if c in provided],
                    'invalid': [c for c in cited if c not in provided], 'has_trigger_mechanism_impact':
                    all(str(f.get(k, '')).strip() for k in ('trigger', 'mechanism', 'impact')),
                    'has_location': bool(f.get('path')) and isinstance(f.get('line_start'), int)})
    return out


def reportability(finding: dict, audit_row: dict, verdicts: dict, last_rejected: bool) -> dict:
    """R/AR: a final finding is reportable iff its candidate is supported in the ledger and every citation is valid."""
    reasons = []
    cid = finding.get('candidate_id')
    if last_rejected:
        reasons.append('last_ledger_update_rejected')
    if not cid:
        reasons.append('no_candidate_id')
    elif cid not in verdicts:
        reasons.append('candidate_not_in_ledger')
    elif verdicts[cid] != 'supported':
        reasons.append(f'candidate_verdict_{verdicts[cid]}')
    if not audit_row['cited']:
        reasons.append('no_citation')
    if audit_row['invalid']:
        reasons.append('invalid_citation')
    return {'ok': not reasons, 'reasons': reasons}


def run_review(key: str, arm: str, inner_client, *, product=None, registry_class=None,
               expected_inputs: Optional[dict] = None) -> dict:
    """Run ONE review through the real product loop. ``inner_client`` is a CliCompletionClient or fixture client."""
    product = product or load_product()
    started = time.time()
    config = pilot_config(product)
    accounting = QueryAccounting()
    account = oc.EvidenceAccount(oc.Budgets())
    holder: dict = {}

    def factory(root):
        masked = MaskedSources(root)
        holder['masked'] = masked
        state = holder['state'] = ReviewState(arm=arm, inputs={}, account=account, masked=masked, accounting=accounting,
                                              client_inner=inner_client, product=product)
        cls = registry_class or make_registry_class(product)
        registry = cls(root, packet_id=key, account=account, masked=masked, accounting=accounting, state=state,
                       config=config)
        state.registry = registry
        return registry

    inputs = build_inputs(key, factory, product, expected_inputs)
    state, masked = holder['state'], holder['masked']
    registry = state.registry
    verify = verify_workspace(inputs['data']['workspace_root'], inputs['data']['workspace_sources'])
    if expected_inputs is not None and verify['tree_sha256'] != expected_inputs.get('workspace_tree_sha256'):
        raise AdapterError(f'{key}: workspace manifest tree differs from the prepared/frozen one')
    agreement = seed_agreement(inputs, masked)
    if agreement['mismatched']:
        raise AdapterError(f'{key}: seed lines disagree with masked workspace: {agreement}')
    account.add_seed(inputs['seed_evidence'])
    diff_evidence = diff_core_evidence(inputs['diff'])
    for ev in diff_evidence:
        account.store.add(ev)
    inputs['provided_base_ids'] = [e.id for e in inputs['seed_evidence']] + [e.id for e in diff_evidence]
    state.inputs = inputs
    seed_tokens = account.exposed_tokens()
    if seed_tokens > LIMITS['source_union_reference_tokens']:
        raise AdapterError(f'{key}: seed already uses {seed_tokens} > {LIMITS["source_union_reference_tokens"]} tokens')
    client = OarClient(inner_client, state)
    error = None
    result = None
    try:
        with seed_rendering(inputs, product), scoped_patches(state, product):
            try:
                result = product.orchestrator.run_agentic_review(
                    inputs['diff'], inputs['packet'], client, registry, config,
                    review_context=inputs['review_context'])
            except Exception as exc:           # transport failures, state blocks: kept as the end reason, never retried
                error = exc
        ended = time.time()
        return finalize(key, arm, inputs, state, registry, account, accounting, masked, result, error, verify,
                        agreement, seed_tokens, started, ended, product)
    finally:
        lsp_shutdown(registry)                 # never leave a language server behind


def finalize(key, arm, inputs, state, registry, account, accounting, masked, result, error, verify, agreement,
             seed_tokens, started, ended, product) -> dict:
    final_payload = state.payloads[-1] if state.payloads else None
    last_provided = set(state.stage_records[-1]['provided_evidence_ids']) if state.stage_records else set()
    findings_raw = final_payload.get('findings', []) if (final_payload and result is not None) else []
    ledger_report = None
    if state.ledger is not None:
        ledger_report = state.ledger.final_report(state.last_update)
        ledger_report['export'] = state.ledger.export()
        ledger_report['transitions'] = [t.to_dict() for t in state.ledger.transitions]
        ledger_report['rejections'] = state.ledger.rejections
        ledger_report['cap_log'] = state.ledger.cap_log
    verdicts = state.ledger.verdicts() if state.ledger is not None else {}
    audit = citation_audit(findings_raw, last_provided)
    last_rejected = state.use_r and state.last_update is not None and not state.last_update.accepted
    for entry, raw in zip(audit, findings_raw):
        entry['ledger_verdict'] = verdicts.get(raw.get('candidate_id')) if state.ledger is not None else None
        if state.use_r:        # R/AR only: the method's reportability rule (P/A get no such gate)
            entry['reportability'] = reportability(raw, entry, verdicts, last_rejected)
    end_reason = result.trace.termination_reason if result is not None else f'exception:{type(error).__name__}'
    trace = result.trace if result is not None else None
    summary = account.summary()
    union_tokens = account.exposed_tokens()
    status = ('operational_failure' if error is not None or (ledger_report or {}).get('operational_failure') else 'completed')
    if error is None and trace is not None and trace.response_format_errors:
        status = 'operational_failure'
    invalid_final = [e['index'] for e in audit if e.get('reportability') and not e['reportability']['ok']]
    if status == 'completed' and invalid_final:
        status = 'invalid_final_report'        # raw findings stay in findings_raw; none is a successful report
    return {
        'review_id': f'{key}_{arm}', 'key': key, 'arm': arm, 'status': status, 'end_reason': end_reason,
        'error': None if error is None else {'type': type(error).__name__, 'message': str(error)[:2000],
                                             'record': getattr(error, 'record', None)},
        'policy': {'A_scheduling': ARMS[arm][0], 'R_ledger': ARMS[arm][1]},
        'timing': {'started_at': started, 'ended_at': ended, 'seconds': ended - started},
        'limits': LIMITS,
        'model_calls': state.calls, 'refused_retries': state.refused_retries,
        'queries': accounting.summary(),
        'source_exposure': {'seed_tokens': seed_tokens, 'union_reference_tokens': union_tokens,
                            'cap': LIMITS['source_union_reference_tokens'], 'union_lines': summary['union_lines'],
                            'union_sha256': summary['union_sha256'], 'caps': summary['caps'],
                            'protected_seed_ids_intact': all(e.id in account.protected and e.id in account.store
                                                             for e in inputs['seed_evidence']),
                            'consistency_blocks': [e for e in account.log if e.get('blocked_by') == 'inconsistent_source'],
                            'log': account.log, 'tokenizer': summary['tokenizer']},
        'initial_input': initial_input_summary(state),
        'stages': state.stage_records, 'schedule': state.schedule_trace,
        'product_calls': state.product_calls,
        'product_trace': None if trace is None else {
            'termination_reason': trace.termination_reason, 'iterations': trace.iterations,
            'requests': [dataclasses.asdict(r) for r in trace.requests],
            'evidence': [{'id': e.id, 'operation': e.operation, 'source': e.source, 'chars': len(e.content),
                          'truncated': e.truncated, 'metadata': e.metadata, 'content': e.content}      # = what the model saw
                         for e in trace.evidence],
            'tool_events': [dataclasses.asdict(e) for e in trace.tool_events],
            'evidence_tokens_product_estimate': trace.evidence_tokens,
            'response_format_retries': trace.response_format_retries,
            'response_format_errors': trace.response_format_errors,
            'context_reflections': trace.context_reflections},
        'tool_trace': registry.trace, 'metadata_allocation': {
            'cap': account.budgets.metadata_tokens_per_call, 'used': registry.meta_used, 'log': registry.meta_log,
            'rule': 'cumulative over the review: admitted metadata stays in every later prompt'},
        'native_summary': registry.native_summary,
        'lsp_status': native_status(registry),
        'findings_product': None if result is None else [dataclasses.asdict(f) for f in result.findings],
        'findings_raw': findings_raw, 'citation_audit': audit,
        'reportable_finding_indices': [e['index'] for e in audit if not e.get('reportability') or e['reportability']['ok']]
                                      if status == 'completed' else [],
        'invalid_final_finding_indices': invalid_final,
        'review_notes': None if result is None else result.review_notes,
        'payloads': state.payloads, 'ledger': ledger_report,
        'candidates_final_raw': (final_payload or {}).get('candidates'),
        'workspace': verify, 'seed_agreement': agreement, 'authoritative_inputs': inputs['data']['authoritative'],
        'seed_id_map': inputs['seed_id_map'], 'seed_snapshot_by_id': {r['id']: r['snapshot'] for r in inputs['seed_snippet_index'].values()},
        'rendered_seed_ids': state.rendered_seed_ids, 'diff_evidence_ids': [f'diff:{f.path}' for f in inputs['diff'].files],
    }


def initial_input_summary(state) -> dict:
    """The first model call already contains the automatic pre-gap lookups (product behaviour); say so."""
    if not state.stage_records:
        return {'model_called': False}
    base = set(state.inputs.get('provided_base_ids', []))
    extra = [i for i in state.stage_records[0]['provided_evidence_ids'] if i not in base]
    return {'model_called': True, 'pre_gap_evidence_ids': extra, 'differs_from_pure_seed': bool(extra),
            'pre_gap_queries': next((b for b in state.accounting.batches if b['label'] == 'pre_gap'), None),
            'note': 'the product runs its gap detectors before the first model call; their results are in the first prompt'}


def lsp_shutdown(registry) -> None:
    resolver = getattr(registry, 'lsp_resolver', None)
    close = getattr(resolver, 'close', None) or getattr(resolver, 'shutdown', None)
    if close:
        with contextlib.suppress(Exception):
            close()
