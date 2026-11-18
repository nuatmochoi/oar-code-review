"""Preserved-core, lazy-file Pyright navigation for paired fixed/agentic runs.

No corpus-wide symbol index, reviewer calls, labels or witnesses. Every cross-
file target is returned by an actual (cached or new) native LSP query. Static
same-file structure is explicitly distinguished from runtime binding evidence.
"""
import ast
import copy
import heapq
import importlib.util
import io
import json
import keyword
import re
import sys
import tokenize
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ace_review.context_adaptive import digest, token_count
from ace_review.protocol_hypothesis import mask_source

_name = 'agentic_preserved_structure'
if _name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_name, ROOT / 'runs/lsp_context_closure_20261023/context_policy.py')
    _structure = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _structure
    _spec.loader.exec_module(_structure)
else:
    _structure = sys.modules[_name]
Client = _structure.Client
SOURCE_CAP, OBS_CAP, EXTRA_QUERIES, QUERY_CAP, DEPTH = 5200, 800, 6, 18, 3
MAX_NATIVE_TARGETS, MAX_CANDIDATE_LOCATIONS = 4, 32
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
DECLS = (ast.ClassDef, *FUNCS)


def _start(n):
    return min([n.lineno] + [x.lineno for x in getattr(n, 'decorator_list', [])])


def _header(n):
    if isinstance(n, ast.Match):
        first = n.cases[0].pattern.lineno
    else:
        first = n.body[0].lineno
    return set(range(_start(n), max(n.lineno + 1, first)))


def _position(text, n):
    return _structure._old.position(text, n)


class FileIndex:
    """One parsed file, loaded only when already visible or returned by LSP."""
    def __init__(self, text):
        self.text, self.rows = text, text.splitlines()
        self.tree = ast.parse(text)
        self.defs, self.parents, self.names = {}, {}, []
        self.identifier_spans = []
        try:
            for tok in tokenize.generate_tokens(io.StringIO(mask_source(text)).readline):
                if tok.type == tokenize.NAME and not keyword.iskeyword(tok.string):
                    self.identifier_spans.append((tok.start[0], tok.start[1], tok.end[1], tok.string))
        except (tokenize.TokenError, IndentationError):
            # Preserve validated spans preceding malformed/masked token input;
            # remaining request positions are conservatively unsupported.
            pass
        outer = self
        class Visit(ast.NodeVisitor):
            def __init__(self):
                self.path = []
            def declaration(self, n):
                outer.defs['.'.join(self.path + [n.name])] = n
                self.path.append(n.name)
                self.generic_visit(n)
                self.path.pop()
            visit_ClassDef = declaration
            visit_FunctionDef = declaration
            visit_AsyncFunctionDef = declaration
        Visit().visit(self.tree)
        for n in ast.walk(self.tree):
            for child in ast.iter_child_nodes(n):
                self.parents[id(child)] = n
            if isinstance(n, (ast.Name, ast.Attribute)):
                self.names.append(n)
    def enclosing(self, line):
        choices = [(name, n) for name, n in self.defs.items() if n.lineno <= line <= n.end_lineno]
        return min(choices, key=lambda x: (x[1].end_lineno - x[1].lineno, x[1].lineno)) if choices else None
    def owners(self, node):
        result = []
        while id(node) in self.parents:
            node = self.parents[id(node)]
            if isinstance(node, DECLS):
                result.append(node)
        return list(reversed(result))
    def declaration_position(self, n):
        row = self.rows[n.lineno - 1]
        m = re.search(r'\b(?:class|def)\s+(' + re.escape(n.name) + r')\b', row)
        return (n.lineno - 1, len(row[:m.start(1)].encode('utf-16-le')) // 2) if m else None
    def context_lines(self, n):
        """Literal lexical guards/owner declarations, including else/case."""
        lines = set()
        current = n
        while id(current) in self.parents:
            current = self.parents[id(current)]
            if isinstance(current, ast.match_case):
                lines.update(range(current.pattern.lineno, max(current.pattern.lineno + 1, current.body[0].lineno)))
            elif isinstance(current, ast.Match):
                lines.update(_header(current))
            elif isinstance(current, (ast.stmt, ast.ExceptHandler)) and isinstance(getattr(current, 'body', None), list) and current.body:
                lines.update(_header(current))
                for field in ('orelse', 'finalbody'):
                    suite = getattr(current, field, [])
                    if suite and suite[0].lineno <= n.lineno <= suite[-1].end_lineno:
                        lines.update(_structure._clause_lines(self.text, suite))
        return lines
    def module_statement(self, line):
        choices = [n for n in ast.walk(self.tree) if isinstance(n, ast.stmt) and not isinstance(n, DECLS)
                   and n.lineno <= line <= n.end_lineno]
        if not choices:
            return None
        n = min(choices, key=lambda x: (x.end_lineno - x.lineno, x.col_offset * -1))
        guards, parent = [], n
        while id(parent) in self.parents:
            parent = self.parents[id(parent)]
            if isinstance(parent, DECLS):
                return None
            if isinstance(parent, (ast.stmt, ast.ExceptHandler, ast.match_case)):
                guards.append(parent)
        return n, guards


def prepare_seed(root, base_root, legacy_packet, scope):
    """Return a byte-identical source/observation seed and serializable audit."""
    packet = copy.deepcopy(legacy_packet)
    assert token_count(packet['source']) <= SOURCE_CAP
    assert token_count(packet['plain']) <= OBS_CAP
    assert len(packet['audit']['queries']) <= 12
    packet['audit']['seed_source_sha256'] = digest(packet['source'])
    packet['audit']['seed_plain_sha256'] = digest(packet['plain'])
    packet['audit']['seed_query_count'] = len(packet['audit']['queries'])
    packet['audit']['seed_head_lines'] = copy.deepcopy(packet['audit']['source_lines'])
    packet['audit']['seed_base_lines'] = copy.deepcopy(packet['audit']['base_lines'])
    packet['audit']['policy'] = 'preserved_seed_v1'
    return packet


class Navigator:
    def __init__(self, root, base_root, seed, scope, agentic):
        self.root, self.base_root = Path(root).resolve(), Path(base_root).resolve()
        self.seed, self.scope, self.agentic = seed, scope, agentic
        self.protected = {f: set(v) for f, v in seed['audit']['source_lines'].items()}
        self.extra, self.inventories, self.inventory_texts = {}, [], []
        self.texts, self.indexes = {}, {}
        self.query_records, self.outcomes, self.admitted, self.blocked = [], [], [], []
        self.requests, self.declared_gaps, self.generic_gaps = [], [], []
        self.queue, self.sequence, self.scheduled = [], 0, set()
        self.seed_queries = copy.deepcopy(seed['audit']['queries'])
        self.seen = {(q['operation'], q['file'], q['line'] - 1, q['character_utf16']) for q in self.seed_queries}
        self.depths = {}
        for pkg in seed['audit']['admitted']:
            if pkg['key'][0] != 'head':
                continue
            f, qualname = pkg['key'][1:]
            d = 0 if pkg['origin'] == 'changed_root' else min(
                [q['depth'] + 1 for q in self.seed_queries if any(tuple(t) == (f, qualname) for t in q['targets'])] or [DEPTH + 1])
            for ln in pkg['lines']:
                self.depths[(f, ln)] = min(d, self.depths.get((f, ln), DEPTH + 1))
        self.gap_scanned, self.class_gaps = set(), set()
        self.deleted_anchors = []

    def text(self, f):
        p = (self.root / f).resolve()
        if not p.is_relative_to(self.root) or p.suffix != '.py' or not p.is_file():
            raise ValueError('not a production Python file inside the prepared workspace')
        if f not in self.texts:
            self.texts[f] = p.read_text()
        return self.texts[f]

    def index(self, f):
        if f not in self.indexes:
            self.indexes[f] = FileIndex(self.text(f))
        return self.indexes[f]

    def visible(self, f):
        return self.protected.get(f, set()) | self.extra.get(f, set())

    def source(self):
        out = [self.seed['source']]
        for f, lines in sorted(self.extra.items()):
            if not lines:
                continue
            out.append(f'[E-head-{digest(f)[:10]}] head {f}; additional numbered supplied lines only; omitted code unknown')
            rows = mask_source(self.text(f)).splitlines()
            prev = None
            for ln in sorted(lines):
                if prev is not None and ln != prev + 1:
                    out.append('... omitted ...')
                out.append(f'{ln}: {rows[ln - 1]}')
                prev = ln
        out.extend(self.inventory_texts)
        return '\n'.join(out)

    def add_lines(self, f, lines, depth, origin, component, provenance):
        lines = set(lines)
        new = lines - self.visible(f)
        if not new:
            return 'already_supplied'
        previous = set(self.extra.get(f, set()))
        self.extra[f] = previous | new
        if token_count(self.source()) > SOURCE_CAP:
            self.extra[f] = previous
            self.blocked.append({'file': f, 'component': component, 'origin': origin, 'outcome': 'source_budget',
                                 'lines': sorted(lines), 'provenance': provenance})
            return 'source_budget'
        for ln in new:
            self.depths[(f, ln)] = min(depth, self.depths.get((f, ln), DEPTH + 1))
        self.admitted.append({'file': f, 'component': component, 'origin': origin, 'lines': sorted(new),
                              'depth': depth, 'provenance': provenance, 'evidence_id': 'E-head-' + digest(f)[:10]})
        return 'added'

    def add_class(self, f, qualname, n, depth, origin, provenance):
        status = self.add_lines(f, _header(n) | self.index(f).context_lines(n), depth, origin, qualname + ':class_header', provenance)
        if status == 'source_budget':
            return status
        if any(x['file'] == f and x['qualname'] == qualname for x in self.inventories):
            return status
        inv = _structure.class_inventory(self.text(f), f, qualname, n)
        descriptors = []
        for e in inv['entries']:
            prefix = '?' if e['conditions'] else ''
            descriptors.append(f"{prefix}{e['kind']} {','.join(e['names'])}@{e['line']}")
        state = 'complete explicit lexical declarations' if inv['complete_explicit_lexical_inventory'] else 'partial lexical declarations; special binding scope unresolved'
        text = (f"[{inv['evidence_id']}] head {f}::{qualname} span {n.lineno}-{n.end_lineno}; {state}: "
                + '; '.join(descriptors) + '. ? means conditional. Inherited/dynamic members are unknown. '
                'Even a complete inventory does not prove runtime absence/presence. This is not method-body or attribute-value evidence; only numbered source lines supply bodies/values.')
        self.inventory_texts.append(text)
        if token_count(self.source()) > SOURCE_CAP:
            self.inventory_texts.pop()
            self.blocked.append({'file': f, 'component': qualname + ':inventory', 'origin': origin,
                                 'outcome': 'source_budget', 'provenance': provenance})
        else:
            inv['provenance'] = provenance
            self.inventories.append(inv)
            status = 'added'
        return status

    def query(self, method, f, line, char, symbol, depth, priority, origin, intent='definition', hint=None):
        record = {'operation': method, 'file': f, 'line': line + 1, 'character_utf16': char,
                  'symbol': symbol, 'depth': depth, 'origin': origin, 'intent': intent, 'hint': hint}
        key = (method, f, line, char)
        if method not in {'definition', 'references'}:
            return 'unsupported_operation'
        if line + 1 not in self.visible(f):
            return 'unsupported_invisible_position'
        if depth > DEPTH:
            return 'depth_budget'
        if key in self.seen:
            return 'already_queried'
        # A model request can promote an already queued generic query. Heap
        # duplicates are deduplicated on actual execution, not silently lost.
        if key in self.scheduled and not origin.startswith(('reviewer_', 'declared_')):
            return 'already_scheduled'
        self.scheduled.add(key)
        heapq.heappush(self.queue, (priority, self.sequence, record))
        self.sequence += 1
        return 'queued'

    def decl_query(self, f, n, depth, origin, priority, method='definition', intent='definition', hint=None):
        idx = self.index(f)
        status = self.add_lines(f, _header(n) | idx.context_lines(n), depth, origin, n.name + ':query_anchor', {'kind': 'same-file literal declaration header'})
        if status == 'source_budget':
            return status
        pos = idx.declaration_position(n)
        return self.query(method, f, *pos, n.name, depth, priority, origin, intent, hint) if pos else 'unsupported_declaration_position'

    def generic(self):
        """Source-gap adaptation: visible calls, attributes, imports, owners."""
        for f in sorted(set(self.protected) | set(self.extra)):
            idx = self.index(f)
            supplied = self.visible(f)
            for n in ast.walk(idx.tree):
                if not hasattr(n, 'lineno') or n.lineno not in supplied:
                    continue
                if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute)):
                    symbol = ast.unparse(n.func)
                    if isinstance(n.func, ast.Name) and n.func.id in {'str','int','float','bool','len','tuple','list','dict','set','isinstance','type','getattr','hasattr','super','range','enumerate','zip'}:
                        continue
                    ln, ch = _position(idx.text, n.func)
                    self._generic_query('definition', f, ln, ch, symbol, 'visible_callee', 3, 'callee')
                if isinstance(n, ast.Attribute):
                    ln, ch = _position(idx.text, n)
                    method = 'references' if isinstance(n.ctx, (ast.Store, ast.Del)) else 'definition'
                    self._generic_query(method, f, ln, ch, ast.unparse(n), 'visible_attribute', 2, 'attribute_consumer' if method == 'references' else 'definition')
                if isinstance(n, (ast.Import, ast.ImportFrom)) and set(range(n.lineno, n.end_lineno + 1)) <= supplied:
                    for alias in n.names:
                        if alias.name == '*':
                            continue
                        row = idx.rows[alias.lineno - 1]
                        ch = len(row.encode()[:alias.col_offset].decode().encode('utf-16-le')) // 2
                        self._generic_query('definition', f, alias.lineno - 1, ch, alias.name, 'visible_alias', 4, 'alias')
            # Owner class is lexical evidence; retrieve only its definition,
            # never all its callers automatically. Constructor bodies are
            # selected separately from actual native class definitions.
            for qual, n in idx.defs.items():
                if not isinstance(n, ast.ClassDef) or n.lineno not in supplied:
                    continue
                if (f, qual) in self.class_gaps:
                    continue
                self.class_gaps.add((f, qual))
                pos = idx.declaration_position(n)
                if pos:
                    self._generic_query('definition', f, *pos, n.name, 'visible_owner_class', 1, 'constructor')
        self.generic_gaps = [x for x in self.generic_gaps if x['outcome'] != 'already_scheduled']

    def _generic_query(self, method, f, ln, ch, symbol, origin, lane, intent):
        key = (method, f, ln, ch, origin)
        if key in self.gap_scanned:
            return
        self.gap_scanned.add(key)
        d = self.depths.get((f, ln + 1), DEPTH + 1)
        outcome = self.query(method, f, ln, ch, symbol, d, (lane, d, f, ln), origin, intent)
        self.generic_gaps.append({'operation': method, 'file': f, 'line': ln + 1, 'symbol': symbol,
                                  'origin': origin, 'depth': d, 'outcome': outcome})

    def add_requests(self, requests):
        for r in requests[:6]:
            record = copy.deepcopy(r)
            self.requests.append(record)
            f, line, column = r.get('file'), r.get('line'), r.get('column')
            if not isinstance(f, str) or not isinstance(line, int) or not isinstance(column, int) or line not in self.visible(f):
                record['outcome'] = 'unsupported_invisible_position';continue
            row = self.text(f).splitlines()[line - 1]
            if not any(ln == line and lo <= column - 1 < hi for ln, lo, hi, name in self.index(f).identifier_spans):
                record['outcome'] = 'unsupported_identifier_column';continue
            char = len(row[:column - 1].encode('utf-16-le')) // 2
            record['outcome'] = self.query(r.get('operation'), f, line - 1, char, row[column - 1:],
                                           self.depths.get((f, line), DEPTH + 1), (-3, 0, f, line), 'reviewer_request', 'requested')

    def add_declared_gaps(self, review):
        if not isinstance(review, dict):
            return
        assessment = review.get('context_assessment')
        if not isinstance(assessment, dict) and isinstance(review.get('review'), dict):
            assessment = review['review'].get('context_assessment')
        for gap in (assessment or {}).get('missing_connections', [])[:12]:
            record = copy.deepcopy(gap)
            self.declared_gaps.append(record)
            evidence, symbol, relation = gap.get('evidence_id'), gap.get('symbol'), gap.get('relation')
            files = [f for f in set(self.protected) | set(self.extra) if evidence == 'E-head-' + digest(f)[:10]]
            if not files or not isinstance(symbol, str) or not symbol.strip():
                record['outcome'] = 'unsupported_visible_evidence_anchor';continue
            if relation not in {'definition','caller','callee','attribute_consumer','constructor','inheritance','alias'}:
                record['outcome'] = 'unsupported_relation';continue
            f = files[0];idx = self.index(f);symbol = symbol.strip(' `');matches = []
            if not all(part.isidentifier() and not keyword.iskeyword(part) for part in symbol.split('.')):
                record['outcome'] = 'unsupported_symbol_syntax';continue
            for n in idx.names:
                ln, ch = _position(idx.text, n)
                rendered = ast.unparse(n)
                if ln + 1 in self.visible(f) and (rendered == symbol or (isinstance(n, ast.Attribute) and n.attr == symbol)):
                    matches.append((ln, ch, rendered))
            for qual, n in idx.defs.items():
                if n.lineno in self.visible(f) and symbol in {n.name, qual}:
                    pos = idx.declaration_position(n)
                    if pos:matches.append((*pos, n.name))
            method = 'references' if relation in {'caller','attribute_consumer'} else 'definition'
            if matches:
                ln, ch, rendered = sorted(matches)[0]
                record['outcome'] = self.query(method, f, ln, ch, rendered, self.depths.get((f, ln + 1), DEPTH + 1),
                                               (-2, 0, f, ln), 'declared_missing_connection', relation, symbol)
            else:
                # An unseen name may prioritize actual native results from a
                # visible lexical owner; it never becomes a fabricated source
                # coordinate or cross-file text-search fallback.
                classes = [(q, n) for q, n in idx.defs.items() if isinstance(n, ast.ClassDef) and n.lineno in self.visible(f)]
                if not classes:
                    record['outcome'] = 'unresolved_no_visible_symbol_or_parent';continue
                qual, node = min(classes, key=lambda x: (self.depths.get((f, x[1].lineno), DEPTH + 1), x[1].lineno))
                record['parent_anchor'] = {'file': f, 'qualname': qual, 'line': node.lineno}
                record['outcome'] = self.decl_query(f, node, self.depths.get((f, node.lineno), DEPTH + 1),
                                                   'declared_parent_reference', (-2, 1, f, node.lineno), 'references', relation, symbol)

    def deleted_bootstrap(self):
        for f, qual in self.seed['audit'].get('deleted_roots', []):
            if '.' not in qual:
                self.deleted_anchors.append({'base_file': f, 'deleted_symbol': qual, 'outcome': 'unresolved_no_lexical_class_owner'})
                continue
            if not (self.root / f).is_file():
                continue
            base_index = FileIndex((self.base_root / f).read_text())
            old = base_index.defs.get(qual)
            head_index = self.index(f)
            owner = qual.rsplit('.', 1)[0]
            new = head_index.defs.get(owner)
            if not isinstance(old, FUNCS) or not isinstance(new, ast.ClassDef):
                self.deleted_anchors.append({'base_file': f, 'deleted_symbol': qual, 'outcome': 'unresolved_no_corresponding_head_class'})
                continue
            provenance = {'kind': 'same-file qualified lexical owner correspondence, not runtime fallback',
                          'base_symbol': qual, 'base_span': [old.lineno, old.end_lineno],
                          'head_owner': owner, 'head_span': [new.lineno, new.end_lineno]}
            outcome = self.add_class(f, owner, new, 0, 'deleted_method_owner', provenance)
            self.deleted_anchors.append({**provenance, 'outcome': outcome})
            if outcome != 'source_budget':
                self.decl_query(f, new, 0, 'deleted_owner_entrypoints', (0, 0, f, new.lineno), 'references', 'caller')
                for base in new.bases:
                    if isinstance(base, (ast.Name, ast.Attribute)):
                        ln, ch = _position(head_index.text, base)
                        self.query('definition', f, ln, ch, ast.unparse(base), 0, (0, 1, f, ln), 'deleted_owner_base', 'inheritance')

    def locations(self, raw):
        result = []
        for loc in raw or []:
            uri = loc.get('uri', loc.get('targetUri'));rg = loc.get('range', loc.get('targetSelectionRange'))
            try:
                p = Path(unquote(urlparse(uri).path)).resolve();f = str(p.relative_to(self.root))
            except (TypeError, ValueError):
                result.append({'outcome': 'unsupported_outside_workspace'});continue
            if p.suffix != '.py' or not p.is_file():
                result.append({'outcome': 'unsupported_outside_production'});continue
            result.append({'file': f, 'line': rg['start']['line'] + 1, 'character_utf16': rg['start']['character']})
        return result

    def module(self, f, line, depth, origin, provenance):
        found = self.index(f).module_statement(line)
        if not found:
            return 'unsupported_not_module_statement'
        n, guards = found
        lines = set(range(n.lineno, n.end_lineno + 1))
        for guard in guards:
            if isinstance(guard, ast.match_case):
                lines.update(range(guard.pattern.lineno, max(guard.pattern.lineno + 1, guard.body[0].lineno)))
            else:
                lines.update(_header(guard))
                for field in ('orelse', 'finalbody'):
                    suite = getattr(guard, field, [])
                    if suite and suite[0].lineno <= line <= suite[-1].end_lineno:
                        lines.update(_structure._clause_lines(self.text(f), suite))
        return self.add_lines(f, lines, depth, origin, f'module:{n.lineno}-{n.end_lineno}', provenance)

    def cached_module_gaps(self):
        admitted = 0
        for i, q in enumerate(self.seed_queries):
            for excluded in q.get('excluded', []):
                if not isinstance(excluded, dict) or 'outside callable/class' not in excluded.get('reason', ''):
                    continue
                f, line = excluded['file'], excluded['line']
                if not any(x.get('file') == f and x.get('line') == line for x in self.locations(q.get('raw'))):
                    continue
                if admitted >= MAX_NATIVE_TARGETS:
                    self.outcomes.append({'phase': 'cached_module', 'seed_query_index': i + 1, 'file': f, 'line': line, 'outcome': 'fanout_budget'})
                    continue
                outcome = self.module(f, line, q['depth'] + 1, 'cached_native_module', {'kind': 'actual cached native result', 'seed_query_index': i + 1})
                admitted += outcome == 'added'
                self.outcomes.append({'phase': 'cached_module', 'seed_query_index': i + 1, 'file': f, 'line': line, 'outcome': outcome})

    def consume(self, q, raw):
        locations = self.locations(raw)
        if not raw:
            return [{'outcome': 'empty'}]
        valid = [x for x in locations if 'file' in x]
        rejected = [x for x in locations if 'file' not in x]
        # The bounded candidate window avoids parsing whole repositories after
        # a broad reference request. Only native-returned lines can be ranked.
        candidates = sorted(valid, key=lambda x: (x['file'] != q['file'], x['file'], x['line']))[:MAX_CANDIDATE_LOCATIONS]
        hint = q.get('hint')
        if hint:
            def hinted(x):
                row = self.text(x['file']).splitlines()[x['line'] - 1]
                return not bool(re.search(r'(?<!\w)' + re.escape(hint.split('.')[-1]) + r'(?!\w)', row))
            candidates.sort(key=lambda x: (hinted(x), x['file'] != q['file'], x['file'], x['line']))
        result = list(rejected)
        if len(valid) > len(candidates):
            result.append({'outcome': 'fanout_budget', 'uninspected_native_locations': len(valid) - len(candidates)})
        admitted, visited = 0, set()
        for x in candidates:
            f, line = x['file'], x['line']
            if admitted >= MAX_NATIVE_TARGETS:
                result.append({**x, 'outcome': 'fanout_budget'});continue
            idx = self.index(f);enclosing = idx.enclosing(line)
            provenance = {'kind': 'actual additional native result', 'query_number': len(self.query_records) + 1, 'returned_location': x}
            depth = q['depth'] + 1
            if enclosing:
                qual, n = enclosing
                if (f, qual) in visited:
                    result.append({**x, 'outcome': 'duplicate_target'});continue
                visited.add((f, qual))
                if isinstance(n, ast.ClassDef):
                    outcome = self.add_class(f, qual, n, depth, 'native_' + q['operation'], provenance)
                    # One constructor candidate only; the body requires its
                    # own actual definition query at a literal head signature.
                    if q['intent'] in {'constructor', 'callee'} and outcome != 'source_budget':
                        methods = [m for m in n.body if isinstance(m, FUNCS) and m.name in {'__new__', '__init__'}]
                        methods.sort(key=lambda m: (m.name != '__new__', m.lineno))
                        if methods:
                            self.decl_query(f, methods[0], depth, 'native_class_constructor_gap', (0, depth, f, methods[0].lineno), intent='constructor')
                else:
                    lines = set(range(_start(n), n.end_lineno + 1))
                    lines.update(idx.context_lines(n))
                    outcome = self.add_lines(f, lines, depth, 'native_' + q['operation'], qual, provenance)
                result.append({**x, 'target': qual, 'outcome': outcome})
            else:
                outcome = self.module(f, line, depth, 'native_' + q['operation'], provenance)
                result.append({**x, 'outcome': outcome})
            admitted += outcome == 'added'
        return result

    def run(self, client):
        while self.queue and len(self.query_records) < EXTRA_QUERIES:
            _, _, q = heapq.heappop(self.queue)
            key = (q['operation'], q['file'], q['line'] - 1, q['character_utf16'])
            if key in self.seen:
                continue
            self.seen.add(key)
            raw, error = None, None
            try:
                raw = getattr(client, q['operation'])(q['file'], q['line'] - 1, q['character_utf16'])
                outcomes = self.consume(q, raw)
            except Exception as exc:
                error = repr(exc);outcomes = [{'outcome': 'native_error'}]
            record = {**q, 'raw': raw, 'error': error, 'outcomes': outcomes,
                      'targets': [[x['file'], x['target']] for x in outcomes if 'target' in x],
                      'excluded': [x for x in outcomes if x['outcome'].startswith('unsupported')]}
            self.query_records.append(record)
            self.generic()
        for _, _, q in self.queue:
            key = (q['operation'], q['file'], q['line'] - 1, q['character_utf16'])
            if key not in self.seen:
                self.outcomes.append({**q, 'outcome': 'query_budget'})

    def packet(self):
        outcome_counts = {}
        for x in self.outcomes + [y for q in self.query_records for y in q['outcomes']]:
            outcome_counts[x['outcome']] = outcome_counts.get(x['outcome'], 0) + 1
        plain = self.seed['plain'] + '\n[E-RETRIEVAL] Original seed source is protected without eviction. '
        plain += f"Additional actual native queries: {len(self.query_records)}/{EXTRA_QUERIES}; outcomes {json.dumps(outcome_counts, sort_keys=True)}. "
        plain += 'Empty, unsupported, omitted-by-budget and unresolved are different outcomes; none proves absence. Inventories describe static lexical structure, not runtime binding.'
        assert token_count(plain) <= OBS_CAP
        source = self.source()
        assert source.startswith(self.seed['source']) and token_count(source) <= SOURCE_CAP
        audit = copy.deepcopy(self.seed['audit'])
        audit.update(policy='preserved_agentic_native_v1', source_tokens=token_count(source),
                     seed_queries=self.seed_queries, additional_queries=self.query_records,
                     new_queries=self.query_records,
                     queries=self.seed_queries + self.query_records,
                     source_lines={f: sorted(self.visible(f)) for f in set(self.protected) | set(self.extra)},
                     base_lines=copy.deepcopy(self.seed['audit']['base_lines']),
                     protected_seed_source_sha256=digest(self.seed['source']), protected_seed_plain_sha256=digest(self.seed['plain']),
                     seed_preserved=True, additional_admitted=self.admitted, additional_blocked=self.blocked,
                     class_inventories=self.inventories, requests=self.requests, declared_gaps=self.declared_gaps,
                     generic_gaps=self.generic_gaps, query_outcomes=self.outcomes, deleted_owner_anchors=self.deleted_anchors,
                     lazy_parsed_files=sorted(self.indexes), native_added_query_count=len(self.query_records),
                     logical_native_query_count=len(self.seed_queries) + len(self.query_records), mode='G' if self.agentic else 'F')
        assert len(audit['queries']) <= QUERY_CAP
        return {'source': source, 'plain': plain, 'audit': audit}


def followup(root, base_root, seed, scope, requests, agentic, review=None):
    nav = Navigator(root, base_root, seed, scope, agentic)
    nav.cached_module_gaps()
    nav.deleted_bootstrap()
    nav.generic()
    if agentic:
        nav.add_requests(requests)
        nav.add_declared_gaps(review)
    client = Client(nav.root)
    try:
        nav.run(client)
    finally:
        client.close()
    return {'packet': nav.packet(), 'actual_trace': client.trace,
            'initialization': client.initialization, 'stderr': client.errors}
