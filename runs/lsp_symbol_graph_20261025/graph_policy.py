"""H: bounded typed-occurrence navigation on top of the frozen G policy.

The graph records native definition/reference links, not runtime dispatch or
dataflow. Python protocol, annotation and literal assignment edges are explicit
same-file AST *search hints*. They never equate symbols by spelling. Only real
native targets may cause cross-file source admission. No evaluation data enters
this module. All source additions use G's masking, whole-body and cap rules.
"""
import ast
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_name = 'symbol_graph_frozen_preserved_policy'
if _name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_name, ROOT / 'runs/lsp_agentic_preserved_20261023/agentic_policy.py')
    g = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = g
    _spec.loader.exec_module(g)
else:
    g = sys.modules[_name]

Client, prepare_seed = g.Client, g.prepare_seed
token_count, digest = g.token_count, g.digest
SOURCE_CAP, OBS_CAP, EXTRA_QUERIES, QUERY_CAP, DEPTH = g.SOURCE_CAP, g.OBS_CAP, g.EXTRA_QUERIES, g.QUERY_CAP, g.DEPTH
MAX_NATIVE_TARGETS, MAX_CANDIDATE_LOCATIONS = g.MAX_NATIVE_TARGETS, g.MAX_CANDIDATE_LOCATIONS
FUNCS = g.FUNCS

# Python protocol suggestions, not a claim that a spelling denotes a builtin.
PROTOCOL_CALLS = {
    'len': ('__len__',), 'iter': ('__iter__', '__getitem__'),
    'next': ('__next__',), 'reversed': ('__reversed__', '__len__', '__getitem__'),
    'tuple': ('__iter__', '__getitem__'), 'list': ('__iter__', '__getitem__'),
    'set': ('__iter__', '__getitem__'), 'frozenset': ('__iter__', '__getitem__'),
    'enumerate': ('__iter__', '__getitem__'),
}
PROTOCOL_MEMBERS = frozenset(x for names in PROTOCOL_CALLS.values() for x in names) | {'__new__', '__init__', '__contains__', '__bool__'}


def _qkey(q):
    return q['operation'], q['file'], q['line'] - 1, q['character_utf16']


class Navigator(g.Navigator):
    def __init__(self, root, base_root, seed, scope, agentic):
        super().__init__(root, base_root, seed, scope, agentic)
        self.graph_nodes, self.graph_edges, self.edge_keys = {}, [], set()
        self.graph_frontier, self.candidate_rankings, self.seed_native_records = [], [], []
        self.needs, self.relations, self.registered, self.walked, self.ast_hints = {}, {}, {}, set(), set()
        self.class_frontiers, self.graph_scanned = set(), set()
        self.snapshot_hashes = {}
        self.native_classes = {}
        self.anchor_symbols, self.symbol_anchors, self.symbol_members = {}, {}, {}
        self.symbol_evidence, self.canonical_events, self.canonical_walked = {}, [], set()
        self.native_admissions = {}
        self.bootstrap_graph()

    def snapshot(self, f):
        if f not in self.snapshot_hashes:
            self.snapshot_hashes[f] = digest(self.text(f))
        return self.snapshot_hashes[f]

    def locations(self, raw):
        # Preserve the server selection range. G's packaging only needs its
        # start, but graph identity must not merge different occurrences.
        if isinstance(raw, dict):
            raw = [raw]
        result = []
        for loc in raw or []:
            normalized = super().locations([loc])[0]
            if 'file' in normalized:
                rg = loc.get('targetSelectionRange', loc.get('range'))
                normalized['range'] = copy.deepcopy(rg)
            result.append(normalized)
        return result

    def occurrence(self, f, line, char, rg=None, kind='occurrence'):
        if rg is None:
            rg = {'start': {'line': line - 1, 'character': char}, 'end': {'line': line - 1, 'character': char}}
        identity = {'version': 'head', 'file': f, 'range': rg, 'source_sha256': self.snapshot(f)}
        ident = ('S-' if kind == 'native_definition_symbol' else 'O-') + digest(json.dumps(identity, sort_keys=True))[:24]
        self.graph_nodes.setdefault(ident, {'id': ident, 'kind': kind, **identity})
        return ident

    def edge(self, origin, target, kind, provenance, **extra):
        row = {'from': origin, 'to': target, 'kind': kind, 'provenance': provenance, **extra}
        key = digest(json.dumps(row, sort_keys=True))
        if key not in self.edge_keys:
            self.edge_keys.add(key)
            self.graph_edges.append(row)

    def native_window(self, q, raw):
        locations = self.locations(raw)
        valid = [x for x in locations if 'file' in x]
        # Exactly G's pre-ranking 32-location window, before any semantic hint.
        window = sorted(valid, key=lambda x: (x['file'] != q['file'], x['file'], x['line']))[:MAX_CANDIDATE_LOCATIONS]
        return locations, window, len(valid) - len(window)

    def register_native(self, q, raw, phase, number):
        key = _qkey(q)
        if key in self.registered:
            return self.registered[key]
        _, window, omitted = self.native_window(q, raw)
        anchor = self.occurrence(q['file'], q['line'], q['character_utf16'])
        query_identity = {'phase': phase, 'query_index': number, 'query_key': list(key),
                          'kind': 'actual cached native result' if phase == 'seed' else 'actual additional native result',
                          'raw_sha256': digest(json.dumps(raw, sort_keys=True))}
        items = []
        for x in window:
            target = self.occurrence(x['file'], x['line'], x['character_utf16'], x['range'])
            provenance = {**query_identity, 'returned_location': copy.deepcopy(x)}
            # Reference results remain occurrences related by the native
            # query. They are never unioned with same-named declarations.
            self.edge(anchor, target, 'native_' + q['operation'], provenance)
            if q['operation'] == 'definition':
                symbol = self.occurrence(x['file'], x['line'], x['character_utf16'], x['range'], 'native_definition_symbol')
                self.edge(target, symbol, 'native_definition_location', provenance)
                self.anchor_symbols.setdefault(key, set()).add(symbol)
                self.symbol_anchors.setdefault(symbol, set()).add(key)
                self.symbol_evidence.setdefault(symbol, []).append((copy.deepcopy(q), x, provenance))
                # Only exact native target identity combines needs. A shared
                # text name or enclosing class never enters this mapping.
                self.symbol_members.setdefault(symbol, set()).update(self.needs.get(key, ()))
                for anchor_key in self.symbol_anchors[symbol]:
                    self.needs.setdefault(anchor_key, set()).update(self.symbol_members[symbol])
            else:
                symbol = None
            items.append((x, target, symbol, provenance))
        self.registered[key] = items
        if phase == 'seed':
            self.seed_native_records.append({**query_identity, 'registered_locations': len(items), 'uninspected_native_locations': omitted})
        return items

    def effective_needs(self, key):
        members = set(self.needs.get(key, ()))
        for symbol in self.anchor_symbols.get(key, ()):
            self.symbol_members.setdefault(symbol, set()).update(members)
            members.update(self.symbol_members[symbol])
        for symbol in self.anchor_symbols.get(key, ()):
            self.symbol_members[symbol].update(members)
            for anchor in self.symbol_anchors[symbol]:
                self.needs.setdefault(anchor, set()).update(members)
        return members

    def bootstrap_graph(self):
        for i, q in enumerate(self.seed_queries):
            self.register_native(q, q.get('raw'), 'seed', i + 1)

    def cached_module_gaps(self):
        super().cached_module_gaps()
        # G's initial cached-module admissions share the same cumulative
        # per-query fanout ledger with later canonical-endpoint revisits.
        for row in self.outcomes:
            if row.get('phase') == 'cached_module' and row.get('outcome') == 'added':
                self.native_admissions.setdefault(('seed', row['seed_query_index']), set()).add(
                    (row['file'], 'module', row['line']))

    def token_node(self, f, line, char):
        """AST role at an exact native coordinate; not enclosing==binding."""
        idx = self.index(f)
        for n in idx.names:
            ln, ch = g._position(idx.text, n)
            if (ln + 1, ch) == (line, char):
                return n
        for n in idx.defs.values():
            if idx.declaration_position(n) == (line - 1, char):
                return n
        for n in ast.walk(idx.tree):
            if isinstance(n, ast.arg) and g._position(idx.text, n) == (line - 1, char):
                return n
        return None

    def role(self, f, line, char):
        n = self.token_node(f, line, char)
        if isinstance(n, ast.ClassDef):
            return 'class_declaration'
        if isinstance(n, FUNCS):
            return 'callable_declaration'
        if isinstance(n, ast.arg):
            return 'parameter_declaration'
        idx = self.index(f)
        parent = idx.parents.get(id(n))
        if isinstance(parent, ast.Call) and parent.func is n:
            return 'call'
        if isinstance(parent, ast.ClassDef) and n in parent.bases:
            return 'inheritance'
        if isinstance(n, (ast.Name, ast.Attribute)):
            return 'write' if isinstance(n.ctx, (ast.Store, ast.Del)) else 'read'
        return 'other'

    def query(self, method, f, line, char, symbol, depth, priority, origin, intent='definition', hint=None):
        key = (method, f, line, char)
        self.relations.setdefault(key, set()).add(intent)
        return super().query(method, f, line, char, symbol, depth, priority, origin, intent, hint)

    def want(self, f, node, members, depth, reason, priority=-2.5, relation='protocol'):
        """Follow an actual visible expression; never synthesize a member use."""
        if isinstance(node, ast.Call):
            node = node.func
        if not isinstance(node, (ast.Name, ast.Attribute)):
            self.graph_frontier.append({'file': f, 'reason': reason, 'wanted_members': sorted(members), 'outcome': 'unsupported_expression_anchor'})
            return 'unsupported_expression_anchor'
        ln, ch = g._position(self.text(f), node)
        key = ('definition', f, ln, ch)
        self.needs.setdefault(key, set()).update(members)
        self.effective_needs(key)
        self.relations.setdefault(key, set()).add(relation)
        outcome = self.query('definition', f, ln, ch, ast.unparse(node), depth,
                             (priority, depth, f, ln), 'declared_graph_frontier', relation)
        self.graph_frontier.append({'file': f, 'line': ln + 1, 'character_utf16': ch, 'wanted_members': sorted(members),
                                    'reason': reason, 'outcome': outcome, 'depth': depth,
                                    'semantics': 'same-file AST search hint; not runtime binding'})
        if outcome == 'already_queried':
            self.revisit(key)
        return outcome

    def generic(self):
        super().generic()
        for f in sorted(set(self.protected) | set(self.extra)):
            idx, supplied = self.index(f), self.visible(f)
            for n in ast.walk(idx.tree):
                if not hasattr(n, 'lineno') or n.lineno not in supplied:
                    continue
                spec = None
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in PROTOCOL_CALLS and n.args:
                    spec = (n.args[0], PROTOCOL_CALLS[n.func.id], 'visible Python protocol-call candidate ' + n.func.id)
                elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
                    spec = (n.iter, ('__aiter__', '__anext__') if isinstance(n, ast.AsyncFor) or getattr(n, 'is_async', 0) else ('__iter__', '__getitem__'), 'visible iteration expression')
                elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load):
                    # Exclude type annotation subscription: not value indexing.
                    parent = idx.parents.get(id(n))
                    if not (isinstance(parent, ast.arg) or isinstance(parent, ast.AnnAssign) and parent.annotation is n or isinstance(parent, FUNCS) and parent.returns is n):
                        spec = (n.value, ('__getitem__',), 'visible value subscription candidate')
                if spec is None:
                    continue
                expr, members, reason = spec
                key = (f, n.lineno, n.col_offset, reason)
                if key in self.graph_scanned:
                    continue
                self.graph_scanned.add(key)
                self.want(f, expr, members, self.depths.get((f, n.lineno), DEPTH + 1), reason)

    def add_declared_gaps(self, review):
        super().add_declared_gaps(review)
        # A structured missing protocol name can prioritize a visible lexical
        # owner. Its spelling never identifies a class in another file.
        for gap in self.declared_gaps:
            symbol = gap.get('symbol', '')
            member = symbol.strip(' `').split('.')[-1] if isinstance(symbol, str) else ''
            if member not in PROTOCOL_MEMBERS:
                continue
            for f in sorted(set(self.protected) | set(self.extra)):
                if gap.get('evidence_id') != 'E-head-' + digest(f)[:10]:
                    continue
                for qual, node in self.index(f).defs.items():
                    if not isinstance(node, ast.ClassDef) or node.lineno not in self.visible(f):
                        continue
                    pos = self.index(f).declaration_position(node)
                    if not pos:
                        continue
                    key = ('definition', f, *pos)
                    self.needs.setdefault(key, set()).add(member)
                    self.effective_needs(key)
                    outcome = self.query('definition', f, *pos, node.name, self.depths.get((f, node.lineno), DEPTH + 1),
                                         (-2.5, 0, f, node.lineno), 'declared_graph_protocol_owner', 'protocol')
                    self.graph_frontier.append({'file': f, 'line': node.lineno, 'wanted_members': [member], 'reason': 'structured missing member hint on visible lexical owner', 'outcome': outcome})
                    if outcome == 'already_queried':
                        self.revisit(key)

    def revisit(self, key):
        # Cached native edges may close newly expressed needs without new LSP
        # calls. Replay requires the original raw-result location and snapshot.
        members = frozenset(self.effective_needs(key))
        visit = (key, members)
        if visit in self.walked or not members:
            return
        self.walked.add(visit)
        admitted = 0
        # Resolve via canonical native symbols, so needs learned at any
        # already linked occurrence propagate to the same exact endpoint.
        for symbol in sorted(self.anchor_symbols.get(key, ())):
            marker = (symbol, members)
            if marker in self.canonical_walked:
                continue
            self.canonical_walked.add(marker)
            q, x, provenance = min(self.symbol_evidence[symbol], key=lambda row: (row[0]['depth'], row[0]['file'], row[0]['line']))
            if admitted >= MAX_NATIVE_TARGETS:
                self.outcomes.append({'phase': 'graph_cached_frontier', 'outcome': 'fanout_budget', **x})
                continue
            outcome = self.admit_target(q, x, provenance, set(members), cached=True)
            admitted += outcome.get('outcome') == 'added'
            self.outcomes.append({'phase': 'graph_cached_frontier', **outcome})
            self.canonical_events.append({'symbol': symbol, 'trigger_query_key': list(key),
                                           'linked_occurrences': [list(k) for k in sorted(self.symbol_anchors[symbol])],
                                           'wanted_members': sorted(members), 'selected_native_provenance': provenance,
                                           'outcome': outcome['outcome'], 'additional_anchor_queries': 0})

    def lexical_hint(self, f, source, target, members, depth, reason, priority=-4):
        if not members or target is None:
            return
        key = (f, getattr(source, 'lineno', 0), getattr(target, 'lineno', 0), getattr(target, 'col_offset', 0), tuple(sorted(members)), reason)
        if key in self.ast_hints:
            return
        self.ast_hints.add(key)
        node = target.func if isinstance(target, ast.Call) else target
        if isinstance(node, (ast.Name, ast.Attribute)):
            ln, ch = g._position(self.text(f), node)
            src_line, src_char = g._position(self.text(f), source)
            a = self.occurrence(f, src_line + 1, src_char)
            b = self.occurrence(f, ln + 1, ch)
            self.edge(a, b, 'same_file_ast_hint', {'kind': reason, 'source_sha256': self.snapshot(f)}, wanted_members=sorted(members))
        self.want(f, target, members, depth, reason, priority)

    def transfer(self, q, x, members, depth):
        """Only explicit annotation/assignment/return syntax; no dataflow claim."""
        if not members:
            return
        f = x['file'];idx = self.index(f)
        node = self.token_node(f, x['line'], x['character_utf16'])
        if node is None:
            return
        if isinstance(node, ast.arg):
            self.lexical_hint(f, node, node.annotation, members, depth, 'parameter annotation')
        elif isinstance(node, FUNCS):
            self.lexical_hint(f, node, node.returns, members, depth, 'callable return annotation')
        elif isinstance(node, (ast.Name, ast.Attribute)):
            parent = idx.parents.get(id(node))
            if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and isinstance(node.ctx, ast.Store):
                self.lexical_hint(f, node, getattr(parent, 'annotation', None), members, depth, 'assignment annotation')
                self.lexical_hint(f, node, parent.value, members, depth, 'literal assignment value; not value-flow proof')

    def expand_class(self, f, qual, node, members, depth, provenance):
        idx = self.index(f)
        self.native_classes[(f, node.lineno)] = provenance
        # Read direct lexical members including conditional suites, excluding
        # nested class/function scopes. Inventory keeps those guards explicit.
        direct = []
        def walk(stmts):
            for stmt in stmts:
                if isinstance(stmt, FUNCS):
                    direct.append(stmt)
                elif not isinstance(stmt, ast.ClassDef):
                    for field in ('body', 'orelse', 'finalbody'):
                        suite = getattr(stmt, field, None)
                        if isinstance(suite, list):
                            walk(suite)
                    for handler in getattr(stmt, 'handlers', []):
                        walk(handler.body)
                    for case in getattr(stmt, 'cases', []):
                        walk(case.body)
        walk(node.body)
        found = set()
        for method in sorted(direct, key=lambda n: (n.name not in members, n.name != '__new__', n.lineno)):
            if method.name not in members:
                continue
            found.add(method.name)
            mark = (f, node.lineno, method.lineno, method.name)
            if mark in self.class_frontiers:
                continue
            self.class_frontiers.add(mark)
            outcome = self.decl_query(f, method, depth, 'declared_graph_member_signature', (-4, depth, f, method.lineno), intent='protocol_member')
            pos = idx.declaration_position(method)
            self.graph_frontier.append({'file': f, 'line': method.lineno, 'qualname': qual, 'wanted_members': [method.name],
                                        'reason': 'literal member signature in a native-resolved class; body still needs actual definition query',
                                        'outcome': outcome, 'native_class_provenance': provenance})
            if pos:
                a = self.occurrence(f, node.lineno, idx.declaration_position(node)[1])
                b = self.occurrence(f, pos[0] + 1, pos[1])
                self.edge(a, b, 'same_file_lexical_member_hint', {'kind': 'explicit class declaration structure', 'source_sha256': self.snapshot(f)}, member=method.name)
        missing = members - found
        if missing:
            # Missing direct declarations do not prove inherited absence.
            for base in node.bases:
                self.lexical_hint(f, node, base, missing, depth, 'literal base expression of native-resolved class')

    def rank(self, q, x):
        f, line, ch = x['file'], x['line'], x['character_utf16']
        role = self.role(f, line, ch)
        intents = self.relations.get(_qkey(q), set()) | {q.get('intent', '')}
        wants = self.effective_needs(_qkey(q))
        fit = 2
        if q['operation'] == 'definition':
            fit = 0 if role in {'class_declaration', 'callable_declaration', 'parameter_declaration'} else 1
        if 'attribute_consumer' in intents:
            fit = 0 if role in {'read', 'call'} else 2
        elif 'inheritance' in intents:
            fit = 0 if role == 'inheritance' else 2
        elif 'caller' in intents or 'constructor' in intents:
            fit = 0 if role in {'call', 'class_declaration'} else 2
        if wants and role in {'class_declaration', 'parameter_declaration'}:
            fit = -1
        enclosing = self.index(f).enclosing(line)
        if enclosing:
            qual, node = enclosing
            body = set(range(g._start(node), node.end_lineno + 1))
            missing = bool(body - self.visible(f))
        else:
            qual, missing = None, line not in self.visible(f)
        # Relation fit and missing body come before G's stable lexical order.
        hint = q.get('hint')
        hinted = False if not hint else not bool(re.search(r'(?<!\w)' + re.escape(hint.split('.')[-1]) + r'(?!\w)', self.text(f).splitlines()[line - 1]))
        return (hinted, fit, not missing, f != q['file'], f, line, ch), role, qual

    def admit_target(self, q, x, provenance, members, cached=False):
        f, line = x['file'], x['line']
        if q['depth'] > DEPTH:
            return {**x, 'outcome': 'depth_budget'}
        idx, depth = self.index(f), q['depth'] + 1
        origin = 'cached_native_target' if cached else 'graph_native_' + q['operation']
        enclosing = idx.enclosing(line)
        package = (f, enclosing[0]) if enclosing else (f, 'module', line)
        admission_key = (provenance['phase'], provenance['query_index'])
        prior_admitted = self.native_admissions.setdefault(admission_key, set())
        if package not in prior_admitted and len(prior_admitted) >= MAX_NATIVE_TARGETS:
            return {**x, 'outcome': 'fanout_budget'}
        if enclosing:
            qual, n = enclosing
            if isinstance(n, ast.ClassDef):
                outcome = self.add_class(f, qual, n, depth, origin, provenance)
                # Only a native class declaration, not an arbitrary occurrence
                # enclosed in a class, unlocks the class-member frontier.
                exact = self.token_node(f, line, x['character_utf16'])
                if isinstance(exact, ast.ClassDef) and q['operation'] == 'definition' and outcome != 'source_budget':
                    desired = set(members)
                    if q.get('intent') in {'constructor', 'callee'} or {'constructor', 'callee'} & self.relations.get(_qkey(q), set()):
                        desired.update(('__new__', '__init__'))
                    if desired:
                        self.expand_class(f, qual, n, desired, depth, provenance)
            else:
                lines = set(range(g._start(n), n.end_lineno + 1)) | idx.context_lines(n)
                outcome = self.add_lines(f, lines, depth, origin, qual, provenance)
            result = {**x, 'target': qual, 'outcome': outcome}
        else:
            outcome = self.module(f, line, depth, origin, provenance)
            result = {**x, 'outcome': outcome}
        if outcome == 'added':
            prior_admitted.add(package)
        if outcome != 'source_budget':
            self.transfer(q, x, members, depth)
        return result

    def consume(self, q, raw):
        phase, number = 'additional', len(self.query_records) + 1
        items = self.register_native(q, raw, phase, number)
        if not raw:
            return [{'outcome': 'empty'}]
        locations, window, omitted = self.native_window(q, raw)
        result = [x for x in locations if 'file' not in x]
        if omitted:
            result.append({'outcome': 'fanout_budget', 'uninspected_native_locations': omitted})
        ranked = sorted([(self.rank(q, x), x, provenance) for x, _, _, provenance in items], key=lambda t: t[0][0])
        candidates, visited, admitted = [], set(), 0
        for (score, role, qual), x, provenance in ranked:
            package = (x['file'], qual) if qual else (x['file'], 'module', x['line'])
            row = {**x, 'role': role, 'rank': list(score), 'package': list(package)}
            if package in visited:
                outcome = {**x, 'outcome': 'duplicate_target'}
            elif admitted >= MAX_NATIVE_TARGETS:
                outcome = {**x, 'outcome': 'fanout_budget'}
            else:
                visited.add(package)
                outcome = self.admit_target(q, x, provenance, self.effective_needs(_qkey(q)))
                admitted += outcome['outcome'] == 'added'
            row['outcome'] = outcome['outcome']
            candidates.append(row)
            result.append(outcome)
        self.candidate_rankings.append({'query_index': number, 'query_key': list(_qkey(q)),
                                         'candidate_window': copy.deepcopy(window), 'ranked_candidates': candidates,
                                         'admitted_packages': admitted, 'uninspected_native_locations': omitted})
        return result

    def packet(self):
        packet = super().packet()
        packet['audit'].update(policy='preserved_symbol_graph_v1', mode='H',
            symbol_graph={'schema': 1, 'semantics': 'native positional occurrence links plus explicit lexical search hints; not runtime or dataflow proof',
                          'nodes': list(self.graph_nodes.values()), 'edges': self.graph_edges,
                          'seed_native_records': self.seed_native_records, 'snapshots': dict(sorted(self.snapshot_hashes.items())),
                          'canonical_symbols': [{'symbol': symbol, 'occurrences': [list(k) for k in sorted(anchors)],
                                                 'wanted_members': sorted(self.symbol_members.get(symbol, ()))}
                                                for symbol, anchors in sorted(self.symbol_anchors.items())],
                          'canonical_closures': self.canonical_events,
                          'heuristics': ['Python protocol-call/iteration/subscription demand', 'literal annotation/assignment closure',
                                         'native class direct-member and base-expression frontier', 'relation-role/missing-body candidate ranking']},
            graph_frontier=self.graph_frontier, candidate_rankings=self.candidate_rankings)
        packet['audit']['depth_semantics'] = 'Inherited G limit: query-origin depth <=3; the returned package can be depth 4 and cannot initiate a depth-4 native query.'
        packet['audit']['native_admission_totals'] = [
            {'phase': phase, 'query_index': number, 'packages': [list(x) for x in sorted(packages, key=str)]}
            for (phase, number), packages in sorted(self.native_admissions.items())]
        return packet


def followup(root, base_root, seed, scope, requests, agentic=True, review=None):
    nav, client = None, None
    try:
        nav = Navigator(root, base_root, seed, scope, agentic)
        nav.cached_module_gaps()
        nav.deleted_bootstrap()
        nav.generic()
        if agentic:
            nav.add_requests(requests)
            nav.add_declared_gaps(review)
        client = Client(nav.root)
        nav.run(client)
        packet = nav.packet()
    except Exception as exc:
        # Harness keeps preparation failures in the denominator. Preserve the
        # actual trace even if packet serialization/packaging raises; no retry.
        exc.partial_trace = getattr(client, 'trace', [])
        exc.initialization = getattr(client, 'initialization', None)
        exc.stderr = getattr(client, 'errors', [])
        exc.partial_queries = nav.query_records if nav is not None else []
        raise
    finally:
        if client is not None:
            client.close()
    return {'packet': packet, 'actual_trace': client.trace,
            'initialization': client.initialization, 'stderr': client.errors}
