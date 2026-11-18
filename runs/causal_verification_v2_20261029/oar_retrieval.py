"""B policy: path-scoped lookups and budgeted acquisition of complete Python source units (V3).

Two parts.

1. Pure helpers (no product import): ``Scope`` (a request path as an exact file or a directory prefix compared by path
   components, never by raw string prefix), ``SymbolQuery`` (``Class.method`` / ``module.func`` owner qualifiers), and
   ``PythonUnits`` - a Python ``ast`` index of the frozen workspace files that names the complete source unit around a
   line: decorators + header + body of a function/method, the class-level assignments a method reads through
   ``self.``/``cls.``/``Owner.``, a module/class-level assignment statement, or a class header with its class-level
   values. Ranges come from ``ast`` ``lineno``/``end_lineno`` only (no estimated windows). AST is bookkeeping, never a
   native resolution: anything it selects is labelled ``python-ast``.

2. ``make_b_registry_class(product, base)``: a subclass of the pilot registry used only by the B/CB arms.
   - symbol_search / go_to_definition / find_references / find_implementations / incoming_calls / outgoing_calls /
     grep_literal honour ``request.path`` as a scope and ``Owner.name`` qualifiers BEFORE anything is rendered or cut by
     the metadata allocation. When the product backend would ignore the scope or truncate globally first (grep's 50-hit
     cap, the structural index that only loads files from that capped grep), the lookup indexes/scans the scope itself.
     Scope selects the symbol/anchor; native references to that anchor may legitimately be in other files. Structural
     same-name references remain explicitly ambiguous even when mixed with native rows. Invalid explicit scopes fail.
   - go_to_definition declarations are complete AST units of the structural targets (at most four, like the product's
     existing declaration bodies). Native-only targets are separately charged reads, never extra free declarations.
     A unit longer than the product's 200-line read limit is never shown cut: it is reported as not provided.
   - After the batch's own requests ran, locator results (definition / references / implementations / calls / symbol
     rows / grep hits) are mapped to their enclosing function or method. Each unit that is not already shown is read
     with a REAL product ``read_lines`` call that is charged as one more query of the same batch, only while that
     batch still has a free slot (3 per batch, 6 in total, pre-gap included). ``plan_requests`` reserves at most one
     slot before product deduplication, for eligible queued work or a prospective locator result. Queued work is a real
     read_lines request and can progress without unrelated model requests. Otherwise it is queued for a later batch
     or reported blocked (source budget / read limit). Units that were not provided are listed in one
     metadata note per batch (charged to the cumulative metadata allocation). Nothing is read without being charged.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import posixpath
import re
from pathlib import Path
from typing import Optional

import oar_core as oc

READ_MAX_LINES = 200            # = product ReviewConfig.agentic_read_max_lines (one read_lines call)
MAX_UNITS_PER_LOCATOR = 3       # closure units considered per locator request (body + class values count separately)
MAX_DECLARATIONS = 4            # = product _definition_evidence: declarations of at most 4 symbols
SCOPED_OPERATIONS = ('symbol_search', 'go_to_definition', 'find_references', 'find_implementations',
                     'incoming_calls', 'outgoing_calls', 'grep_literal')
CLOSURE_OPERATIONS = ('go_to_definition', 'find_references', 'find_implementations', 'incoming_calls',
                      'outgoing_calls', 'symbol_search', 'grep_literal')
AUTO_ORIGIN = 'oar-b-closure'


# ============================================================ scope
@dataclasses.dataclass(frozen=True)
class Scope:
    raw: Optional[str]
    kind: str                 # none | file | dir | not_found | invalid
    path: str = ''

    @property
    def active(self) -> bool:
        return self.kind in ('file', 'dir')

    def contains(self, rel: str) -> bool:
        """Component-wise containment. ``pkg/a`` contains ``pkg/a/x.py`` but never ``pkg/another.py``."""
        if not self.active:
            return self.kind == 'none'
        rel = (rel or '').replace('\\', '/')
        if self.kind == 'file':
            return rel == self.path
        return rel.startswith(self.path + '/')

    def describe(self) -> str:
        if self.kind == 'file':
            return f'exact file {self.path}'
        if self.kind == 'dir':
            return f'directory {self.path}/'
        if self.kind == 'none':
            return 'none'
        return f'{self.raw!r} {self.kind.replace("_", " ")} in the workspace; lookup refused'


def parse_scope(raw: Optional[str], root) -> Scope:
    if raw is None or not str(raw).strip():
        return Scope(raw, 'none')
    text = str(raw).strip().replace('\\', '/')
    while text.startswith('./'):
        text = text[2:]
    text = text.rstrip('/')
    if not text or text.startswith('/'):
        return Scope(raw, 'invalid')
    norm = posixpath.normpath(text)
    if norm == '.':
        return Scope(raw, 'none')
    if norm == '..' or norm.startswith('../'):
        return Scope(raw, 'invalid')
    root = Path(root).resolve()
    target = (root / norm).resolve()
    if not target.is_relative_to(root):
        return Scope(raw, 'invalid')
    if target.is_file():
        return Scope(raw, 'file', norm)
    if target.is_dir():
        return Scope(raw, 'dir', norm)
    return Scope(raw, 'not_found', norm)


# ============================================================ qualified queries
_DOTTED = re.compile(r'^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$')


@dataclasses.dataclass(frozen=True)
class SymbolQuery:
    raw: str
    name: str
    qualifiers: tuple = ()
    dropped: tuple = ()           # leading self/cls: a receiver, not an owner name

    def matches(self, qualname: Optional[tuple]) -> bool:
        if not self.qualifiers:
            return True
        if not qualname:
            return False          # owner unknown (non-Python or not found in the AST): not claimed as a match
        want = (*self.qualifiers, self.name)
        return len(qualname) >= len(want) and tuple(qualname[-len(want):]) == want

    def describe(self) -> str:
        if not self.qualifiers and not self.dropped:
            return repr(self.name)
        text = f'name {self.name!r}'
        if self.qualifiers:
            text += f' owned by {".".join(self.qualifiers)!r}'
        if self.dropped:
            text += f' (receiver {".".join(self.dropped)!r} ignored: its class is not known statically)'
        return text


def parse_symbol_query(raw: Optional[str]) -> SymbolQuery:
    text = (raw or '').strip()
    if not _DOTTED.match(text):
        return SymbolQuery(text, text)
    parts = text.split('.')
    quals, dropped = list(parts[:-1]), []
    if quals and quals[0] in ('self', 'cls'):
        dropped.append(quals.pop(0))
    return SymbolQuery(text, parts[-1], tuple(quals), tuple(dropped))


def module_parts(rel: str) -> tuple:
    rel = rel.replace('\\', '/')
    if not rel.endswith('.py'):
        return tuple(p for p in rel.split('/') if p)
    parts = rel[:-3].split('/')
    if parts and parts[-1] == '__init__':
        parts = parts[:-1]
    return tuple(p for p in parts if p)


# ============================================================ AST units
@dataclasses.dataclass(frozen=True)
class SourceUnit:
    path: str
    kind: str                 # function | method | class_values | statement | class_header
    name: str
    qualname: tuple
    runs: tuple               # ((start, end), ...), ascending, disjoint
    owner: Optional[str] = None
    owner_key: Optional[tuple] = None          # (class qualname, class def line) of a method's direct owner
    attr_refs: tuple = ()

    @property
    def start(self) -> int:
        return self.runs[0][0]

    @property
    def end(self) -> int:
        return self.runs[-1][1]

    @property
    def lines(self) -> list:
        return [n for s, e in self.runs for n in range(s, e + 1)]

    @property
    def span(self) -> int:
        return self.end - self.start + 1

    @property
    def key(self) -> tuple:
        return (self.path, self.runs)

    def label(self) -> str:
        qual = '.'.join(self.qualname[-2:]) if self.qualname else self.name
        kind = {'function': 'def', 'method': 'def', 'class_values': 'class values of', 'statement': 'statement',
                'class_header': 'class'}[self.kind]
        return f'{self.path}:{self.start}-{self.end} {kind} {qual}'

    def to_dict(self) -> dict:
        return {'path': self.path, 'kind': self.kind, 'name': self.name, 'qualname': list(self.qualname),
                'runs': [list(r) for r in self.runs], 'owner': self.owner, 'attr_refs': list(self.attr_refs)}


class UnitError(ValueError):
    pass


@dataclasses.dataclass
class _Func:
    name: str
    qualname: tuple
    start: int
    def_line: int
    header_end: int
    end: int
    owner: Optional[str]
    owner_key: Optional[tuple]
    attr_refs: tuple


@dataclasses.dataclass
class _Class:
    name: str
    qualname: tuple
    start: int
    def_line: int
    header_end: int
    end: int
    values: dict              # name -> [(start, end)] direct class-body assignments


@dataclasses.dataclass
class _FileIndex:
    functions: list
    classes: list
    statements: list          # (start, end, qualname-of-container) module/class-level assignment statements
    line_count: int


def _header_end(node) -> int:
    first = node.body[0].lineno if node.body else node.lineno
    return max(node.lineno, first - 1) if first > node.lineno else node.lineno


def _assigned_names(stmt) -> list:
    targets = []
    if isinstance(stmt, ast.Assign):
        targets = stmt.targets
    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        targets = [stmt.target]
    names = []
    for t in targets:
        for node in ast.walk(t):
            if isinstance(node, ast.Name):
                names.append(node.id)
    return names


class PythonUnits:
    """``ast`` index of workspace files (read-only, cached). Raw text is parsed only to find line ranges."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self._cache: dict = {}

    def index(self, rel: str) -> _FileIndex:
        if rel in self._cache:
            cached = self._cache[rel]
            if isinstance(cached, UnitError):
                raise cached
            return cached
        try:
            path = (self.root / rel).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise UnitError(f'{rel}: not a file in the workspace')
            if path.suffix != '.py':
                raise UnitError(f'{rel}: not a Python source')
            text = path.read_text(encoding='utf-8', errors='replace')
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError) as exc:
                raise UnitError(f'{rel}: python ast parse failed ({type(exc).__name__})') from exc
        except UnitError as exc:
            self._cache[rel] = exc
            raise
        result = self._build(rel, tree, len(text.splitlines()))
        self._cache[rel] = result
        return result

    def _build(self, rel, tree, line_count) -> _FileIndex:
        mod = module_parts(rel)
        functions, classes, statements = [], [], []

        def visit(node, chain: tuple, cls_info: Optional[_Class], direct_class: bool):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = min([d.lineno for d in child.decorator_list] + [child.lineno])
                    owner = cls_info if direct_class else None
                    refs = set()
                    if owner is not None:
                        receivers = {'self', 'cls', owner.name}
                        for sub in ast.walk(child):
                            if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                                    and sub.value.id in receivers):
                                refs.add(sub.attr)
                    functions.append(_Func(child.name, (*mod, *chain, child.name), start, child.lineno,
                                           _header_end(child), child.end_lineno, owner.name if owner else None,
                                           (owner.qualname, owner.def_line) if owner else None, tuple(sorted(refs))))
                    visit(child, (*chain, child.name), None, False)
                elif isinstance(child, ast.ClassDef):
                    start = min([d.lineno for d in child.decorator_list] + [child.lineno])
                    info = _Class(child.name, (*mod, *chain, child.name), start, child.lineno, _header_end(child),
                                  child.end_lineno, {})
                    for stmt in child.body:
                        if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                            for name in _assigned_names(stmt):
                                info.values.setdefault(name, []).append((stmt.lineno, stmt.end_lineno))
                            statements.append((stmt.lineno, stmt.end_lineno, info.qualname))
                    classes.append(info)
                    visit(child, (*chain, child.name), info, True)
                else:
                    if isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node is tree:
                        statements.append((child.lineno, child.end_lineno, mod))
                    visit(child, chain, cls_info, False)
        visit(tree, (), None, False)
        return _FileIndex(functions, classes, statements, line_count)

    # -- unit constructors
    def _func_unit(self, rel, f: _Func) -> SourceUnit:
        return SourceUnit(rel, 'method' if f.owner else 'function', f.name, f.qualname, ((f.start, f.end),),
                          f.owner, f.owner_key, f.attr_refs)

    def class_values_unit(self, unit: SourceUnit) -> Optional[SourceUnit]:
        """The class-level assignments of the method's direct owner that the method reads (bounded statements)."""
        if unit.kind != 'method' or not unit.attr_refs or unit.owner_key is None:
            return None
        idx = self.index(unit.path)
        owner = next((c for c in idx.classes if (c.qualname, c.def_line) == unit.owner_key), None)
        if owner is None:
            return None
        spans = sorted({span for name in unit.attr_refs for span in owner.values.get(name, [])})
        if not spans:
            return None
        return SourceUnit(unit.path, 'class_values', owner.name, owner.qualname, _merge_runs(spans), owner.name)

    def unit_at(self, rel: str, line: int, *, definition: bool = False) -> Optional[SourceUnit]:
        """The complete unit a location identifies.

        A line on a def's decorators/header -> that function. ``definition=True`` also accepts a class header (class
        header + its class-level assignments, never the methods) and a module/class-level assignment statement. Any
        other line -> the innermost enclosing function. Otherwise None (module-level code has no enclosing body).
        """
        idx = self.index(rel)
        headers = [f for f in idx.functions if f.start <= line <= f.header_end]
        if headers:
            return self._func_unit(rel, max(headers, key=lambda f: (f.start, f.def_line)))
        if definition:
            cls = [c for c in idx.classes if c.start <= line <= c.header_end]
            if cls:
                c = max(cls, key=lambda c: (c.start, c.def_line))
                spans = [(c.start, c.header_end)] + sorted({s for v in c.values.values() for s in v})
                return SourceUnit(rel, 'class_header', c.name, c.qualname, _merge_runs(spans))
        enclosing = [f for f in idx.functions if f.start <= line <= f.end]
        if enclosing:
            return self._func_unit(rel, max(enclosing, key=lambda f: (f.start, f.def_line)))
        if definition:
            stmts = [s for s in idx.statements if s[0] <= line <= s[1]]
            if stmts:
                s = max(stmts, key=lambda s: s[0])
                return SourceUnit(rel, 'statement', s[2][-1] if s[2] else rel, s[2], ((s[0], s[1]),))
        return None

    def qualname_at(self, rel: str, line: int, name: str) -> Optional[tuple]:
        """Qualified name (module parts + classes + name) of the def/class called ``name`` declared at ``line``."""
        try:
            idx = self.index(rel)
        except UnitError:
            return None
        hits = [f.qualname for f in idx.functions if f.name == name and f.start <= line <= f.header_end]
        hits += [c.qualname for c in idx.classes if c.name == name and c.start <= line <= c.header_end]
        if hits:
            return max(hits, key=len)
        return None


def _merge_runs(spans) -> tuple:
    out: list = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return tuple(tuple(r) for r in out)


# ============================================================ query accounting with charged automatic reads
def make_b_accounting_class(base, budget_error):
    class BQueryAccounting(base):
        """Primary requests as before; every automatic read is ONE more physical query of the SAME batch."""

        def record(self, label, requests):
            super().record(label, requests)
            batch = self.batches[-1]
            batch.update(primary=list(batch['requests']), auto=[], auto_locators=[])

        def free_slots(self) -> int:
            if not self.batches:
                return 0
            return max(0, min(self.limits['queries_per_batch'] - self.batches[-1]['count'],
                              self.limits['queries_total'] - self.total))

        def record_auto(self, label, request, locator_id):
            batch = self.batches[-1] if self.batches else None
            if batch is None or batch['label'] != label:
                raise budget_error(f'{label}: automatic read outside its batch')
            if batch['count'] + 1 > self.limits['queries_per_batch']:
                raise budget_error(f'{label}: automatic read would make {batch["count"] + 1} queries in one batch')
            if self.total + 1 > self.limits['queries_total']:
                raise budget_error(f'{label}: automatic read would make {self.total + 1} queries in total')
            self.total += 1
            batch['count'] += 1
            batch['running_total'] = self.total
            batch['requests'].append(request.id)
            batch['operations'].append(request.operation)
            batch['auto'].append(request.id)
            batch['auto_locators'].append(locator_id)
            # Charging a physical read does not mean its entire bounding range was exposed. The registry adds an
            # ordinary read key only after a contiguous, successfully admitted full-range source unit was shown.

    return BQueryAccounting


# ============================================================ registry subclass (B / CB only)
def make_b_registry_class(product, base):
    import oar_product_adapter as ad            # late import: the adapter imports this module

    ContextEvidence = product.models.ContextEvidence
    ContextRequest = product.models.ContextRequest
    reg = product.registry
    product_registry = reg.SafeToolRegistry

    class BRegistry(base):
        """Pilot registry + B: scoped lookups and charged complete-unit reads (see module docstring)."""

        b_enabled = True

        def __init__(self, root, **kw):
            super().__init__(root, **kw)
            self.units = PythonUnits(self.root)
            self.b_queue: list = []          # units waiting for a free slot of a later batch (FIFO)
            self.b_log: list = []            # every closure decision, per batch
            self.b_batches: list = []
            self._b_targets: dict = {}       # request id -> [target]
            self._b_scope_meta = None
            self._b_auto_seq: dict = {}
            self._b_planned: dict = {}       # scheduled real read request id -> exact queued unit
            self.b_plans: list = []

        # ---------------------------------------------------------------- scope / query helpers
        def _scope(self, request) -> Scope:
            return parse_scope(request.path, self.root)

        def _scope_meta(self, scope: Scope, query: Optional[SymbolQuery], total: int, kept: list, outside: list,
                        lookup: str) -> dict:
            files = sorted({getattr(s, 'path', None) or (s.get('path') if isinstance(s, dict) else '') for s in outside})
            # with an active scope only the scope is indexed/scanned: what lies outside was not searched (None), and a
            # count of whatever other lookups happened to index would be misleading
            searched_outside = not scope.active
            return {'requested_path': scope.raw, 'scope_kind': scope.kind, 'scope_path': scope.path,
                    'scope': scope.describe(), 'requested_query': query.raw if query else None,
                    'query': query.describe() if query else None, 'candidates_total': total, 'in_scope': len(kept),
                    'outside_scope_not_listed': len(outside) if searched_outside else None,
                    'outside_scope_files': files[:3] if searched_outside else [],
                    'outside_scope_file_count': len(files) if searched_outside else 0, 'lookup': lookup}

        def _in_scope_files_containing(self, scope: Scope, needle: str) -> list:
            """Real scoped lookup: in-scope workspace files whose text contains ``needle`` (no global hit cap)."""
            out = []
            for rel in self._all_paths:
                if not scope.contains(rel) or self._is_secret_path(Path(rel)):
                    continue
                path = self.root / rel
                try:
                    if path.stat().st_size > self.config.agentic_max_file_bytes:
                        continue
                    if needle in path.read_text(encoding='utf-8', errors='replace'):
                        out.append(rel)
                except OSError:
                    continue
            return out

        def _qualname(self, symbol) -> Optional[tuple]:
            return self.units.qualname_at(symbol.path, int(symbol.line_start), symbol.name)

        def _scoped_symbols(self, request):
            """(query, scope, selected structural symbols, outside, lookup). Filter BEFORE any slicing/rendering."""
            query = parse_symbol_query(request.query)
            name = self._structural_query(replace_query(request, query.name))
            scope = self._scope(request)
            if scope.kind in ('invalid', 'not_found'):
                raise reg.ToolDenied(f'invalid explicit lookup scope: {scope.describe()}')
            if scope.active:
                files = [scope.path] if scope.kind == 'file' else self._in_scope_files_containing(scope, name)
                self.code_index.ensure_paths(files)
                lookup = 'scoped_index(' + ('file' if scope.kind == 'file' else f'{len(files)} files in directory') + ')'
            else:
                self._ensure_query_candidates(name)
                lookup = 'product_index'
            symbols = self.code_index.search_symbols(name)
            kept, outside = [], []
            for s in symbols:
                ok = scope.contains(s.path) and query.matches(self._qualname(s) if query.qualifiers else None)
                (kept if ok else outside).append(s)
            # exact-case names first, then the product's own order (stable)
            kept.sort(key=lambda s: s.name != name)
            return query, scope, kept, outside, lookup, len(symbols)

        # ---------------------------------------------------------------- dispatch
        def _dispatch(self, request):
            if request.operation not in ad.PILOT_OPERATIONS:
                raise reg.ToolDenied(f'operation {request.operation} is not enabled in this pilot')
            if request.operation in SCOPED_OPERATIONS:
                scope = self._scope(request)
                if scope.kind in ('invalid', 'not_found'):
                    raise reg.ToolDenied(f'invalid explicit lookup scope: {scope.describe()}')
            handler = {'grep_literal': self._b_grep, 'go_to_definition': self._b_definition,
                       'find_references': self._b_references, 'find_implementations': self._b_relation,
                       'incoming_calls': self._b_relation, 'outgoing_calls': self._b_relation,
                       'symbol_search': self._b_symbol_search}.get(request.operation)
            if handler is None:
                return super()._dispatch(request)
            return handler(request)

        def _b_grep(self, request):
            query = (request.query or '').strip()
            scope = self._scope(request)
            if not scope.active:
                item = self._grep_literal(request)
                return replace_meta(item, b_scope=self._scope_meta(scope, None, item.metadata.get('matches', 0),
                                                                   [None] * item.metadata.get('matches', 0), [], 'product_scan'))
            if len(query) < 3 or len(query) > 120 or '\n' in query or '\r' in query:
                raise reg.ToolDenied('grep query는 줄바꿈 없이 3~120자여야 합니다.')
            saved = self._all_paths
            self._all_paths = [p for p in saved if scope.contains(p)]
            try:
                matches, hit_limit = self._scan_literal(query)       # the product scanner over the scope only
            finally:
                self._all_paths = saved
            content, truncated = self._limit_content('\n'.join(matches) if matches else '(no matches)')
            meta = self._scope_meta(scope, None, len(matches), matches, [], 'scoped_scan')
            return ContextEvidence(id=f'evidence-{request.id}', request_id=request.id, operation=request.operation,
                                   source=f'grep:{query}', content=content, truncated=truncated or hit_limit,
                                   metadata={'query': query, 'matches': len(matches), 'cache_hit': False, 'b_scope': meta})

        def _b_definition(self, request):
            query, scope, kept, outside, lookup, total = self._scoped_symbols(request)
            lsp_items = (self.lsp_resolver.go_to_definition(kept[0]) if kept and self.lsp_resolver is not None else [])
            declarations, seen = [], set()
            # V2 already bundled up to four STRUCTURAL declaration bodies in one definition operation. Preserve
            # that bound; native-only destinations are queued below and must pay for a separate product read_lines.
            targets = [(s.path, s.line_start, 'tree-sitter') for s in kept[:8]]
            for path, line, origin in targets:
                if len(declarations) >= MAX_DECLARATIONS:
                    break
                try:
                    unit = self.units.unit_at(path, int(line), definition=True)
                except UnitError as exc:
                    declarations.append({'path': path, 'line': line, 'provided': False, 'why': str(exc)[:120],
                                         'located_by': origin})
                    continue
                if unit is None or unit.key in seen:
                    continue
                seen.add(unit.key)
                row = {'path': unit.path, 'line_start': unit.start, 'line_end': unit.end,
                       'runs': [list(r) for r in unit.runs], 'unit': unit.to_dict(), 'located_by': origin,
                       'range_basis': 'python-ast', 'provided': unit.span <= READ_MAX_LINES}
                if row['provided']:
                    _, file_lines = self._safe_text(unit.path)
                    row['content'] = '\n'.join(f'{n}: {reg._redact_line(file_lines[n - 1])}'
                                               for n in unit.lines if n <= len(file_lines))
                else:
                    row['why'] = f'{unit.span} lines > {READ_MAX_LINES}-line read limit'
                declarations.append(row)
            payload = {'definitions': [dataclasses.asdict(s) for s in kept[:8]],
                       'lsp_locations': [dataclasses.asdict(l) for l in lsp_items[:8]], 'scip_locations': [],
                       'declarations': declarations}
            content, truncated = self._limit_content(json.dumps(payload, indent=2, ensure_ascii=False))
            sources = [s for s, items in ((lsp_items[0].source if lsp_items else 'lsp', lsp_items),
                                          ('tree-sitter', kept)) if items]
            return ContextEvidence(
                id=f'evidence-{request.id}', request_id=request.id, operation=request.operation,
                source=f'definition:{query.name}', content=content, truncated=truncated,
                metadata={'count': len(lsp_items) + len(kept), 'declaration_count': len(declarations),
                          'resolution_source': lsp_items[0].source if lsp_items else 'tree-sitter',
                          'resolution_sources': sources, 'fallback_used': bool(kept and not lsp_items),
                          'ambiguity_reasons': (['same-name or overload candidates were not semantically disambiguated']
                                                if len(kept) > 1 and not lsp_items else []),
                          'b_scope': self._scope_meta(scope, query, total, kept, outside, lookup)})

        def _safe_text(self, rel):
            path, rel_path = self._safe_file(rel)
            return rel_path, path.read_text(encoding='utf-8', errors='replace').splitlines()

        def _b_references(self, request):
            query, scope, kept, outside, lookup, total = self._scoped_symbols(request)
            lsp_items = self.lsp_resolver.find_references(kept[0]) if kept and self.lsp_resolver is not None else []
            structural = self.code_index.find_references(query.name)
            confirmed = [i for i in structural if i.inference != 'element_value']
            inferred = [i for i in structural if i.inference == 'element_value']
            groups = [('scip', []), (lsp_items[0].source if lsp_items else 'lsp', lsp_items), ('tree-sitter', confirmed)]
            sources = [s for s, items in groups if items]
            meta = self._scope_meta(scope, query, total, kept, outside, lookup)
            meta['reference_target'] = (f'{kept[0].path}:{kept[0].line_start} {kept[0].name}' if kept else None)
            locations = self._merge_locations(groups)             # LSP-confirmed rows first, structural-only after
            results = []
            if locations or not inferred:
                item = self._structural_evidence(
                    request, f'references:{query.name}', locations,
                    resolution_source=sources[0] if sources else 'tree-sitter', resolution_sources=sources,
                    fallback_used=bool(confirmed and not lsp_items),
                    ambiguity_reasons=(['structural rows are name matches (any symbol with this name), not resolved to '
                                        'the scoped target'] if confirmed else []))
                results.append(replace_meta(item, b_scope=meta))
            if inferred:
                item = self._structural_evidence(
                    request, f'references:{query.name}#inferred', self._merge_locations([('tree-sitter', inferred)]),
                    evidence_id=f'evidence-{request.id}-inferred', resolution_source='tree-sitter',
                    resolution_sources=['tree-sitter'], fallback_used=True,
                    ambiguity_reasons=[reg.ELEMENT_VALUE_AMBIGUITY_REASON])
                results.append(replace_meta(item, b_scope=meta))
            return results

        def _b_relation(self, request):
            query, scope, kept, outside, lookup, total = self._scoped_symbols(request)
            method = {'find_implementations': 'implementations', 'incoming_calls': 'incoming_calls',
                      'outgoing_calls': 'outgoing_calls'}[request.operation]
            lsp_items = (getattr(self.lsp_resolver, method)(kept[0])
                         if kept and self.lsp_resolver is not None and hasattr(self.lsp_resolver, method) else [])
            structural = self.code_index.find_references(query.name) if request.operation != 'outgoing_calls' else []
            sources = ([lsp_items[0].source] if lsp_items else []) + (['tree-sitter'] if structural else [])
            item = self._structural_evidence(
                request, f'{request.operation}:{query.name}', [*lsp_items, *structural],
                resolution_source=sources[0] if sources else 'tree-sitter', resolution_sources=sources,
                fallback_used=bool(structural and not lsp_items),
                ambiguity_reasons=(['structural candidates are name matches, not a typed call hierarchy or resolved '
                                    'binding to the scoped anchor'] if structural else []))
            meta = self._scope_meta(scope, query, total, kept, outside, lookup)
            meta['relation_target'] = f'{kept[0].path}:{kept[0].line_start} {kept[0].name}' if kept else None
            return replace_meta(item, b_scope=meta)

        def _b_symbol_search(self, request):
            query, scope, kept, outside, lookup, total = self._scoped_symbols(request)
            lsp_all = (self.lsp_resolver.workspace_symbols(query.name)
                       if self.lsp_resolver is not None and hasattr(self.lsp_resolver, 'workspace_symbols') else [])
            lsp_kept, lsp_out = [], []
            for s in lsp_all:
                ok = scope.contains(s.path) and query.matches(
                    self.units.qualname_at(s.path, int(s.line_start), s.name) if query.qualifiers else None)
                (lsp_kept if ok else lsp_out).append(s)
            item = self._merged_evidence(request, f'symbol:{query.name}', kept, lsp_kept)
            meta = self._scope_meta(scope, query, total + len(lsp_all), kept + lsp_kept, outside + lsp_out, lookup)
            return replace_meta(item, b_scope=meta)

        # ---------------------------------------------------------------- normalisation (scope header + targets)
        def _normalize(self, request, raw):
            self._b_scope_meta = next((r.metadata.get('b_scope') for r in raw if r.metadata.get('b_scope')), None)
            try:
                norm = super()._normalize(request, raw)
            finally:
                self._b_scope_meta = None
            if request.operation in CLOSURE_OPERATIONS:
                self._b_targets[request.id] = self._targets(request, raw, norm)
            return norm

        def _meta_text(self, header, rows, stub):
            meta = self._b_scope_meta
            if meta:
                extra = f'; scope={meta["scope"]}'
                if meta.get('query'):
                    extra += f'; query={meta["query"]}'
                if meta['scope_kind'] in ('file', 'dir') or meta.get('requested_query') and '.' in (meta.get('requested_query') or ''):
                    outside = meta.get('outside_scope_not_listed')
                    extra += (f'; in_scope={meta["in_scope"]}; outside_scope_not_listed='
                              + ('not searched' if outside is None else str(outside)))
                    if outside:
                        extra += f' (files: {", ".join(meta["outside_scope_files"])}'
                        extra += f' +{meta["outside_scope_file_count"] - 3} more)' if meta['outside_scope_file_count'] > 3 else ')'
                    extra += f'; lookup={meta["lookup"]}'
                header = header + extra
            return super()._meta_text(header, rows, stub)

        def _n_grep(self, request, raw):
            """V2 grep normalisation; with an active scope every element says which scope was scanned."""
            norm = super()._n_grep(request, raw)
            meta = raw[0].metadata.get('b_scope') if raw else None
            if meta and meta['scope_kind'] in ('file', 'dir'):
                norm.items[:] = [dataclasses.replace(i, source=f'{i.source} [scope {meta["scope"]}; scoped scan]')
                                 for i in norm.items]
            return norm

        def _n_definition(self, request, raw):
            """V2 rendering of positions; declarations are the complete AST units the scoped lookup returned."""
            item = raw[0]
            payload = self._json(item)
            sources = item.metadata.get('resolution_sources') or []
            lsp_items = payload.get('lsp_locations', [])
            native = bool(lsp_items) and any(ad.is_native(s) for s in sources)
            backend = next((s for s in sources if ad.is_native(s)), 'tree-sitter') if native else 'tree-sitter'
            rows = [f'definition {d["name"]} ({d["kind"]}) {d["path"]}:{d["line_start"]}-{d["line_end"]} '
                    f'visibility={d["visibility"]} [{d["source"]}]' for d in payload.get('definitions', [])]
            rows += [f'lsp_location {l["path"]}:{l["line_start"]}-{l["line_end"]} col {l["column_start"]}-{l["column_end"]} '
                     f'[{l["source"]}]' for l in lsp_items]
            rows += [f'declaration {d.get("path")}:{d.get("line_start", d.get("line"))}-{d.get("line_end", "")} '
                     f'not provided: {d.get("why")}' for d in payload.get('declarations', []) if not d.get('provided')]
            header = (f'go_to_definition {item.source.split(":", 1)[1]}: positions only (no code body in this element); '
                      f'resolution_sources={sources}; fallback_used={item.metadata.get("fallback_used")}; '
                      f'declaration ranges: python-ast units (decorators+header+body; class = header + class-level values)')
            text, omitted = self._meta_text(header, rows, f'go_to_definition {item.source.split(":", 1)[1]}')
            raw_id, final = self._ids(request)
            first = (payload.get('definitions') or lsp_items or [{}])[0]
            ev = oc.Evidence(final, 'head', first.get('path') or request.path or 'workspace', text=text,
                             provenance='native_lsp' if native else 'ast_fallback', content_kind='definition_location',
                             backend=backend, anchor_line=first.get('line_start'))
            items = [ContextEvidence(id=raw_id, request_id=request.id, operation=request.operation, source=item.source,
                                     content=text, metadata={'resolution_sources': sources})]
            core, kinds, notes = [ev], ['metadata'], [f'listing rows omitted: {omitted}']
            k = 0
            for decl in payload.get('declarations', []):
                if not decl.get('provided'):
                    continue
                k += 1
                wanted = [n for s, e in decl['runs'] for n in range(s, e + 1)]
                try:
                    masked = self.masked.lines(decl['path'])
                except ad.MaskError as exc:
                    notes.append(f'declaration body {decl["path"]}: {exc}')
                    continue
                lines = {n: masked[n - 1] for n in wanted if n <= len(masked)}
                if len(lines) != len(wanted):
                    notes.append(f'declaration {decl["path"]}:{decl["line_start"]}: unit beyond end of file; not provided')
                    continue
                unit = decl['unit']
                label = (f'declaration:{decl["path"]}:{decl["line_start"]}-{decl["line_end"]} '
                         f'[{unit["kind"]} {".".join(unit["qualname"][-2:])}; range python-ast]')
                it, dev = self._source_item(request, f'-d{k}', label, decl['path'], lines, 'ast_fallback', 'python-ast')
                items.append(it)
                core.append(dev)
                kinds.append('source')
            return ad.Normalized(items, core, kinds, notes, {'native_items': len(lsp_items),
                                 'total_items': len(lsp_items) + len(payload.get('definitions', [])), 'backend': backend})

        # ---------------------------------------------------------------- closure targets
        def _targets(self, request, raw, norm) -> list:
            """(path, line, locator provenance, definition?) in rendered order; nothing here is read or shown."""
            out = []
            evidence_ids = [ev.id for ev in norm.core]
            if request.operation == 'grep_literal':
                for ev in norm.core:
                    if ev.content_kind == 'source_lines':
                        out += [{'path': ev.file, 'line': n, 'locator_provenance': 'grep_literal', 'definition': False}
                                for n, _ in ev.lines]
            elif request.operation == 'go_to_definition':
                payload = self._json(raw[0])
                for decl in payload.get('declarations', []):     # shown declarations: their class values only
                    if decl.get('provided') and decl['unit']['kind'] == 'method':
                        out.append({'path': decl['path'], 'line': decl['line_start'], 'definition': True,
                                    'locator_provenance': ('native_lsp' if ad.is_native(decl.get('located_by', ''))
                                                           else 'ast_fallback'), 'values_only': True})
                for loc in payload.get('lsp_locations', []):
                    out.append({'path': loc['path'], 'line': loc['line_start'], 'definition': True,
                                'locator_provenance': ('native_lsp' if ad.is_native(loc.get('source', ''))
                                                       else 'ast_fallback')})
            else:
                name = parse_symbol_query(request.query).name
                for item in raw:
                    rows = self._json(item)
                    native = any(ad.is_native(s) for s in (item.metadata.get('resolution_sources') or []))
                    for r in rows if isinstance(rows, list) else []:
                        line = r.get('line_start') or r.get('line')
                        if not r.get('path') or not line:
                            continue
                        tags = r.get('sources') or [r.get('source', '')]
                        row_native = native and any(ad.is_native(t) for t in tags if t)
                        target = {'path': r['path'], 'line': int(line), 'definition': False,
                                  'locator_provenance': 'native_lsp' if row_native else 'ast_fallback'}
                        # the product matches names case-insensitively (and symbol search by substring / fuzzily): such
                        # rows stay LISTED, but a body is acquired only for an exact-name row or a semantic (LSP) edge
                        if request.operation == 'symbol_search' and r.get('name') != name:
                            target['skip'] = 'name_not_exact'
                        elif not row_native and r.get('target') and not exact_name(r['target'], name):
                            target['skip'] = 'name_not_exact'
                        out.append(target)
            for t in out:
                t.update(locator=request.id, locator_operation=request.operation, locator_evidence_ids=evidence_ids)
            return out

        # ---------------------------------------------------------------- batch execution with closures
        def plan_requests(self, requests):
            """Reserve <=1 physical slot BEFORE product deduplication/slicing (B only).

            An eligible queued unit becomes an actual, exact read_lines request; execute routes that request through
            the bounded-unit renderer and charges its real read once. With no eligible queue, a full proposed batch
            whose retained prefix contains a locator leaves one prospective body slot. The latter can remain unused
            if the locator yields no admissible unit; that uncertainty is recorded, never charged as a read.
            """
            allowance = max(0, min(self.accounting.limits['queries_per_batch'],
                                   self.accounting.limits['queries_total'] - self.accounting.total))
            unique, seen = [], set(self.accounting.seen_keys)
            for request in requests:
                if request.dedup_key not in seen:
                    unique.append(request)
                    seen.add(request.dedup_key)
            record = {'batch': self.state.batch_label(), 'input': [r.id for r in requests],
                      'allowance': allowance, 'reserved': 0, 'queue_checks': [], 'scheduled_auto': None}
            selected = unique[:allowance]
            pending = None
            if allowance:
                for entry in self.b_queue:
                    decision = self._decide(entry, record, check_slots=False)
                    if decision['status'] == 'already_shown':
                        values = self._values_entry(entry)
                        if values is not None:
                            entry = values
                            decision = self._decide(entry, record, check_slots=False)
                    record['queue_checks'].append({'unit': entry['unit'].label(), **decision})
                    if decision['status'] == 'read':
                        pending = entry
                        break
            if pending is not None:
                request = self._read_request(pending, planned=True)
                self._b_planned[request.id] = pending
                selected = [*unique[:max(0, allowance - 1)], request]
                record.update(reserved=1, reason='eligible_queued_unit', scheduled_auto=request.id,
                              scheduled_unit=pending['unit'].to_dict())
            elif allowance > 1 and len(unique) >= allowance and any(
                    r.operation in CLOSURE_OPERATIONS and self._scope(r).kind not in ('invalid', 'not_found')
                    for r in unique[:allowance - 1]):
                selected = unique[:allowance - 1]
                record.update(reserved=1, reason='prospective_locator_body')
            record['selected'] = [r.id for r in selected]
            record['displaced_primary'] = [r.id for r in unique if r.id not in record['selected']]
            self.b_plans.append(record)
            return selected

        def execute(self, requests):
            planned = [(r, self._b_planned.pop(r.id)) for r in requests if r.id in self._b_planned]
            planned_ids = {r.id for r, _ in planned}
            primary = [r for r in requests if r.id not in planned_ids]
            batch = super().execute(primary)
            label = self.accounting.batches[-1]['label'] if self.accounting.batches else self.state.batch_label()
            record = {'batch': label, 'primary': [r.id for r in primary],
                      'scheduled_auto': [r.id for r, _ in planned],
                      'free_slots_after_primary': self.accounting.free_slots(), 'decisions': [], 'executed': []}
            self.b_batches.append(record)
            self.accounting.batches[-1]['scheduled_auto'] = [r.id for r, _ in planned]
            work = [dict(e, from_queue=True, planned_request=r) for r, e in planned]
            planned_keys = {e['unit'].key for _, e in planned}
            queued_before = list(self.b_queue)
            self.b_queue = []
            work += [dict(q, from_queue=True) for q in queued_before if q['unit'].key not in planned_keys]
            for request in primary:
                work += self._units_for(request, self._b_targets.pop(request.id, []), record)
            items, events, unprovided = [], [], []
            seen = set()
            while work:
                entry = work.pop(0)
                unit = entry['unit']
                if unit.key in seen:
                    continue
                seen.add(unit.key)
                decision = self._decide(entry, record)
                if entry.get('planned_request') is not None:
                    # This exact request has already entered the product loop. Execute and charge it once even if a
                    # preceding primary happened to expose the same unit; no virtual token is passed off as a query.
                    decision = {'status': 'read', 'scheduled_read': True, 'decision_before_scheduled_read': decision}
                record['decisions'].append({'unit': unit.label(), 'kind': unit.kind, 'locator': entry['locator'],
                                            'from_queue': bool(entry.get('from_queue')), 'decision': decision['status'],
                                            **{k: v for k, v in decision.items() if k != 'status'}})
                if decision['status'] == 'read':
                    item, event, admitted = self._auto_read(entry, label, request=entry.get('planned_request'))
                    events.append(event)
                    if item is not None and admitted:
                        items.append(item)
                        record['executed'].append(item.request_id)
                        values = self._values_entry(entry)
                        if values is not None:
                            work.insert(0, values)          # the method's class values come right after it
                    elif event.status == 'completed':
                        unprovided.append((entry, 'read but not admitted (source budget/consistency)'))
                elif decision['status'] == 'queued':
                    self.b_queue.append({k: v for k, v in entry.items() if k not in ('from_queue', 'planned_request')})
                    unprovided.append((entry, 'queued: no lookup slot left in this round'))
                elif decision['status'] in ('source_budget', 'exceeds_read_limit', 'not_maskable'):
                    unprovided.append((entry, decision['note']))
                elif decision['status'] == 'already_shown':
                    values = self._values_entry(entry)
                    if values is not None:
                        work.insert(0, values)
            note = self._status_note(label, unprovided)
            if note is not None:
                items.append(note)
            record['queue_after'] = [q['unit'].label() for q in self.b_queue]
            self.b_log.append(record)
            return reg.ToolExecutionBatch(evidence=[*batch.evidence, *items], events=[*batch.events, *events])

        def _units_for(self, request, targets, record) -> list:
            out, keys = [], set()
            skipped = {'no_enclosing_function': 0, 'not_python': 0, 'beyond_per_locator_limit': 0, 'name_not_exact': 0}
            for t in targets:
                if t.get('skip'):
                    skipped[t['skip']] += 1
                    continue
                try:
                    unit = self.units.unit_at(t['path'], int(t['line']), definition=t['definition'])
                except UnitError:
                    skipped['not_python'] += 1
                    continue
                if unit is None or unit.kind == 'class_header':
                    skipped['no_enclosing_function'] += 1
                    continue
                if t.get('values_only'):
                    values = self.units.class_values_unit(unit)
                    if values is None:
                        continue
                    unit = values
                if unit.key in keys:
                    continue
                if len(keys) >= MAX_UNITS_PER_LOCATOR:
                    skipped['beyond_per_locator_limit'] += 1
                    continue
                keys.add(unit.key)
                out.append({'unit': unit, **{k: v for k, v in t.items() if k not in ('path', 'line', 'definition')},
                            'target_line': t['line'], 'target_path': t['path']})
            record.setdefault('targets', []).append({'locator': request.id, 'targets': len(targets), 'units': len(out),
                                                     **skipped})
            return out

        def _values_entry(self, entry):
            unit = entry['unit']
            if unit.kind != 'method' or entry.get('values_only'):
                return None
            values = self.units.class_values_unit(unit)
            if values is None:
                return None
            return {**{k: v for k, v in entry.items() if k != 'planned_request'}, 'unit': values,
                    'values_of': unit.label(), 'from_queue': False}

        def _decide(self, entry, record, *, check_slots=True) -> dict:
            unit = entry['unit']
            store = self.account.store
            if all(store.covers('head', unit.path, s, e) for s, e in unit.runs):
                return {'status': 'already_shown'}
            if unit.span > READ_MAX_LINES:
                return {'status': 'exceeds_read_limit',
                        'note': f'not read: {unit.span} lines > {READ_MAX_LINES}-line read limit'}
            try:
                masked = self.masked.lines(unit.path)
            except ad.MaskError as exc:
                return {'status': 'not_maskable', 'note': f'not provided: {str(exc)[:80]}'}
            if unit.end > len(masked):
                return {'status': 'not_maskable', 'note': 'not provided: unit beyond end of file'}
            ev = oc.Evidence('trial', 'head', unit.path, lines=tuple((n, masked[n - 1]) for n in unit.lines),
                             provenance='read_lines')
            before = self.account.exposed_tokens()
            trial = oc.EvidenceStore()
            trial.by_id, trial._lines = dict(store.by_id), {k: dict(v) for k, v in store._lines.items()}
            try:
                trial.add(ev)
            except oc.EvidenceConflict as exc:
                return {'status': 'not_maskable', 'note': f'not provided: inconsistent with shown lines ({exc})'}
            marginal = trial.union_tokens(self.account.counter) - before
            cap = self.account.budgets.source_reference_tokens
            if before + marginal > cap:
                return {'status': 'source_budget', 'marginal_tokens': marginal, 'used': before,
                        'note': f'not read: needs {marginal} source tokens, {max(0, cap - before)} left of {cap}'}
            if check_slots and self.accounting.free_slots() <= 0:
                return {'status': 'queued', 'marginal_tokens': marginal}
            return {'status': 'read', 'marginal_tokens': marginal}

        def _read_request(self, entry, *, planned=False):
            unit = entry['unit']
            n = self._b_auto_seq[entry['locator']] = self._b_auto_seq.get(entry['locator'], 0) + 1
            return ContextRequest(id=f'{entry["locator"]}-b{n}', operation='read_lines', path=unit.path,
                                  start=unit.start, end=unit.end, origin=AUTO_ORIGIN,
                                  # read_lines ignores query. This distinguishes a bounded source-unit exposure from
                                  # a later ordinary request for the full bounding range, including unshown gaps.
                                  query=('oar-b-unit:' + oc._sha(unit.to_dict())[:16]) if planned else None,
                                  reason=f'automatic read of {unit.label()} located by {entry["locator"]}')

        def _auto_read(self, entry, label, request=None):
            unit = entry['unit']
            req = request or self._read_request(entry)
            self.accounting.record_auto(label, req, entry['locator'])           # charged BEFORE the read
            batch = product_registry.execute(self, [req])                       # the product's own read_lines
            event = batch.events[0]
            trace = {'batch': label, 'auto': True, 'request': dataclasses.asdict(req), 'product_event': dataclasses.asdict(event),
                     'locator_request': entry['locator'], 'locator_operation': entry.get('locator_operation'),
                     'locator_evidence_ids': entry.get('locator_evidence_ids'), 'locator_provenance': entry.get('locator_provenance'),
                     'from_queue': bool(entry.get('from_queue')), 'values_of': entry.get('values_of'),
                     'scheduled_by_plan': request is not None,
                     'unit': unit.to_dict(), 'range_basis': 'python-ast', 'source_read': 'product read_lines',
                     'raw': [{'id': r.id, 'source': r.source, 'chars': len(r.content), 'truncated': r.truncated,
                              'sha256': oc._sha(r.content), 'metadata': r.metadata, 'content': r.content}
                             for r in batch.evidence]}
            self.trace.append(trace)
            if event.status != 'completed' or not batch.evidence:
                trace['normalized'] = []
                return None, event, False
            got = batch.evidence[0]
            if int(got.metadata.get('end', 0)) < unit.end:
                trace['normalized'], trace['error'] = [], 'product read ended before the unit end; nothing shown'
                return None, replace_event(event, 'failed', 'read ended before the unit end'), False
            masked = self.masked.lines(unit.path)
            lines = {n: masked[n - 1] for n in unit.lines}
            what = {'function': 'def', 'method': 'def', 'class_values': 'class values of', 'statement': 'statement'}[unit.kind]
            source = (f'{unit.path}:{unit.start}-{unit.end} [auto {what} {".".join(unit.qualname[-2:])}; '
                      f'located by {entry["locator"]}; range python-ast]')
            it, ev = self._source_item(req, '', source, unit.path, lines, 'read_lines', None)
            it = dataclasses.replace(it, metadata={**it.metadata, 'start': unit.start, 'end': unit.end,
                                                   'auto_read_for': entry['locator'], 'range_basis': 'python-ast'})
            admission = self._admit(req, it, ev, 'source')
            trace['normalized'] = [admission]
            if not admission['admitted']:
                return it, replace_event(event, 'denied', 'blocked by pilot budget/consistency'), False
            if unit.runs == ((unit.start, unit.end),):
                # Only a complete admitted interval satisfies a future ordinary read of that entire interval.
                full_key = dataclasses.replace(req, query=None).dedup_key
                self.accounting.seen_keys.append(full_key)
            return it, dataclasses.replace(event, output_chars=len(it.content)), True

        def _status_note(self, label, unprovided):
            if not unprovided:
                return None
            rows = [f'- {e["unit"].label()} (located by {e["locator"]}): {why}' for e, why in unprovided]
            header = (f'automatic body reads not provided in {label} (ranges from python-ast; each read costs one lookup; '
                      f'request read_lines yourself if needed)')
            self._pending_meta = 0
            text, omitted = self._meta_text(header, rows, f'automatic body reads not provided in {label}')
            raw_id, final = f'evidence-{label}-bodies', f'agentic:{self.packet_id}:evidence-{label}-bodies'
            first = unprovided[0][0]['unit']
            item = ContextEvidence(id=raw_id, request_id=f'{label}-bodies', operation='body_acquisition',
                                   source=f'body_acquisition:{label}', content=text, metadata={'omitted_rows': omitted})
            ev = oc.Evidence(final, 'head', first.path, text=text, provenance='ast_fallback',
                             content_kind='member_listing', backend='python-ast', anchor_line=first.start)
            fake = ContextRequest(id=f'{label}-bodies', operation='body_acquisition', reason='closure status')
            admission = self._admit(fake, item, ev, 'metadata')
            self.trace.append({'batch': label, 'auto_note': True, 'request': None, 'normalized': [admission],
                               'rows': rows})
            return item if admission['admitted'] else None

    return BRegistry


def exact_name(target: str, name: str) -> bool:
    """Exact (case-sensitive) name or a dotted access ending in it: ``x.name`` / ``self.name``."""
    return target == name or target.endswith('.' + name)


def replace_query(request, query):
    return dataclasses.replace(request, query=query)


def replace_meta(item, **extra):
    return dataclasses.replace(item, metadata={**item.metadata, **extra})


def replace_event(event, status, error):
    return dataclasses.replace(event, status=status, error=error, output_chars=0)
