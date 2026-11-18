"""Bounded source closure over actual Pyright definition/reference locations.

AST use is restricted to lexical packaging, inventory and query coordinates. It
does not resolve cross-file symbols. Full-function deletion retains the sealed
policy's base-only behavior. No benchmark labels, witnesses or case IDs enter
this module.
"""
import ast
import builtins
import heapq
import importlib.util
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
_name = 'closure_sealed_policy'
if _name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _name, ROOT / 'runs/lsp_linked_adaptive_20261023/policy.py')
    _old = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _old
    _spec.loader.exec_module(_old)
else:
    _old = sys.modules[_name]

Client = _old.Client
SOURCE_CAP, OBS_CAP, QUERY_CAP, FIRST_QUERIES, DEPTH = 5200, 800, 12, 6, 3
digest, token_count = _old.digest, _old.token_count
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
DECLARATIONS = (ast.ClassDef, *FUNCTIONS)


def _start(node):
    return min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])


def _header(node):
    """Literal declaration/control header; a one-line suite stays on its line."""
    body = getattr(node, 'body', [])
    stop = body[0].lineno if body else node.lineno + 1
    return set(range(_start(node), max(node.lineno + 1, stop)))


def _bound_names(target):
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [s for child in target.elts for s in _bound_names(child)]
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    return []


def _clause_lines(src, suite):
    """Keep the literal else/finally/case clause preceding a child suite."""
    result = set()
    if not suite:
        return result
    rows = src.splitlines()
    previous = suite[0].lineno - 1
    while previous >= 1:
        raw = rows[previous - 1]
        if not raw.strip() or raw.lstrip().startswith('#'):
            previous -= 1
            continue
        if raw.strip().startswith(('else:', 'finally:', 'elif ', 'case ')):
            result.add(previous)
        break
    return result


def class_inventory(src, file, qualname, node):
    """Exhaustively enumerate explicit lexical member declarations/bindings.

    A declaration in a conditional/loop/try suite is marked conditional, not
    promoted to an unconditional runtime member. Nested definition bodies are
    not walked. No claim is made about inherited or dynamically added members.
    """
    entries, headers, attributes, guards, unsupported = [], set(_header(node)), set(), [], []

    def note_special_binding(expr):
        if isinstance(expr, ast.AST):
            bindings = [x for x in ast.walk(expr) if isinstance(x, ast.NamedExpr)]
            if bindings:
                unsupported.append({'kind': 'assignment_expression_scope_not_summarized', 'line': getattr(expr, 'lineno', bindings[0].lineno)})

    def entry(n, kind, names, conditions):
        if names:
            entries.append({'kind': kind, 'names': names, 'line': n.lineno,
                            'end_line': n.end_lineno, 'conditions': conditions})

    def visit(nodes, conditions=()):
        for n in nodes:
            cond = list(conditions)
            if isinstance(n, DECLARATIONS):
                entry(n, 'class' if isinstance(n, ast.ClassDef) else 'method', [n.name], cond)
                headers.update(_header(n))
                for field, value in ast.iter_fields(n):
                    if field == 'body':
                        continue
                    for val in value if isinstance(value, list) else [value]:
                        note_special_binding(val)
                continue
            if isinstance(n, (ast.Global, ast.Nonlocal)):
                unsupported.append({'kind': type(n).__name__, 'line': n.lineno, 'names': n.names})
            if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                entry(n, type(n).__name__, [x for t in targets for x in _bound_names(t)], cond)
                attributes.update(range(n.lineno, n.end_lineno + 1))
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                names = [a.asname or (a.name.split('.')[0] if isinstance(n, ast.Import) else a.name) for a in n.names]
                entry(n, type(n).__name__, names, cond)
                attributes.update(range(n.lineno, n.end_lineno + 1))
            elif isinstance(n, (ast.For, ast.AsyncFor)):
                entry(n, type(n).__name__, _bound_names(n.target), cond + [f'loop@{n.lineno}'])
            elif isinstance(n, (ast.With, ast.AsyncWith)):
                entry(n, type(n).__name__, [x for it in n.items if it.optional_vars for x in _bound_names(it.optional_vars)], cond)
            elif isinstance(n, ast.Delete):
                entry(n, 'delete', [x for t in n.targets for x in _bound_names(t)], cond)
            # Assignment expressions in this statement's immediate expression
            # fields are explicit bindings too; never inspect child suites or
            # nested function/class bodies here.
            for field, value in ast.iter_fields(n):
                if field in {'body', 'orelse', 'finalbody', 'handlers', 'cases'}:
                    continue
                vals = value if isinstance(value, list) else [value]
                for val in vals:
                    if isinstance(val, ast.expr):
                        note_special_binding(val)
            child_suites = []
            for field in ('body', 'orelse', 'finalbody'):
                value = getattr(n, field, None)
                if isinstance(value, list) and value:
                    child_suites.append((field, value))
            if child_suites or isinstance(n, (ast.Try, getattr(ast, 'TryStar', ast.Try), ast.Match)):
                headers.update(_header(n))
                guards.append({'kind': type(n).__name__, 'line': n.lineno, 'end_line': n.end_lineno})
            for field, values in child_suites:
                if field in {'orelse', 'finalbody'}:
                    headers.update(_clause_lines(src, values))
                visit(values, conditions + (f'{type(n).__name__}.{field}@{n.lineno}',))
            if isinstance(n, (ast.Try, getattr(ast, 'TryStar', ast.Try))):
                for handler in n.handlers:
                    headers.update(_header(handler))
                    if handler.name:
                        entry(handler, 'except_binding', [handler.name], cond + [f'except@{handler.lineno}'])
                    visit(handler.body, conditions + (f'except@{handler.lineno}',))
            if isinstance(n, ast.Match):
                for case in n.cases:
                    names = [x.name for x in ast.walk(case.pattern)
                             if isinstance(x, (ast.MatchAs, ast.MatchStar)) and x.name]
                    names += [x.rest for x in ast.walk(case.pattern) if isinstance(x, ast.MatchMapping) and x.rest]
                    entry(case.pattern, 'match_binding', names, cond + [f'match@{case.pattern.lineno}'])
                    headers.update(range(case.pattern.lineno, max(case.pattern.lineno + 1, case.body[0].lineno)))
                    visit(case.body, conditions + (f'match@{case.pattern.lineno}',))

    visit(node.body)
    raw = '\n'.join(src.splitlines()[node.lineno - 1:node.end_lineno])
    return {'evidence_id': 'E-CLASS-' + digest(file + '::' + qualname)[:10],
            'file': file, 'qualname': qualname, 'line': node.lineno,
            'end_line': node.end_lineno, 'full_class_source_sha256': digest(raw),
            'full_file_source_sha256': digest(src), 'bases': [ast.unparse(x) for x in node.bases],
            'keywords': [ast.unparse(x) for x in node.keywords],
            'decorators': [ast.unparse(x) for x in node.decorator_list],
            'complete_explicit_lexical_inventory': not unsupported, 'entries': entries,
            'unsupported_binding_syntax': unsupported,
            'control_suites': guards, 'signature_lines': sorted(headers),
            'attribute_lines': sorted(attributes),
            'limitations': 'Explicit lexical declarations and binding statements only. Conditional entries may not execute; inherited, metaclass, decorator, exec, setattr and other dynamic members are unknown. Method bodies are omitted unless their numbered lines are separately supplied.'}


class Navigator(_old.Navigator):
    def __init__(self, base, head, root, scope):
        self.base, self.head, self.root, self.scope = base, head, Path(root), scope
        self.defs, self.owners, self.trees = _old.index(head)
        self.bd, self.bo, self.bt = _old.index(base)
        self.roots, self.deleted, self.root_events = _old.changed_roots(base, head, self.bd, self.defs)
        self.lines, self.base_lines = {}, {}
        self.admitted, self.blocked, self.queue, self.queries, self.requests, self.bindings = [], [], [], [], [], []
        self.seq, self.queued, self.seen = 0, set(), set()
        self.class_inventories, self.module_sites, self.constructor_components = [], [], []
        self._inventory_cache, self._parents_cache = {}, {}
        self._module_queued, self._class_query_queued, self._attribute_queued = set(), set(), set()
        self.skipped_queries = []
        words = set(re.findall(r'[a-z_]\w*', scope.lower()))
        self.roots.sort(key=lambda t: (-len(words & set(re.findall(r'[a-z_]\w*', (t[1] + ' ' + ast.unparse(self.defs[t])).lower()))), t))
        # Admit every original root before optional closure; closure must not
        # crowd out another root's full changed function.
        for t in self.deleted:
            self._core(t, 'deleted_base', base=True)
        accepted = [t for t in self.roots if self._core(t, 'changed_root')]
        for t in accepted:
            self._owner_inventory(t)
            self.enqueue(t, 0)

    def _inventory_text(self, inv):
        items = []
        for e in inv['entries']:
            condition = '?' if e['conditions'] else ''
            items.append(f"{condition}{e['kind']} {','.join(e['names'])}@{e['line']}")
        completeness = 'Complete explicit lexical member inventory' if inv['complete_explicit_lexical_inventory'] else 'Partial lexical declarations/binding statements; assignment-expression or global/nonlocal scope is unresolved'
        return (f"[{inv['evidence_id']}] head {inv['file']}::{inv['qualname']} class span {inv['line']}-{inv['end_line']}. "
                f"{completeness}: {'; '.join(items) or '(none)'}. "
                "? marks conditional bindings. Inherited and dynamic members are unknown. "
                "Method bodies are omitted unless numbered source lines are supplied. "
                "Class attribute statements may be separately omitted by the source cap; this list is not their value evidence.")

    def source(self):
        text = _old.render(self.base, self.base_lines, 'base') + '\n' + _old.render(self.head, self.lines)
        if self.class_inventories:
            text += '\n' + '\n'.join(self._inventory_text(x) for x in self.class_inventories)
        return text

    def _add_lines(self, file, add, key, origin, base=False, kind='source'):
        target = self.base_lines if base else self.lines
        before = set(target.get(file, set()))
        target[file] = before | set(add)
        if token_count(self.source()) > SOURCE_CAP:
            target[file] = before
            self.blocked.append({'key': key, 'origin': origin, 'kind': kind,
                                 'reason': 'whole component exceeds source cap', 'requested_lines': sorted(add)})
            return False
        return True

    def _core(self, t, origin, base=False):
        key = ('base' if base else 'head', *t)
        if any(tuple(a['key']) == key for a in self.admitted):
            return True
        src, defs, owners, trees = (self.base, self.bd, self.bo, self.bt) if base else (self.head, self.defs, self.owners, self.trees)
        add = _old.package(src, defs, owners, trees, t)
        if not self._add_lines(t[0], add, key, origin, base):
            return False
        self.admitted.append({'key': key, 'origin': origin, 'lines': sorted(add), 'component': 'whole_function_or_legacy_base_package'})
        return True

    def _inventory(self, t):
        if t not in self._inventory_cache:
            self._inventory_cache[t] = class_inventory(self.head[t[0]], t[0], t[1], self.defs[t])
        return self._inventory_cache[t]

    def _admit_inventory(self, t):
        if any((x['file'], x['qualname']) == t for x in self.class_inventories):
            return True
        inv = self._inventory(t)
        before = set(self.lines.get(t[0], set()))
        self.lines[t[0]] = before | set(inv['signature_lines'])
        self.class_inventories.append(inv)
        if token_count(self.source()) > SOURCE_CAP:
            self.lines[t[0]] = before
            self.class_inventories.pop()
            self.blocked.append({'key': ('head', *t), 'origin': 'lexical_class_inventory',
                                 'reason': 'whole inventory and signatures exceed source cap'})
            return False
        # Full attribute expressions are indivisible components. Inventory
        # remains useful when an exceptionally large value cannot fit.
        n = self.defs[t]
        member_stmts = []
        def walk_suite(nodes):
            for s in nodes:
                if isinstance(s, DECLARATIONS):
                    continue
                if isinstance(s, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Import, ast.ImportFrom)):
                    member_stmts.append(s)
                for field in ('body', 'orelse', 'finalbody'):
                    values = getattr(s, field, None)
                    if isinstance(values, list):
                        walk_suite(values)
                if isinstance(s, (ast.Try, getattr(ast, 'TryStar', ast.Try))):
                    for h in s.handlers:
                        walk_suite(h.body)
                if isinstance(s, ast.Match):
                    for c in s.cases:
                        walk_suite(c.body)
        walk_suite(n.body)
        for stmt in member_stmts:
            self._add_lines(t[0], set(range(stmt.lineno, stmt.end_lineno + 1)), ('head', *t),
                            'lexical_class_attribute', kind='class_attribute_statement')
        return True

    def _owner_inventory(self, t):
        for owner in self.owners[t]:
            if isinstance(owner, ast.ClassDef):
                target = next((u for u, n in self.defs.items() if u[0] == t[0] and n is owner), None)
                if target:
                    self._admit_inventory(target)

    def admit(self, t, origin, base=False):
        if base:
            return self._core(t, origin, base=True)
        n = self.defs[t]
        if not isinstance(n, ast.ClassDef):
            ok = self._core(t, origin)
            if ok:
                self._owner_inventory(t)
            return ok
        key = ('head', *t)
        if not any(tuple(a['key']) == key for a in self.admitted):
            # The lexical inventory is a complete unit; a header alone remains
            # admissible if the complete inventory is too large.
            self._owner_inventory(t)
            inv_ok = self._admit_inventory(t)
            add = _header(n)
            if not inv_ok and not self._add_lines(t[0], add, key, origin):
                return False
            self.admitted.append({'key': key, 'origin': origin, 'lines': sorted(add), 'component': 'class_header', 'inventory_supplied': inv_ok})
        # Native class resolution is the sole trigger for constructor bodies.
        # Preserve an explicit omission when any full body cannot fit.
        if origin.startswith('native_'):
            methods = [(u, d) for u, d in self.defs.items()
                       if u[0] == t[0] and self.owners[u] and self.owners[u][-1] is n
                       and isinstance(d, FUNCTIONS) and d.name in {'__new__', '__init__', '__call__'}]
            methods.sort(key=lambda x: ({'__new__': 0, '__init__': 1, '__call__': 2}[x[1].name], x[1].lineno))
            for member, method in methods:
                if any(tuple(x['member']) == member for x in self.constructor_components):
                    continue
                ok = self._core(member, 'native_class_constructor')
                self.constructor_components.append({'class': t, 'member': member, 'supplied': ok,
                                                    'reason': 'full body supplied' if ok else 'whole body exceeds source cap'})
        return True

    def _decl_position(self, t):
        n = self.defs[t]
        row = self.head[t[0]].splitlines()[n.lineno - 1]
        match = re.search(r'\b(?:def|class)\s+(' + re.escape(n.name) + r')\b', row)
        if match:
            return n.lineno - 1, len(row[:match.start(1)].encode('utf-16-le')) // 2
        return None

    def enqueue(self, t, depth):
        if t in self.queued or depth > DEPTH:
            return
        super().enqueue(t, depth)
        f, _ = t
        n = self.defs[t]
        # An owning class is only a same-file lexical association. Ask Pyright
        # for its definition before using class-target constructor expansion.
        for owner in reversed(self.owners[t]):
            if isinstance(owner, ast.ClassDef):
                u = next((k for k, v in self.defs.items() if k[0] == f and v is owner), None)
                pos = self._decl_position(u) if u else None
                if pos and u not in self._class_query_queued:
                    self._class_query_queued.add(u)
                    self.push('definition', f, *pos, owner.name, depth, (0, -2, pos[0]), 'lexical_owner_class_native_definition')
                    self.push('references', f, *pos, owner.name, depth, (1, -1, pos[0]), 'lexical_owner_class_native_references')
                break
        for attr in ast.walk(n):
            if not isinstance(attr, ast.Attribute):
                continue
            ln, ch = _old.position(self.head[f], attr)
            if ln + 1 not in self.lines.get(f, set()):
                continue
            changed = depth == 0 and any(e['file'] == f and e['head_lines'][0] <= attr.lineno <= e['head_lines'][1] for e in self.root_events)
            # Changed writes prioritize consumers. Changed loads prioritize
            # the defining attribute. Further supplied constructors/functions
            # can expose attribute links at a lower deterministic priority.
            method = 'references' if isinstance(attr.ctx, (ast.Store, ast.Del)) else 'definition'
            signature = (method, f, ast.unparse(attr))
            if signature in self._attribute_queued:
                continue
            self._attribute_queued.add(signature)
            priority = (0, -3 if method == 'references' else -1, ln) if changed else (2, 1, ln)
            self.push(method, f, ln, ch, ast.unparse(attr), depth, priority, 'attribute_consumers' if method == 'references' else 'attribute_definition')

    def _parents(self, f):
        if f not in self._parents_cache:
            self._parents_cache[f] = {id(child): parent for parent in ast.walk(self.trees[f]) for child in ast.iter_child_nodes(parent)}
        return self._parents_cache[f]

    def _module_statement(self, f, line):
        choices = [n for n in ast.walk(self.trees[f]) if isinstance(n, ast.stmt)
                   and n.lineno <= line <= n.end_lineno and not isinstance(n, DECLARATIONS)]
        if not choices:
            return None
        node = min(choices, key=lambda x: (x.end_lineno - x.lineno, x.end_col_offset - x.col_offset))
        parents = self._parents(f)
        cur, ancestors = node, []
        while id(cur) in parents:
            cur = parents[id(cur)]
            if isinstance(cur, DECLARATIONS):
                return None
            if isinstance(cur, (ast.stmt, ast.ExceptHandler, ast.match_case)):
                ancestors.append(cur)
        return node, ancestors

    def _module_identifiers(self, f, node):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != '*':
                    line = alias.lineno - 1
                    row = self.head[f].splitlines()[line]
                    prefix = row.encode()[:alias.col_offset].decode()
                    yield 'definition', line, len(prefix.encode('utf-16-le')) // 2, alias.name
                    if alias.asname:
                        # Pyright distinguishes an explicitly renamed import
                        # from its source declaration. Ask native references
                        # on the local binding to follow assignment/re-export
                        # sites instead of guessing them from matching names.
                        endrow = self.head[f].splitlines()[alias.end_lineno - 1]
                        endprefix = endrow.encode()[:alias.end_col_offset].decode()
                        localprefix = endprefix[:-len(alias.asname)]
                        yield 'references', alias.end_lineno - 1, len(localprefix.encode('utf-16-le')) // 2, alias.asname
            return
        # The runtime value of an assignment is not assumed. Follow each
        # syntactic load through a separate native definition request.
        for expr in ast.walk(node):
            if isinstance(expr, (ast.Name, ast.Attribute)) and isinstance(expr.ctx, ast.Load):
                if isinstance(expr, ast.Name) and expr.id in vars(builtins):
                    continue
                ln, ch = _old.position(self.head[f], expr)
                yield 'definition', ln, ch, ast.unparse(expr)

    def _admit_module(self, f, line, method, depth):
        found = self._module_statement(f, line)
        if not found:
            return None
        node, guards = found
        key = ('head', f, f'<module:{node.lineno}-{node.end_lineno}>')
        if not any(tuple(x['key']) == key for x in self.admitted):
            add = set(range(node.lineno, node.end_lineno + 1))
            for guard in guards:
                if isinstance(guard, ast.match_case):
                    add.update(range(guard.pattern.lineno, max(guard.pattern.lineno + 1, guard.body[0].lineno)))
                    continue
                add.update(_header(guard))
                # For else/finally suites preserve the literal intervening
                # clause line as well, not a reconstructed condition.
                for field in ('orelse', 'finalbody'):
                    suite = getattr(guard, field, [])
                    if suite and suite[0].lineno <= node.lineno <= suite[-1].end_lineno:
                        first = suite[0].lineno
                        previous = first - 1
                        while previous >= guard.lineno:
                            raw = self.head[f].splitlines()[previous - 1]
                            add.add(previous)
                            if raw.strip().startswith(('else:', 'finally:', 'elif ')):
                                break
                            if raw.strip() and not raw.lstrip().startswith('#'):
                                break
                            previous -= 1
            if not self._add_lines(f, add, key, 'native_' + method, kind='module_statement_and_guards'):
                return None
            record = {'key': key, 'origin': 'native_' + method, 'lines': sorted(add), 'component': 'module_statement'}
            self.admitted.append(record)
            self.module_sites.append({'file': f, 'native_line': line, 'statement_start': node.lineno,
                                      'statement_end': node.end_lineno, 'statement_kind': type(node).__name__,
                                      'source_sha256': digest('\n'.join(self.head[f].splitlines()[node.lineno - 1:node.end_lineno])),
                                      'guards': [{'kind': type(g).__name__, 'line': g.pattern.lineno if isinstance(g, ast.match_case) else g.lineno,
                                                  'end_line': g.body[-1].end_lineno if isinstance(g, ast.match_case) else g.end_lineno} for g in guards],
                                      'lines': sorted(add), 'runtime_binding': 'unknown'})
        site = (f, node.lineno, node.end_lineno)
        if site not in self._module_queued:
            self._module_queued.add(site)
            for operation, ln, ch, symbol in self._module_identifiers(f, node):
                if ln + 1 in self.lines.get(f, set()):
                    self.push(operation, f, ln, ch, symbol, depth, (0, -1, ln), 'native_module_binding_' + operation)
        return (f, key[-1])

    def advance(self, client, limit):
        assert limit <= QUERY_CAP
        while self.queue and len(self.queries) < limit:
            pri, seq, method, f, ln, ch, symbol, depth, origin = heapq.heappop(self.queue)
            key = (method, f, ln, ch)
            if key in self.seen:
                continue
            if ln + 1 not in self.lines.get(f, set()):
                self.skipped_queries.append({'operation': method, 'file': f, 'line': ln + 1,
                                             'origin': origin, 'reason': 'query position not in supplied head source'})
                continue
            self.seen.add(key)
            raw, targets, excluded, sites, error = None, [], [], [], None
            try:
                raw = getattr(client, method)(f, ln, ch)
                for loc in raw or []:
                    uri = loc.get('uri', loc.get('targetUri'))
                    rng = loc.get('range', loc.get('targetSelectionRange'))
                    try:
                        tf = str(Path(unquote(urlparse(uri).path)).relative_to(client.root))
                    except (TypeError, ValueError):
                        excluded.append('outside workspace')
                        continue
                    if tf not in self.head:
                        excluded.append('outside production corpus')
                        continue
                    tl = rng['start']['line'] + 1
                    t = self.enclosing(tf, tl)
                    sites.append((tf, tl - 1, rng['start']['character']))
                    if t:
                        targets.append(t)
                        if self.admit(t, 'native_' + method):
                            self.enqueue(t, depth + 1)
                        if method == 'definition':
                            self.callback(t, f, ln, ch, depth + 1)
                    else:
                        target = self._admit_module(tf, tl, method, depth + 1)
                        if target:
                            targets.append(target)
                        else:
                            excluded.append({'file': tf, 'line': tl,
                                             'reason': 'module statement unavailable or exceeds source cap'})
                if method == 'references':
                    t = self.enclosing(f, ln + 1)
                    if t:
                        for tf, tl, tc in sites:
                            self.callback(t, tf, tl, tc, depth + 1)
            except Exception as exc:
                error = repr(exc)
            self.queries.append({'operation': method, 'file': f, 'line': ln + 1,
                                 'character_utf16': ch, 'symbol': symbol, 'depth': depth,
                                 'origin': origin, 'targets': list(dict.fromkeys(targets)),
                                 'excluded': excluded, 'error': error, 'raw': raw})

    def audit(self):
        result = super().audit()
        result.update(policy='native_context_closure_v1', class_inventories=self.class_inventories,
                      module_sites=self.module_sites, constructor_components=self.constructor_components,
                      skipped_queries=self.skipped_queries,
                      inventory_evidence_ids=[x['evidence_id'] for x in self.class_inventories],
                      cross_file_resolution='actual native definition/references only',
                      deletion_fallback='unchanged base-only for fully deleted functions')
        return result
