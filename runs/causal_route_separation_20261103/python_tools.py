"""Manifest-bound, question-driven Python source tools. No project code executes.

PythonTools(case, out_dir, budgets).query(question) returns evidence, unresolved,
raw_paths and incremental cost. close() shuts down all native sessions and
returns cumulative cost/status. Native locations are candidates, not runtime
bindings. AST relations explicitly distinguish lexical/intra/interprocedural
relations. Source bodies are comment/docstring masked and admitted atomically.
Question column=0 (or omitted) selects an unambiguous actual NAME token;
column>0 is a one-based UTF-8 byte offset, converted to LSP UTF-16 only after
token verification. query.cost is incremental; close.cost is the incremental
shutdown cost, while close.cumulative_cost includes all previous operations.
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import importlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import textwrap
import time
import tokenize
from urllib.parse import unquote, urlparse

from common import load, write_new, sha, digest, source_tokens

MASK_POLICY = 'legacy-compatible-comment-docstring-mask-physical-lines-v1'
LEGACY_PRODUCT = Path(__file__).resolve().parents[1] / 'oar_product_pilot_v3_20261026/product_snapshot_20261026'
PYRIGHT = Path(__file__).resolve().parents[2] / 'tools/pyright-lsp-20261023/node_modules/.bin/pyright-langserver'
SOURCE_KINDS = {'source_lines', 'implementation_body', 'exception_branch', 'body'}
DEFAULTS = dict(max_source_tokens=12000, max_native_calls=48, max_native_seconds=180,
                max_file_reads=256, max_query_seconds=20, max_wall_seconds=900,
                max_index_seconds=120)


def _physical(text):
    rows = re.split(r'(?<=\n)|(?<=\r)(?!\n)', text)
    return [r for r in rows if r]


def _plain(row):
    return row[:-2] if row.endswith('\r\n') else row[:-1] if row.endswith(('\n', '\r')) else row


def _dotted(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute):
        root = _dotted(node.value)
        return root + '.' + node.attr if root else None
    return None


def _mask(text, tree=None):
    """Mask only comments and actual docstrings, retaining every physical row."""
    rows = _physical(text)
    chars = [list(row) for row in rows]
    def erase(a, b):
        for n in range(a[0], b[0] + 1):
            first = a[1] if n == a[0] else 0
            last = b[1] if n == b[0] else len(chars[n - 1])
            for col in range(first, min(last, len(chars[n - 1]))):
                if chars[n - 1][col] not in '\r\n': chars[n - 1][col] = ' '
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    for tok in tokenize.generate_tokens(io.StringIO(normalized).readline):
        if tok.type == tokenize.COMMENT: erase(tok.start, tok.end)
    tree = tree or ast.parse(text)
    for node in ast.walk(tree):
        body = getattr(node, 'body', None)
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or not body: continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            a, b = first.lineno, first.end_lineno
            ac = len(_plain(rows[a - 1]).encode('utf-8')[:first.col_offset].decode('utf-8'))
            bc = len(_plain(rows[b - 1]).encode('utf-8')[:first.end_col_offset].decode('utf-8'))
            erase((a, ac), (b, bc))
            chars[a - 1][ac:ac + 2] = ['"', '"']
    # Match the preserved source pool's whitespace/docstring representation.
    return ''.join(''.join(row).rstrip(' \t\r\n') + ('\n' if rows[i].endswith(('\r', '\n')) else '')
                   for i, row in enumerate(chars))


def _scope_nodes(container, parents):
    for child in ast.walk(container):
        current = parents.get(child); nested = False
        while current is not None and current is not container:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                nested = True; break
            current = parents.get(current)
        if not nested: yield child


def _safe_rel(path):
    if not isinstance(path, str) or not path or '\\' in path: raise ValueError('invalid_relative_path')
    p = PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts: raise ValueError('path_outside_manifest')
    return str(p)


def _module(path):
    parts = list(PurePosixPath(path).with_suffix('').parts)
    if parts[-1] == '__init__': parts.pop()
    return '.'.join(parts)


def _changed(diff):
    rows = []; path = None; head = 0
    for original in diff.split('\n'):
        row = original.removesuffix('\r')
        if row.startswith('+++ b/'): path = row[6:]
        elif row.startswith('@@'):
            match = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)', row)
            if match: head = int(match[1])
        elif path and row.startswith('+') and not row.startswith('+++'): rows.append((path, head)); head += 1
        elif path and row.startswith('-') and not row.startswith('---'): rows.append((path, max(1, head)))
        elif path and row.startswith(' '): head += 1
    return list(dict.fromkeys(rows))


def seed_questions(case):
    """At most four exploratory questions from already public diff/HEAD blocks.

    Never rewrites natural-language contracts as executable callback source and
    never scans an unbilled repository index. A seed is a question, not a defect.
    """
    changed = _changed(case.get('diff', '')); choices = []
    for block in case.get('initial_blocks', []):
        if block.get('snapshot') != 'HEAD': continue
        try:
            text = textwrap.dedent(block['content']); tree = ast.parse(text)
        except (SyntaxError, KeyError): continue
        offset = block['start_line'] - 1
        for node in ast.walk(tree):
            if not hasattr(node, 'lineno'): continue
            a, b = node.lineno + offset, getattr(node, 'end_lineno', node.lineno) + offset
            if not any(p == block['path'] and a <= n <= b for p, n in changed): continue
            if isinstance(node, ast.Call):
                name = _dotted(node.func)
                if name: choices.append((0, block['path'], a, 'implementation_body', 'body', name))
            elif isinstance(node, (ast.Return, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.If, ast.While)):
                choices.append((1, block['path'], a, 'value_path', 'flow', ''))
    if not choices: choices = [(2, p, n, 'value_path', 'flow', '') for p, n in changed]
    out = []; seen = set()
    for _, path, line, kind, query, symbol in sorted(choices):
        key = path, line, query, symbol
        if key in seen: continue
        seen.add(key)
        out.append(dict(id=f'exploration-{len(out)+1}', claim_id='exploration', kind=kind,
                        query=query, snapshot='HEAD', file=path, line=line, symbol=symbol,
                        reason='Inspect actual changed operation without assuming that it is defective.'))
        if len(out) == 4: break
    return out


class ToolLimit(RuntimeError): pass


class NativeSession:
    """Use the preserved, tested product JSON-RPC client without its review loop."""
    def __init__(self, root, artifact_dir, timeout, environment=None):
        self.root = Path(root); self.out = Path(artifact_dir); self.timeout = timeout
        self.deadline = time.monotonic() + timeout
        self.records = []; self.wire_rpc = 0; self.lookup_rpc = 0; self.sequence = 0
        self.environment = dict(environment or {}); self.client = None
        snapshot = LEGACY_PRODUCT.resolve()
        existing = sys.modules.get('review_agent')
        if existing and not Path(existing.__file__).resolve().is_relative_to(snapshot):
            raise RuntimeError('different_review_agent_module_already_loaded')
        if str(snapshot) not in sys.path: sys.path.insert(0, str(snapshot))
        sys.dont_write_bytecode = True
        module = importlib.import_module('review_agent.lsp.client')
        structural = importlib.import_module('review_agent.code_intelligence.tree_sitter_index')
        self.module = module; self.StructuralSymbol = structural.StructuralSymbol
        command = str(PYRIGHT) if PYRIGHT.is_file() else shutil.which('pyright-langserver')
        if not command: raise RuntimeError('pyright_langserver_not_available')
        config = dataclasses.replace(module.SERVER_CONFIGS['python'], command=command)
        self.client = module.LspClient(self.root, config, timeout_seconds=timeout,
                                       initialize_timeout_seconds=timeout,
                                       environment={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        original_request = self.client._request; original_write = self.client._write
        def request(method, params, *, timeout=None):
            self.sequence += 1; index = self.sequence; started = time.monotonic()
            record = dict(method=method, params=params)
            try:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0: raise TimeoutError('shared_native_RPC_deadline')
                value = original_request(method, params, timeout=min(remaining, timeout or self.client.timeout_seconds))
                record['result'] = value; return value
            except Exception as exc:
                record['error'] = type(exc).__name__ + ': ' + str(exc); raise
            finally:
                record['seconds'] = time.monotonic() - started
                path = self.out / f'rpc-{index:04d}.json'; write_new(path, record)
                self.records.append(str(path))
        def write(message):
            value = original_write(message)
            if 'method' in message:
                self.wire_rpc += 1
                if message['method'].startswith('textDocument/'): self.lookup_rpc += 1
            return value
        self.client._request = request; self.client._write = write
        self.initialization_error = None
        try: self.client.start()
        except Exception as exc:
            self.initialization_error = type(exc).__name__ + ': ' + str(exc)
            try: self.client.close(graceful=False)
            except Exception: pass
    def query(self, operation, path, line, column, name):
        self.deadline = time.monotonic() + self.timeout
        if self.initialization_error:
            return [], dict(state='initialization_failed', operation=operation, error=self.initialization_error,
                            capabilities=self.client.server_capabilities)
        if not self.client.supports(operation):
            return [], dict(state='unsupported', operation=operation,
                            capabilities=self.client.server_capabilities)
        symbol = self.StructuralSymbol(path=path, name=name, kind='question_anchor',
            line_start=line, line_end=line, column_start=column, column_end=column + len(name),
            language='python', source='actual_question_AST_token')
        result = getattr(self.client, operation)(symbol)
        return [dataclasses.asdict(x) if dataclasses.is_dataclass(x) else x for x in result], dict(
            state='completed' if result else 'empty', operation=operation,
            capabilities=self.client.server_capabilities)
    def close(self):
        process = self.client.process if self.client else None
        self.deadline = time.monotonic() + min(2., self.timeout)
        try:
            if self.client: self.client.close()
        finally:
            if process:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream: stream.close()
        return dict(raw_paths=self.records, wire_rpc=self.wire_rpc, lookup_wire_rpc=self.lookup_rpc)


class PythonTools:
    def __init__(self, case, out_dir, budgets):
        self.case = copy.deepcopy(case); self.out = Path(out_dir); self.out.mkdir(parents=True, exist_ok=True)
        self.budgets = {**DEFAULTS, **budgets}; self.started = time.monotonic(); self.query_started = self.started
        self.cache = {}; self.native = {}; self.native_factory = NativeSession
        self.sequence = 0; self.evidence = copy.deepcopy(case.get('initial_blocks', []))
        self.source_total = max(source_tokens(self.evidence), budgets.get('source_tokens_used', 0))
        self.stats = dict(file_reads=0, ast_parses=0, ast_index_seconds=0., native_calls=0,
                          native_seconds=0., wire_rpc=0, lookup_wire_rpc=0, body_reads=0,
                          source_tokens=0, query_calls=0, wall_seconds=0.)
        self.closed = False; self._unresolved = []; self._raw_paths = []; self._relations = []
    def seed_questions(self):
        return seed_questions(self.case)
    def _check(self):
        elapsed = time.monotonic() - self.started
        if elapsed > self.budgets['max_wall_seconds']: raise ToolLimit('wall_time_budget')
        if time.monotonic() - self.query_started > self.budgets['max_query_seconds']: raise ToolLimit('query_time_budget')
    def _snapshot(self, snapshot):
        if snapshot not in ('BASE', 'HEAD') or snapshot not in self.case['snapshots']:
            raise ValueError('unknown_snapshot')
        return self.case['snapshots'][snapshot]
    def _target(self, snapshot, path):
        path = _safe_rel(path); spec = self._snapshot(snapshot)
        if path in spec.get('files', {}):
            root = Path(spec['root']).resolve(); absolute = (root / path).resolve()
            if not absolute.is_relative_to(root): raise ValueError('source_symlink_outside_snapshot')
            return absolute, spec['files'][path], dict(status='repository_snapshot',
                snapshot=snapshot, runtime_dispatch_confirmed=False)
        external = spec.get('external_files', {}).get(path)
        if external:
            binding = dict(status=external.get('binding_status', 'assumption'), origin=external.get('origin'),
                           version=external.get('version'), binding_evidence=external.get('binding_evidence', []),
                           assumptions=external.get('assumptions', []), runtime_dispatch_confirmed=False)
            if binding['status'] == 'confirmed' and not binding['binding_evidence']:
                binding.update(status='assumption', invalid_confirmation='missing_binding_evidence')
            return Path(external['absolute_source']), external['sha256'], binding
        raise ValueError('path_not_in_allowed_snapshot_pool')
    def _read(self, snapshot, path):
        self._check()
        if self.stats['file_reads'] >= self.budgets['max_file_reads']: raise ToolLimit('file_read_budget')
        absolute, expected, binding = self._target(snapshot, path)
        raw = absolute.read_bytes(); self.stats['file_reads'] += 1
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected: raise ValueError('source_sha256_mismatch')
        key = snapshot, path
        if key not in self.cache:
            started = time.monotonic(); text = raw.decode('utf-8'); tree = ast.parse(text, filename=path)
            parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            definitions = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names = [node.name]; parent = parents.get(node)
                    while parent is not None:
                        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): names.insert(0, parent.name)
                        parent = parents.get(parent)
                    definitions.append(('.'.join(names), node))
            masked = _mask(text, tree)
            self.cache[key] = dict(raw=raw, text=text, masked=masked, rows=_physical(text),
                masked_rows=_physical(masked), tree=tree, parents=parents, definitions=definitions,
                sha256=actual, binding=binding, absolute=str(absolute))
            self.stats['ast_parses'] += 1; self.stats['ast_index_seconds'] += time.monotonic() - started
            if self.stats['ast_index_seconds'] > self.budgets['max_index_seconds']: raise ToolLimit('AST_index_time_budget')
        return self.cache[key]
    def _module_path(self, snapshot, module):
        if not module or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', module): return []
        spec = self._snapshot(snapshot); base = module.replace('.', '/')
        for name in (base + '.py', base + '/__init__.py', base + '.pyi'):
            if name in spec.get('files', {}): return [name]
        matches = []
        for path, entry in spec.get('external_files', {}).items():
            declared = entry.get('module')
            if declared == module: matches.append(path); continue
            if declared: continue
            parts = path.split('/')
            positions = [i for i, value in enumerate(parts) if re.fullmatch(r'python-?\d+(?:\.\d+)*', value)]
            if positions and _module('/'.join(parts[positions[-1] + 1:])) == module: matches.append(path)
        return matches
    def _scope(self, data, line):
        nodes = [n for _, n in data['definitions'] if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.lineno <= line <= n.end_lineno]
        return min(nodes, key=lambda n: n.end_lineno - n.lineno) if nodes else data['tree']
    def _imports(self, data, path, scope):
        result = {}; chain = []
        parents = data['parents']; node = scope
        while node is not None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): chain.append(node)
            node = parents.get(node)
        module = _module(path); package = module if path.endswith('/__init__.py') else module.rpartition('.')[0]
        for container in [data['tree'], *reversed(chain)]:
            for node in getattr(container, 'body', []):
                if isinstance(node, ast.Import):
                    for alias in node.names: result[alias.asname or alias.name.split('.')[0]] = (alias.name if alias.asname else alias.name.split('.')[0], '')
                elif isinstance(node, ast.ImportFrom):
                    imported = node.module or ''
                    if node.level:
                        pieces = package.split('.') if package else []
                        imported = '.'.join(pieces[:max(0, len(pieces) - node.level + 1)] + ([imported] if imported else []))
                    for alias in node.names:
                        if alias.name != '*': result[alias.asname or alias.name] = (imported, alias.name)
        return result
    def _resolve(self, snapshot, path, name, line=1, seen=None):
        seen = set() if seen is None else seen; key = snapshot, path, name
        if key in seen: return []
        seen.add(key)
        if len(seen) > 32:
            self._unresolved.append(dict(reason='static_binding_expansion_bound', symbol=name)); return []
        data = self._read(snapshot, path); scope = self._scope(data, line)
        first, _, tail = name.partition('.')
        lexical = scope
        while lexical is not None:
            if isinstance(lexical, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope_qual = next(q for q, n in data['definitions'] if n is lexical)
                nested = [(path, q, n, 'ast_nested_definition') for q, n in data['definitions'] if q == scope_qual + '.' + name]
                if nested: return nested
                params = {a.arg for a in [*lexical.args.posonlyargs, *lexical.args.args, *lexical.args.kwonlyargs]}
                if first in params:
                    if first in ('self', 'cls') and tail:
                        cls = data['parents'].get(lexical)
                        if isinstance(cls, ast.ClassDef):
                            qual = next(q for q, n in data['definitions'] if n is cls)
                            return [(path, q, n, 'ast_declared_class_member_candidate') for q, n in data['definitions'] if q == qual + '.' + tail]
                    self._unresolved.append(dict(reason='parameter_runtime_binding_unknown', symbol=name, file=path, line=line)); return []
                assignments = [n for n in _scope_nodes(lexical, data['parents']) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == first for t in n.targets)]
                if assignments:
                    prior = [n for n in assignments if n.lineno <= line]
                    if not prior:
                        self._unresolved.append(dict(reason='local_binding_before_assignment', symbol=name, file=path, line=line)); return []
                    assignment = max(prior, key=lambda n: n.lineno); target = _dotted(assignment.value)
                    if target: return self._resolve(snapshot, path, target + ('.' + tail if tail else ''), max(1, assignment.lineno - 1), seen)
                    if isinstance(assignment.value, ast.Call) and tail:
                        cls_name = _dotted(assignment.value.func)
                        if cls_name:
                            found = self._resolve(snapshot, path, cls_name + '.' + tail, max(1, assignment.lineno - 1), seen)
                            return [(p, q, n, 'ast_constructed_instance_member_candidate') for p, q, n, _ in found]
                    self._unresolved.append(dict(reason='local_assigned_value_not_statically_callable', symbol=name, file=path, line=line)); return []
            lexical = data['parents'].get(lexical)
        direct = [(path, q, n, 'ast_lexical_definition') for q, n in data['definitions'] if q == name]
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            own = next((q for q, n in data['definitions'] if n is scope), '')
            nested = [(path, q, n, 'ast_nested_definition') for q, n in data['definitions'] if q == own + '.' + name]
            if nested: return nested
        imports = self._imports(data, path, scope)
        if first in imports:
            module, imported = imports[first]; target = '.'.join(x for x in (imported, tail) if x)
            out = []
            if not target: return []
            for module_path in self._module_path(snapshot, module):
                out.extend(self._resolve(snapshot, module_path, target, 1, seen))
            if direct:
                self._unresolved.append(dict(reason='multiple_static_namespace_bindings', symbol=name, file=path))
                return [(p, q, n, 'ast_definition_with_possible_import_rebinding') for p, q, n, _ in direct] + out
            return out or direct
        if direct:
            if len(direct) > 1: self._unresolved.append(dict(reason='multiple_static_definitions', symbol=name, file=path))
            return direct
        # A direct assignment alias preserves a candidate binding; no runtime dispatch claim.
        for node in _scope_nodes(scope, data['parents']):
            if not isinstance(node, ast.Assign) or node.lineno > line: continue
            if any(isinstance(t, ast.Name) and t.id == first for t in node.targets):
                target = _dotted(node.value)
                if target: return self._resolve(snapshot, path, target + ('.' + tail if tail else ''), node.lineno, seen)
                if isinstance(node.value, ast.Call) and tail:
                    cls_name = _dotted(node.value.func)
                    if cls_name: return self._resolve(snapshot, path, cls_name + '.' + tail, node.lineno, seen)
        # Explicit module-qualified external/repository symbols, never import host modules.
        if '.' in name:
            pieces = name.split('.')
            for cut in range(len(pieces) - 1, 0, -1):
                for module_path in self._module_path(snapshot, '.'.join(pieces[:cut])):
                    found = self._resolve(snapshot, module_path, '.'.join(pieces[cut:]), 1, seen)
                    if found: return found
        return []
    def _metadata(self, snapshot, path, line, kind, value, provenance, binding=None):
        data = self._read(snapshot, path)
        item = dict(snapshot=snapshot, path=path, start_line=line, end_line=line,
            kind=kind, content=json.dumps(value, ensure_ascii=False), provenance=provenance,
            mechanical_checks=dict(raw_sha256=data['sha256'], actual_range=[line, line],
                source_body_provided=False, semantic_truth_guaranteed=False),
            binding={**data['binding'], **(binding or {})})
        item['id'] = 'E-PY-' + digest(item)[:16]; return item
    def _body(self, snapshot, path, node, kind='implementation_body', extra=None):
        if path.endswith('.pyi'):
            self._unresolved.append(dict(reason='typing_stub_not_runtime_implementation', path=path)); return None
        data = self._read(snapshot, path)
        a = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])]); b = node.end_lineno
        content = ''.join(data['masked_rows'][a - 1:b])
        item = dict(snapshot=snapshot, path=path, start_line=a, end_line=b, content=content,
            kind=kind, provenance='read_lines_from_verified_AST_unit',
            mechanical_checks=dict(raw_sha256=data['sha256'], mask_policy=MASK_POLICY,
                masked_sha256=digest(content), actual_range=[a, b], complete_unit=True,
                line_ending_policy='raw LF/CRLF/CR identity kept by SHA; masked physical records normalized to LF',
                semantic_truth_guaranteed=False), binding={**data['binding'], **(extra or {})})
        item['id'] = 'E-PY-' + digest(item)[:16]
        self.stats['body_reads'] += 1
        try: before = source_tokens(self.evidence); after = source_tokens(self.evidence + [item])
        except AssertionError as exc:
            self._unresolved.append(dict(reason='supplied_masked_source_representation_conflict', path=path, detail=str(exc))); return None
        delta = after - before
        if self.source_total + delta > self.budgets['max_source_tokens']:
            self._unresolved.append(dict(reason='whole_body_source_token_budget', path=path,
                                         actual_range=[a, b], source_tokens_required=delta)); return None
        if not any(x.get('id') == item['id'] for x in self.evidence): self.evidence.append(item)
        self.source_total += delta; self.stats['source_tokens'] += delta
        return item
    def _native(self, q, operation, data):
        self._check()
        if self.stats['native_calls'] >= self.budgets['max_native_calls']: raise ToolLimit('native_call_budget')
        remaining = self.budgets['max_native_seconds'] - self.stats['native_seconds']
        if remaining <= 0: raise ToolLimit('native_time_budget')
        snapshot = q['snapshot']; path = q['file']; line = q.get('line', 1); name = q.get('symbol', '').split('.')[-1]
        anchor = self._anchor(q, data)
        if anchor is None: return []
        name, column = anchor
        started = time.monotonic(); self.stats['native_calls'] += 1
        session = None; before = (0, 0)
        try:
            new_session = snapshot not in self.native
            if new_session:
                self.native[snapshot] = self.native_factory(self._snapshot(snapshot)['root'], self.out / snapshot,
                    min(remaining, self.budgets['max_query_seconds']), self.case.get('environment'))
            session = self.native[snapshot]; before = (0, 0) if new_session else (session.wire_rpc, session.lookup_rpc)
            remaining_query = self.budgets['max_query_seconds'] - (time.monotonic() - self.query_started)
            remaining_native = remaining - (time.monotonic() - started)
            if min(remaining_query, remaining_native) <= 0: raise ToolLimit('native_initialization_exhausted_query_budget')
            session.timeout = min(remaining_query, remaining_native)
            locations, state = session.query(operation, path, line, column, name)
            self._unresolved.append(dict(reason='native_operation_state', **state)) if state['state'] != 'completed' else None
            self._raw_paths.extend(session.records)
            return locations
        except Exception as exc:
            self._unresolved.append(dict(reason='native_operation_failed', operation=operation,
                                         error=type(exc).__name__ + ': ' + str(exc))); return []
        finally:
            self.stats['native_seconds'] += time.monotonic() - started
            if session:
                self.stats['wire_rpc'] += session.wire_rpc - before[0]
                self.stats['lookup_wire_rpc'] += session.lookup_rpc - before[1]
    def _anchor(self, q, data):
        """Verify a source NAME token; neither strings nor comments are anchors."""
        line = q.get('line', 1); requested = q.get('column', 0)
        name = q.get('symbol', '').split('.')[-1]
        if not isinstance(requested, int) or isinstance(requested, bool) or requested < 0:
            raise ValueError('question_column_must_be_nonnegative_UTF8_byte_integer')
        row = _plain(data['rows'][line - 1]); char_column = None
        if requested:
            raw = row.encode('utf-8')
            if requested - 1 >= len(raw): raise ValueError('question_column_outside_source_line')
            try: char_column = len(raw[:requested - 1].decode('utf-8'))
            except UnicodeDecodeError as exc: raise ValueError('question_column_inside_UTF8_codepoint') from exc
        normalized = data['text'].replace('\r\n', '\n').replace('\r', '\n')
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(normalized).readline)
                  if t.type == tokenize.NAME and t.start[0] == line and (not name or t.string == name)
                  and (char_column is None or t.start[1] == char_column)]
        if len(tokens) != 1:
            self._unresolved.append(dict(reason='native_anchor_token_unresolved' if not tokens else 'native_anchor_token_ambiguous',
                file=q['file'], line=line, symbol=name, column=requested,
                column_encoding='one_based_UTF8_bytes', candidate_tokens=len(tokens)))
            return None
        token = tokens[0]
        return token.string, len(row[:token.start[1]].encode('utf-16-le')) // 2 + 1
    def _location_targets(self, snapshot, locations):
        out = []
        for loc in locations:
            path = self._native_path(snapshot, loc.get('path')); line = loc.get('line_start', 1)
            try:
                data = self._read(snapshot, path)
            except (ValueError, OSError, SyntaxError) as exc:
                self._unresolved.append(dict(reason='native_location_outside_allowed_pool_or_unavailable',
                    path=path, error=str(exc))); continue
            candidates = []
            for qual, node in data['definitions']:
                if node.lineno != line: continue
                row = _plain(data['rows'][line - 1])
                match = re.search(r'\b(?:def|class)\s+(' + re.escape(node.name) + r')\b', row)
                if not match: continue
                column = len(row[:match.start(1)].encode('utf-16-le')) // 2 + 1
                if loc.get('column_start') == column: candidates.append((qual, node))
            if not candidates:
                self._unresolved.append(dict(reason='native_location_not_function_or_class_name_token', path=path, line=line,
                                             location=loc, body_not_inferred_from_enclosing_scope=True)); continue
            qual, node = min(candidates, key=lambda x: x[1].end_lineno - x[1].lineno)
            out.append((path, qual, node, 'native_lsp_location_candidate'))
        return out
    def _native_path(self, snapshot, path):
        if not isinstance(path, str): return path
        if path.startswith('file:'): path = unquote(urlparse(path).path)
        if not Path(path).is_absolute(): return path
        spec = self._snapshot(snapshot); absolute = Path(path).resolve(); root = Path(spec['root']).resolve()
        if absolute.is_relative_to(root):
            relative = absolute.relative_to(root).as_posix()
            if relative in spec.get('files', {}): return relative
        for relative, external in spec.get('external_files', {}).items():
            if str(absolute) in (str(Path(external['absolute_source']).resolve()), external.get('origin')): return relative
        return path
    def _exception_info(self, node):
        return [dict(kind=type(n).__name__, start_line=n.lineno, end_line=n.end_lineno,
                     condition=ast.unparse(n.test) if isinstance(n, ast.If) else None)
                for n in ast.walk(node) if isinstance(n, (ast.Try, ast.ExceptHandler, ast.Raise, ast.If))]

    def _flow(self, snapshot, path, node):
        """Return actual source units with explicitly static, non path-sensitive edges."""
        data = self._read(snapshot, path); edges = []; targets = []
        scope = node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)) else self._scope(data, node.lineno)
        parents = data['parents']
        def own_nodes(container): return _scope_nodes(container, parents)
        for item in own_nodes(scope):
            self._check()
            if isinstance(item, ast.Assign) and _dotted(item.value):
                for target in item.targets:
                    if isinstance(target, ast.Name): edges.append(dict(kind='assignment_alias_candidate', procedural_scope='intraprocedural',
                        file=path, line=item.lineno, target=target.id, value=_dotted(item.value)))
            if not isinstance(item, ast.Call): continue
            name = _dotted(item.func)
            if not name:
                self._unresolved.append(dict(reason='dynamic_call_expression', file=path, line=item.lineno)); continue
            found = self._resolve(snapshot, path, name, item.lineno)
            if not found:
                self._unresolved.append(dict(reason='call_target_unresolved', file=path, line=item.lineno, symbol=name)); continue
            for callee_path, qual, callee, mode in found:
                edge = dict(kind='direct_call_candidate', procedural_scope='interprocedural',
                    caller_file=path, caller_line=item.lineno, callee_file=callee_path,
                    callee_line=callee.lineno, callee_symbol=qual, binding_mode=mode,
                    runtime_dispatch_confirmed=False)
                edges.append(edge); targets.append((callee_path, qual, callee, mode))
                if isinstance(callee, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    params = [a.arg for a in [*callee.args.posonlyargs, *callee.args.args]]
                    decorators = {_dotted(d) for d in callee.decorator_list}
                    bound = mode in ('ast_constructed_instance_member_candidate', 'ast_declared_class_member_candidate')
                    if params and params[0] in ('self', 'cls') and 'staticmethod' not in decorators and (bound or 'classmethod' in decorators): params = params[1:]
                    positional_count_unknown = False
                    for position, arg in enumerate(item.args):
                        if isinstance(arg, ast.Starred):
                            positional_count_unknown = True
                            self._unresolved.append(dict(reason='star_argument_parameter_mapping_unresolved', file=path, line=item.lineno)); continue
                        if positional_count_unknown:
                            self._unresolved.append(dict(reason='post_star_positional_parameter_mapping_unresolved', file=path, line=item.lineno)); continue
                        if position < len(params): edges.append(dict(edge, kind='argument_to_parameter_candidate',
                            argument=ast.unparse(arg), parameter=params[position], position=position))
                        elif callee.args.vararg:
                            edges.append(dict(edge, kind='argument_to_parameter_candidate', argument=ast.unparse(arg),
                                parameter=callee.args.vararg.arg, position=position, variadic=True))
                        else: self._unresolved.append(dict(reason='positional_argument_without_declared_parameter', file=path, line=item.lineno, position=position))
                    keyword_params = {a.arg for a in [*callee.args.args, *callee.args.kwonlyargs]}
                    for keyword in item.keywords:
                        if keyword.arg is None:
                            self._unresolved.append(dict(reason='kwargs_parameter_mapping_unresolved', file=path, line=item.lineno)); continue
                        if keyword.arg in keyword_params:
                            edges.append(dict(edge, kind='argument_to_parameter_candidate', argument=ast.unparse(keyword.value), parameter=keyword.arg))
                        elif callee.args.kwarg:
                            edges.append(dict(edge, kind='argument_to_parameter_candidate', argument=ast.unparse(keyword.value),
                                parameter=callee.args.kwarg.arg, keyword_key=keyword.arg, variadic=True))
                        else: self._unresolved.append(dict(reason='keyword_argument_without_declared_parameter', file=path, line=item.lineno, keyword=keyword.arg))
                    parent = parents.get(item)
                    if isinstance(parent, (ast.Assign, ast.AnnAssign)):
                        assigned = [ast.unparse(t) for t in parent.targets] if isinstance(parent, ast.Assign) else [ast.unparse(parent.target)]
                        callee_data = self._read(snapshot, callee_path)
                        for returned in _scope_nodes(callee, callee_data['parents']):
                            if isinstance(returned, ast.Return): edges.append(dict(edge, kind='return_to_assignment_candidate',
                                return_line=returned.lineno, return_expression=ast.unparse(returned.value) if returned.value else 'None', assigned_targets=assigned,
                                branch_feasibility_confirmed=False))
        outer_args = {a.arg for a in [*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs]} if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) else set()
        outer_locals = outer_args | {n.id for n in own_nodes(scope) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)} | {
            n.name for n in getattr(scope, 'body', []) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        for inner in getattr(scope, 'body', []):
            if not isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)): continue
            args = {a.arg for a in [*inner.args.posonlyargs, *inner.args.args, *inner.args.kwonlyargs]}
            local = args | {n.id for n in own_nodes(inner) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            globals_ = {name for n in own_nodes(inner) if isinstance(n, ast.Global) for name in n.names}
            used = {n.id for n in own_nodes(inner) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            for name in sorted((used - local - globals_) & outer_locals): edges.append(dict(
                kind='closure_capture_candidate', procedural_scope='lexical_scope_crossing_not_runtime_call_flow',
                outer_file=path, outer_line=getattr(scope, 'lineno', 1), inner_line=inner.lineno,
                name=name, callback_invocation_confirmed=False))
            for call in own_nodes(inner):
                if isinstance(call, ast.Call) and (_dotted(call.func) or '').split('.')[0] in (used - local - globals_) & outer_locals:
                    self._unresolved.append(dict(reason='closure_callback_runtime_dispatch_unconfirmed',
                        file=path, line=call.lineno, symbol=_dotted(call.func)))
        self._relations.extend(edges)
        evidence = []
        if not isinstance(scope, ast.Module):
            item = self._body(snapshot, path, scope, 'source_lines', dict(static_relations=edges,
                analysis_scope='intraprocedural syntax plus explicitly labelled interprocedural candidate links',
                path_sensitive=False, callback_runtime_dispatch_confirmed=False))
            if item: evidence.append(item)
        for callee_path, qual, callee, mode in targets:
            if isinstance(callee, (ast.FunctionDef, ast.AsyncFunctionDef)):
                item = self._body(snapshot, callee_path, callee, 'implementation_body', dict(
                    resolution_mode=mode, static_candidate_symbol=qual, runtime_dispatch_confirmed=False))
                if item and not any(x['id'] == item['id'] for x in evidence): evidence.append(item)
        return evidence

    def query(self, question):
        if self.closed: raise RuntimeError('PythonTools_already_closed')
        q = copy.deepcopy(question); self.sequence += 1; self.query_started = time.monotonic()
        before = dict(self.stats); self._unresolved = []; self._raw_paths = []; self._relations = []
        self.stats['query_calls'] += 1; evidence = []
        try:
            self._check(); snapshot = q.get('snapshot', 'HEAD'); self._snapshot(snapshot)
            query = q.get('query'); kind = q.get('kind'); path = q.get('file'); line = q.get('line') or 1
            if kind == 'runtime_observation' or query == 'runtime':
                self._unresolved.append(dict(reason='runtime_probe_disabled_static_condition'))
            elif query == 'environment' or kind == 'dependency_version':
                summary = self.case.get('environment', {})
                self._unresolved.append(dict(reason='PR_environment_metadata_only_no_runtime_binding_proof', environment=summary,
                    environment_reference=self.case.get('environment_reference')))
                for external, meta in self._snapshot(snapshot).get('external_files', {}).items():
                    if q.get('symbol') and meta.get('module') == q['symbol'].split('.')[0]:
                        item = self._metadata(snapshot, external, 1, 'environment_record',
                            dict(module=meta.get('module'), origin=meta.get('origin'), version=meta.get('version'),
                                 binding_status=meta.get('binding_status', 'assumption'), binding_evidence=meta.get('binding_evidence', [])),
                            'allowlisted_external_environment_declaration')
                        evidence.append(item)
            else:
                self.source_total = max(self.source_total, self.budgets.get('source_tokens_used', 0))
                path = _safe_rel(path); data = self._read(snapshot, path)
                if not 1 <= line <= len(data['rows']): raise ValueError('question_line_outside_source')
                if q.get('column', 0) and self._anchor(q, data) is None:
                    raise ValueError('explicit_question_position_not_matching_source_symbol_token')
                name = q.get('symbol') or ''
                targets = self._resolve(snapshot, path, name, line) if name else []
                if query in ('definition', 'references', 'implementation'):
                    operation = {'definition': 'definition', 'references': 'references', 'implementation': 'implementation'}[query]
                    locations = self._native(q, operation, data)
                    if query == 'references':
                        for loc in locations:
                            try:
                                item = self._metadata(snapshot, self._native_path(snapshot, loc['path']), loc['line_start'], 'reference_edge',
                                    loc, 'native_lsp_reference_location', dict(call_or_dataflow_proven=False))
                                evidence.append(item)
                            except (ValueError, OSError, SyntaxError) as exc:
                                self._unresolved.append(dict(reason='reference_outside_allowed_pool_or_unavailable', path=loc.get('path'), error=str(exc)))
                    elif query == 'definition':
                        for loc in locations:
                            try:
                                evidence.append(self._metadata(snapshot, self._native_path(snapshot, loc['path']),
                                    loc['line_start'], 'definition_location', loc, 'native_lsp_definition_location',
                                    dict(location_only_not_implementation_body=True)))
                            except (ValueError, OSError, SyntaxError) as exc:
                                self._unresolved.append(dict(reason='definition_outside_allowed_pool_or_unavailable', path=loc.get('path'), error=str(exc)))
                        if not evidence:
                            for target, qual, node, mode in targets:
                                evidence.append(self._metadata(snapshot, target, node.lineno,
                                    'definition_location', dict(symbol=qual, kind=type(node).__name__, candidate_location=True), mode))
                    else:
                        native_targets = self._location_targets(snapshot, locations)
                        if native_targets: targets = native_targets
                        for target, qual, node, mode in targets:
                                item = self._body(snapshot, target, node, 'implementation_body',
                                    dict(resolution_mode=mode, runtime_dispatch_confirmed=False))
                                if item: evidence.append(item)
                elif query == 'flow' or kind == 'value_path':
                    scope = self._scope(data, line)
                    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        anchor = dict(q, file=path, line=scope.lineno, column=0, symbol=scope.name)
                        for operation in ('incoming_calls', 'outgoing_calls'):
                            locations = self._native(anchor, operation, data)
                            for location in locations:
                                try:
                                    evidence.append(self._metadata(snapshot, self._native_path(snapshot, location['path']),
                                        location['line_start'], 'reference_edge', dict(operation=operation, location=location),
                                        'native_lsp_call_hierarchy_candidate', dict(call_hierarchy_candidate=operation,
                                        call_or_dataflow_proven=False, runtime_dispatch_confirmed=False)))
                                except (ValueError, OSError, SyntaxError) as exc:
                                    self._unresolved.append(dict(reason='call_hierarchy_location_outside_allowed_pool_or_unavailable',
                                        path=location.get('path'), error=str(exc)))
                    evidence.extend(self._flow(snapshot, path, scope))
                elif query == 'body' or kind in ('implementation_body', 'exception_branch'):
                    if name and not targets:
                        targets = self._location_targets(snapshot, self._native(q, 'definition', data))
                    if not targets:
                        scope = self._scope(data, line)
                        if name:
                            self._unresolved.append(dict(reason='requested_callee_implementation_unresolved', symbol=name, file=path, line=line))
                            if not isinstance(scope, ast.Module):
                                item = self._body(snapshot, path, scope, 'source_lines', dict(role='enclosing_caller_context_only', requested_callee=name))
                                if item: evidence.append(item)
                        elif not isinstance(scope, ast.Module): targets = [(path, '', scope, 'actual_enclosing_function')]
                    for target, qual, node, mode in targets:
                        if isinstance(node, ast.ClassDef):
                            self._unresolved.append(dict(reason='class_location_not_function_implementation', path=target, symbol=qual)); continue
                        info = dict(resolution_mode=mode, static_candidate_symbol=qual, runtime_dispatch_confirmed=False)
                        if kind == 'exception_branch': info['actual_exception_and_condition_nodes'] = self._exception_info(node)
                        item = self._body(snapshot, target, node, 'exception_branch' if kind == 'exception_branch' else 'implementation_body', info)
                        if item: evidence.append(item)
                else: self._unresolved.append(dict(reason='unsupported_question_query', query=query))
                if not evidence and not self._unresolved: self._unresolved.append(dict(reason='no_static_target_resolved', symbol=name))
        except (ValueError, OSError, SyntaxError, UnicodeError, tokenize.TokenError, ToolLimit) as exc:
            self._unresolved.append(dict(reason=type(exc).__name__, detail=str(exc)))
        self.stats['wall_seconds'] = time.monotonic() - self.started
        cost = {key: self.stats[key] - before[key] for key in self.stats}
        result = dict(evidence=evidence, unresolved=self._unresolved, raw_paths=list(dict.fromkeys(self._raw_paths)),
                      cost=cost, static_relations=self._relations, cumulative_cost=dict(self.stats),
                      cumulative_source_tokens=self.source_total, completeness_claimed=False)
        path = self.out / f'query-{self.sequence:04d}.json'
        write_new(path, dict(question=q, result=result)); result['raw_paths'].append(str(path))
        return result

    def close(self):
        if self.closed: return dict(self._closed_result, cost={key: 0 for key in self.stats})
        before_cost = dict(self.stats)
        statuses = {}; paths = []
        for snapshot, session in self.native.items():
            before = session.wire_rpc; started = time.monotonic()
            try:
                status = session.close(); statuses[snapshot] = status; paths.extend(status.get('raw_paths', []))
            except Exception as exc: statuses[snapshot] = dict(error=type(exc).__name__ + ': ' + str(exc))
            self.stats['wire_rpc'] += session.wire_rpc - before
            self.stats['native_seconds'] += time.monotonic() - started
        self.closed = True; self.stats['wall_seconds'] = time.monotonic() - self.started
        result = dict(cumulative_cost=dict(self.stats), cumulative_source_tokens=self.source_total,
                      cost={key: self.stats[key] - before_cost[key] for key in self.stats},
                      native_status=statuses, raw_paths=paths, source_pool='snapshot manifests plus explicit external_files only')
        path = self.out / 'closed.json'; write_new(path, result); result['raw_paths'].append(str(path))
        self._closed_result = copy.deepcopy(result)
        return result
