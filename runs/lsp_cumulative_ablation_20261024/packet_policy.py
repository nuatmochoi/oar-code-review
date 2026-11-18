"""Cumulative evidence ablations; no model or native-server calls.

B0 receives no repository or historical packet. B1 selects from changed-file
ASTs only. B2 replays the exact F native answers under the unchanged F policy,
then changes packing only; no query is generated from an evicted packet.
"""
import ast
import copy
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ace_review.context_adaptive import digest, token_count
from ace_review.protocol_hypothesis import mask_source


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


old = _load('cumulative_legacy_ast', ROOT / 'runs/lsp_linked_adaptive_20261023/policy.py')
fixed_policy = _load('cumulative_fixed_policy', ROOT / 'runs/lsp_agentic_preserved_recovery_20261023/agentic_policy.py')
SOURCE_CAP, OBS_CAP = 5200, 800


def diff_only():
    """Deliberately no arguments: DIFF and public scope live in common prompt."""
    return {'source': '', 'plain': '', 'audit': {
        'policy': 'diff_only_v1', 'source_tokens': 0, 'source_lines': {},
        'base_lines': {}, 'queries': [], 'roots': [], 'deleted_roots': [],
        'admitted': [], 'blocked': [], 'fresh_native_queries': 0,
        'cached_native_queries': 0, 'ast_parsed_files': [],
        'root_coverage': None, 'has_roots': None,
        'source_selection_inputs': [],
    }}


def ast_only(base, head, scope=None):
    """Legacy changed/deleted root selection with no native-selected inputs.

    Callers supply only raw changed files; scope is ignored. Literal lexical
    guard headers are retained without adding target resolution.
    """
    assert all(base.get(f) != head.get(f) for f in set(base) | set(head))
    hd, ho, ht = old.index(head)
    bd, bo, bt = old.index(base)
    roots, deleted, events = old.changed_roots(base, head, bd, hd)
    roots.sort()
    lines, base_lines, admitted, blocked = {}, {}, [], []
    indexes = {}

    def source():
        return old.render(base, base_lines, 'base') + '\n' + old.render(head, lines)

    for version, targets, src, defs, owners, trees, dest in [
        ('base', deleted, base, bd, bo, bt, base_lines),
        ('head', roots, head, hd, ho, ht, lines),
    ]:
        for target in targets:
            f, qual = target
            if (version, f) not in indexes:
                indexes[(version, f)] = fixed_policy.FileIndex(src[f])
            idx = indexes[(version, f)]
            package = old.package(src, defs, owners, trees, target)
            package |= idx.context_lines(idx.defs[qual])
            before = set(dest.get(f, set()))
            dest[f] = before | package
            record = {'key': [version, f, qual], 'origin': 'deleted_base' if version == 'base' else 'changed_root', 'lines': sorted(package)}
            if token_count(source()) > SOURCE_CAP:
                dest[f] = before
                blocked.append({**record, 'reason': 'whole AST package exceeds source cap'})
            else:
                admitted.append(record)
    rendered = source()
    plain = ('[E-COVERAGE] Static AST-selected source only. No native definition or references query was used. '
             'Source contains changed or deleted functions, lexical owner/guard headers, and syntactically used same-file imports. '
             'No cross-file call target or runtime binding is inferred. '
             f'Changed roots: {len(roots)}; deleted base-only roots: {len(deleted)}; blocked whole packages: {len(blocked)}. '
             'Missing source is unknown, not evidence of absence.')
    return {'source': rendered, 'plain': plain, 'audit': {
        'policy': 'changed_file_ast_only_v1', 'roots': roots, 'deleted_roots': deleted,
        'root_events': events, 'source_lines': {f: sorted(v) for f, v in lines.items()},
        'base_lines': {f: sorted(v) for f, v in base_lines.items()},
        'source_tokens': token_count(rendered), 'admitted': admitted, 'blocked': blocked,
        'queries': [], 'fresh_native_queries': 0, 'cached_native_queries': 0,
        'ast_parsed_files': sorted(set(base) | set(head)),
        'source_selection_inputs': 'raw changed base/head files only; deterministic file/qualified-name root order; scope ignored',
        'root_coverage': len(admitted) == len(roots) + len(deleted), 'has_roots': bool(roots or deleted),
    }}


class ReplayClient:
    """Exact cached F answers only; unsupported or changed queries fail closed."""
    def __init__(self, queries):
        self.queries, self.cursor = queries, 0

    def query(self, method, file, line, char):
        assert self.cursor < len(self.queries), 'replay attempted an extra native query'
        q = self.queries[self.cursor]
        assert (method, file, line + 1, char) == (q['operation'], q['file'], q['line'], q['character_utf16']), 'fixed query schedule changed'
        self.cursor += 1
        assert not q.get('error'), 'cannot replay failed native answer'
        return copy.deepcopy(q['raw'])

    def definition(self, f, line, char):
        return self.query('definition', f, line, char)

    def references(self, f, line, char):
        return self.query('references', f, line, char)


class CaptureList(list):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def append(self, text):
        self.callback(text)
        super().append(text)


class CaptureNavigator(fixed_policy.Navigator):
    """Observe candidate attempts without changing any F decision."""
    def __init__(self, *args):
        super().__init__(*args)
        self.candidates = []
        self.current_class = None
        self.inventory_texts = CaptureList(self.capture_inventory)

    def add_lines(self, f, lines, depth, origin, component, provenance):
        required = sorted(set(lines))
        outcome = super().add_lines(f, required, depth, origin, component, provenance)
        self.candidates.append({'sequence': len(self.candidates), 'kind': 'lines',
            'file': f, 'lines': required, 'depth': depth, 'origin': origin,
            'component': component, 'provenance': copy.deepcopy(provenance),
            'f_outcome': outcome})
        return outcome

    def capture_inventory(self, text):
        f, qual, n, depth, origin, provenance = self.current_class
        inv = fixed_policy._structure.class_inventory(self.text(f), f, qual, n)
        inv['provenance'] = copy.deepcopy(provenance)
        self.candidates.append({'sequence': len(self.candidates), 'kind': 'inventory',
            'file': f, 'component': qual + ':inventory', 'depth': depth,
            'origin': origin, 'provenance': copy.deepcopy(provenance),
            'text': text, 'inventory': inv, 'f_outcome': 'pending'})

    def add_class(self, f, qual, n, depth, origin, provenance):
        previous = self.current_class
        self.current_class = (f, qual, n, depth, origin, provenance)
        start = len(self.candidates)
        outcome = super().add_class(f, qual, n, depth, origin, provenance)
        for candidate in self.candidates[start:]:
            if candidate['kind'] == 'inventory' and candidate['f_outcome'] == 'pending':
                candidate['f_outcome'] = 'added' if candidate['text'] in self.inventory_texts else 'source_budget'
        self.current_class = previous
        return outcome


def capture_fixed_history(root, base_root, seed, fixed, scope):
    nav = CaptureNavigator(root, base_root, seed, scope, False)
    nav.cached_module_gaps()
    nav.deleted_bootstrap()
    nav.generic()
    replay = ReplayClient(fixed['audit']['additional_queries'])
    nav.run(replay)
    reproduced = nav.packet()
    assert replay.cursor == len(replay.queries)
    assert reproduced['source'] == fixed['source'], 'F source replay mismatch'
    assert reproduced['plain'] == fixed['plain'], 'F observations replay mismatch'
    assert reproduced['audit']['additional_queries'] == fixed['audit']['additional_queries'], 'F query/result replay mismatch'
    return nav.candidates


def pack_fixed_history(root, base_root, seed, fixed, candidates, cap=SOURCE_CAP):
    """Reverse optional legacy eviction only when a recorded candidate overflows.

    Query history/candidate attempt order are fixed. Changed/deleted packages
    are never removed. Shared lines are a union of all retained packages and
    accepted candidate requirements, rather than subtracting evicted lines.
    """
    if not any(c['f_outcome'] == 'source_budget' for c in candidates) and cap == SOURCE_CAP:
        return copy.deepcopy(fixed), {'same_f_source_plain': True, 'evicted_seed_packages': [], 'packing_events': [], 'candidate_count': len(candidates)}
    root, base_root = Path(root), Path(base_root)
    packages = copy.deepcopy(seed['audit']['admitted'])
    kept = set(range(len(packages)))
    mandatory = {i for i, p in enumerate(packages) if p['origin'] in {'changed_root', 'deleted_base'}}
    optional = [i for i in reversed(range(len(packages))) if i not in mandatory]
    selected, decisions, evictions = [], [], []
    texts = {}

    def get_text(version, f):
        if (version, f) not in texts:
            texts[(version, f)] = ((root if version == 'head' else base_root) / f).read_text()
        return texts[(version, f)]

    def seed_lines(indices):
        result = {'head': {}, 'base': {}}
        for i in indices:
            p = packages[i];version, f, _ = p['key']
            result[version].setdefault(f, set()).update(p['lines'])
        return result

    original = seed_lines(kept)
    assert original['head'] == {f: set(v) for f, v in seed['audit']['source_lines'].items() if v}
    assert original['base'] == {f: set(v) for f, v in seed['audit']['base_lines'].items() if v}

    def render(indices, events):
        supplied = seed_lines(indices)
        if indices == set(range(len(packages))):
            prefix = seed['source']
        else:
            prefix = old.render({f: get_text('base', f) for f in supplied['base']}, supplied['base'], 'base')
            prefix += '\n' + old.render({f: get_text('head', f) for f in supplied['head']}, supplied['head'])
        extra, invtexts = {}, []
        for c in events:
            if c['kind'] == 'inventory':
                invtexts.append(c['text'])
            else:
                extra.setdefault(c['file'], set()).update(c['lines'])
        out = [prefix]
        for f, required in sorted(extra.items()):
            addition = required - supplied['head'].get(f, set())
            if not addition:
                continue
            out.append(f'[E-head-{digest(f)[:10]}] head {f}; additional numbered supplied lines only; omitted code unknown')
            rows = mask_source(get_text('head', f)).splitlines();prev = None
            for ln in sorted(addition):
                if prev is not None and ln != prev + 1:out.append('... omitted ...')
                out.append(f'{ln}: {rows[ln - 1]}');prev = ln
        out.extend(invtexts)
        for f, ls in extra.items():supplied['head'].setdefault(f, set()).update(ls)
        return '\n'.join(out), supplied

    # Capture must reproduce F's source selection before any counterfactual packing.
    original_events = [c for c in candidates if c['f_outcome'] != 'source_budget']
    assert render(kept, original_events)[0] == fixed['source'], 'candidate rendering differs from F'
    for c in candidates:
        tentative = selected + [c]
        trial = set(kept)
        rendered, _ = render(trial, tentative)
        removed = []
        if token_count(rendered) > cap:
            for i in optional:
                if i not in trial:continue
                trial.remove(i);removed.append(i)
                rendered, _ = render(trial, tentative)
                if token_count(rendered) <= cap:break
        accepted = token_count(rendered) <= cap
        if accepted:
            kept = trial;selected = tentative
            evictions.extend({'candidate_sequence': c['sequence'], 'seed_admission_index': i, 'package': packages[i]} for i in removed)
        decisions.append({'candidate_sequence': c['sequence'], 'kind': c['kind'], 'file': c['file'],
            'component': c['component'], 'f_outcome': c['f_outcome'],
            'outcome': 'retained' if accepted else 'source_budget',
            'evicted_seed_admission_indices': removed if accepted else [],
            'attempted_evictions_rolled_back': removed if not accepted else [],
            'source_tokens_after': token_count(render(kept, selected)[0])})
    source, supplied = render(kept, selected)
    assert mandatory <= kept and token_count(source) <= cap
    for i in mandatory:
        version, f, _ = packages[i]['key']
        assert set(packages[i]['lines']) <= supplied[version][f]
    lost = {version: {f: sorted(ls - supplied[version].get(f, set())) for f, ls in original[version].items() if ls - supplied[version].get(f, set())} for version in ['head', 'base']}
    same = source == fixed['source']
    if same:
        return copy.deepcopy(fixed), {'same_f_source_plain': True, 'evicted_seed_packages': evictions, 'packing_events': decisions, 'candidate_count': len(candidates), 'dropped_seed_lines': lost}
    plain = seed['plain'] + '\n[E-RETRIEVAL] Fixed native query/result history is reused; no new native query was executed. '
    plain += ('Only final source packing changed: optional seed packages may be replaced in reverse admission order; '
              'changed and deleted root packages remain supplied. ')
    plain += f"Historical native queries: {len(fixed['audit']['queries'])}; optional packages evicted: {len(evictions)}; recorded candidates rejected by final source budget: {sum(d['outcome'] == 'source_budget' for d in decisions)}. "
    plain += 'Inventories describe static lexical structure; omitted source and empty native results do not prove runtime absence.'
    assert token_count(plain) <= OBS_CAP
    audit = copy.deepcopy(fixed['audit'])
    audit.update(policy='fixed_history_optional_seed_eviction_v1', seed_preserved=False,
        changed_deleted_roots_preserved=True, source_tokens=token_count(source),
        source_lines={f: sorted(v) for f, v in supplied['head'].items()},
        base_lines={f: sorted(v) for f, v in supplied['base'].items()},
        admitted=[packages[i] for i in sorted(kept)],
        class_inventories=[c['inventory'] for c in selected if c['kind'] == 'inventory'],
        additional_admitted=[c for c in selected if c['kind'] == 'lines'],
        additional_blocked=[d for d in decisions if d['outcome'] == 'source_budget'],
        retained_seed_admission_indices=sorted(kept), mandatory_seed_admission_indices=sorted(mandatory),
        evicted_seed_packages=evictions, dropped_seed_lines=lost, packing_events=decisions,
        fixed_candidate_count=len(candidates), fixed_query_history_unchanged=True,
        query_outcomes_semantics='Historical F traversal outcomes before counterfactual source packing; queries were valid at that historical point.',
        fresh_native_queries=0, cached_native_queries=len(fixed['audit']['queries']),
        cached_seed_native_queries=len(seed['audit']['queries']), cached_fixed_followup_native_queries=len(fixed['audit']['additional_queries']),
        fixed_query_history_sha256=digest(json.dumps(fixed['audit']['queries'], sort_keys=True)), mode='B2')
    return {'source': source, 'plain': plain, 'audit': audit}, {
        'same_f_source_plain': False, 'evicted_seed_packages': evictions,
        'packing_events': decisions, 'candidate_count': len(candidates), 'dropped_seed_lines': lost,
        'fixed_query_history_unchanged': True, 'fresh_native_queries': 0,
    }
