"""Frozen, gold-independent packets for diff -> Python AST -> native Pyright.

Only changed source files and the public diff select AST units and LSP anchors.
The public contract, historical seeds, findings, and annotations never select code.
Native preparation is bounded to two locator attempts and one complete-body read.
The same serialized LSP descriptor is consumed by both lsp and search reviews.
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import io
import json
from pathlib import Path
import tokenize
from typing import Any

import oar_product_adapter as common
import oar_core as core

ARMS = ('diff', 'ast', 'lsp', 'search')
SOURCE_CAP = 5200
METADATA_CAP = 800
VERSION = 1


class PacketError(ValueError):
    pass


def _sha(value):
    return core._sha(value)


def _safe_path(root, path):
    root = Path(root).resolve()
    target = (root / path).resolve()
    if Path(path).is_absolute() or not target.is_relative_to(root) or target.suffix != '.py':
        raise PacketError(f'not a workspace Python path: {path}')
    return target


def reverse_diff(head: str, diff_file) -> str:
    """Reverse exact hunks in memory. Never guess through a context mismatch."""
    lines = head.splitlines()
    for hunk in sorted(diff_file.hunks, key=lambda h: h.new_start, reverse=True):
        after = [line.text for line in hunk.lines if line.kind != 'delete']
        before = [line.text for line in hunk.lines if line.kind != 'add']
        if len(after) != hunk.new_count or len(before) != hunk.old_count:
            raise PacketError(f'{diff_file.path}: malformed hunk counts')
        start = hunk.new_start - (1 if hunk.new_count else 0)
        if start < 0 or lines[start:start + len(after)] != after:
            raise PacketError(f'{diff_file.path}:{hunk.new_start}: head/diff mismatch')
        lines[start:start + len(after)] = before
    return '\n'.join(lines) + ('\n' if lines else '')


def _start(node):
    return min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])


def _header_end(node):
    first = node.body[0].lineno if node.body else node.lineno
    return max(node.lineno, first - 1)


def _changed_lines(file, snapshot):
    kind, field = ('add', 'new_lineno') if snapshot == 'head' else ('delete', 'old_lineno')
    lines = sorted({getattr(line, field) for h in file.hunks for line in h.lines if line.kind == kind})
    if lines:
        return lines, 'changed_lines'
    # A pure deletion/addition has no changed lines on the other side. Its insertion
    # boundary is an anchor, not a claim that a neighbouring line was changed.
    starts = [h.new_start if snapshot == 'head' else h.old_start for h in file.hunks]
    return sorted({max(1, n) for n in starts}), 'opposite_side_boundary'


def _parse(text):
    tree = ast.parse(text)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    return tree, parents


def _ast_units(text, file, snapshot):
    tree, parents = _parse(text)
    changed, anchor_kind = _changed_lines(file, snapshot)
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    selected = set()
    candidates = []
    for line in changed:
        hits = [n for n in functions if _start(n) <= line <= n.end_lineno]
        if hits:
            selected.add(min(hits, key=lambda n: (n.end_lineno - _start(n), -n.lineno)))
            continue
        statements = [n for n in ast.walk(tree) if isinstance(n, ast.stmt)
                      and n.lineno <= line <= n.end_lineno]
        if statements:
            node = min(statements, key=lambda n: (n.end_lineno - n.lineno, -n.lineno))
            end = _header_end(node) if isinstance(node, ast.ClassDef) else node.end_lineno
            candidates.append((0, _start(node), end, 'changed_statement', getattr(node, 'name', '')))
    for node in selected:
        candidates.append((0, _start(node), node.end_lineno, 'changed_function', node.name))
        parent = parents.get(node)
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                candidates.append((1, _start(parent), _header_end(parent), 'ancestor_header', parent.name))
            parent = parents.get(parent)
    # Lexical import selection is intentionally not a binding claim. Select only
    # imports in this changed file whose local names occur in selected functions.
    used = {n.id for node in selected for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = {a.asname or (a.name.split('.')[0] if isinstance(node, ast.Import) else a.name)
                     for a in node.names}
            if used & names:
                candidates.append((2, node.lineno, node.end_lineno, 'lexical_import', ','.join(sorted(used & names))))
    candidates = sorted(set(candidates))
    return candidates, sorted(selected, key=lambda n: (_start(n), n.end_lineno, n.name)), tree, {
        'snapshot': snapshot, 'changed_lines': changed, 'anchor_kind': anchor_kind,
        'selection': 'changed units, ancestor headers, same-file lexical imports; no binding resolution',
    }


def _masked(text):
    from ace_review.protocol_hypothesis import mask_source
    return mask_source(text).splitlines()


def _evidence(row):
    return core.Evidence(row['id'], row['snapshot'], row['path'], lines=tuple(map(tuple, row['lines'])),
                         provenance=row['provenance'], backend=row.get('backend'))


def _row(key, path, snapshot, start, end, masked, reason, provenance='ast_fallback', backend='python-ast'):
    return {'id': common.snippet_id(key, path, start, end, snapshot), 'path': path, 'snapshot': snapshot,
            'start': start, 'end': end, 'lines': [[n, masked[n - 1]] for n in range(start, end + 1)],
            'reason': reason, 'provenance': provenance, 'backend': backend}


def _admit(store, row, cap=SOURCE_CAP):
    ev = _evidence(row)
    if store.covers(row['snapshot'], row['path'], row['start'], row['end']):
        return False, 'already_supplied'
    trial = copy.deepcopy(store)
    trial.add(ev)
    if trial.union_tokens() > cap:
        return False, 'source_cap_complete_unit_omitted'
    store.add(ev)
    return True, None


def _descriptor(snippets, metadata, store):
    desc = {'snippets': snippets, 'metadata': metadata, 'source_reference_tokens': store.union_tokens(),
            'source_union_sha256': store.union_hash(), 'metadata_reference_tokens': sum(common.ref_tokens(m['content']) for m in metadata)}
    desc['packet_sha256'] = _sha(desc)
    return desc


def _prepare_ast(inputs):
    key, root = inputs['key'], inputs['data']['workspace_root']
    store, snippets, audit, head_info = core.EvidenceStore(), [], [], []
    for file in sorted(inputs['diff'].files, key=lambda f: f.path):
        path = file.path
        if not path.endswith('.py'):
            audit.append({'path': path, 'state': 'unsupported_language'})
            continue
        head = _safe_path(root, file.new_path).read_text() if file.new_path else ''
        base = reverse_diff(head, file)
        for snapshot, text in [('head', head), ('base', base)]:
            try:
                candidates, funcs, tree, note = _ast_units(text, file, snapshot)
                masked = _masked(text)
            except (SyntaxError, ValueError, tokenize.TokenError) as exc:
                audit.append({'path': path, 'snapshot': snapshot, 'state': 'parse_unavailable', 'error': type(exc).__name__})
                continue
            note.update(path=path, state='parsed', source_sha256=hashlib.sha256(text.encode()).hexdigest(), admissions=[])
            for _, start, end, kind, name in candidates:
                row = _row(key, path, snapshot, start, end, masked,
                           f'component AST {snapshot} {kind} {name}; complete selected range; python-ast, no native binding')
                admitted, why = _admit(store, row)
                note['admissions'].append({'start': start, 'end': end, 'kind': kind, 'name': name,
                                            'admitted': admitted, 'omitted_reason': why})
                if admitted:
                    snippets.append(row)
            audit.append(note)
            if snapshot == 'head':
                head_info.append((path, text, tree, funcs, _changed_lines(file, 'head')[0]))
    return _descriptor(snippets, [], store), store, audit, head_info


def _char_column(line, utf8_column):
    return len(line.encode('utf-8')[:utf8_column].decode('utf-8'))


def _utf16_column(line, char_column):
    return len(line[:char_column].encode('utf-16-le')) // 2 + 1


def _anchors(head_info):
    uses, declarations = [], []
    for path, text, tree, funcs, changed in head_info:
        lines = text.splitlines()
        selected_nodes = {n for f in funcs for n in ast.walk(f)}
        for node in ast.walk(tree):
            if not hasattr(node, 'lineno'):
                continue
            if node not in selected_nodes and node.lineno not in changed:
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                name, lineno = node.id, node.lineno
                col = _char_column(lines[lineno - 1], node.col_offset)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                name, lineno = node.attr, node.end_lineno
                col = _char_column(lines[lineno - 1], node.end_col_offset) - len(name)
            else:
                continue
            uses.append({'path': path, 'line': lineno, 'column': _utf16_column(lines[lineno - 1], col),
                         'name': name, 'kind': 'lexical_use_site', 'changed_line': lineno in changed})
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        for f in funcs:
            name_token = next((t for t in tokens if t.type == tokenize.NAME and t.string == f.name
                               and t.start[0] == f.lineno and t.start[1] >= _char_column(lines[f.lineno - 1], f.col_offset)), None)
            if name_token:
                declarations.append({'path': path, 'line': f.lineno,
                    'column': _utf16_column(lines[f.lineno - 1], name_token.start[1]),
                    'name': f.name, 'kind': 'changed_function_declaration'})
    uses = sorted({(u['path'], u['line'], u['column'], u['name']): u for u in uses}.values(),
                  key=lambda u: (not u['changed_line'], u['path'], u['line'], u['column'], u['name']))
    declarations.sort(key=lambda u: (u['path'], u['line'], u['column'], u['name']))
    planned = [('go_to_definition', uses[0])] if uses else []
    if declarations:
        planned.append(('find_references', declarations[0]))
    elif len(uses) > 1:
        planned.append(('go_to_definition', uses[1]))
    return planned[:2]


def _default_resolver(root, all_paths):
    from review_agent.lsp.client import LspResolver
    return LspResolver(root, all_paths, timeout_seconds=90, initialize_timeout_seconds=90)


def _symbol(anchor):
    from review_agent.code_intelligence.tree_sitter_index import StructuralSymbol
    return StructuralSymbol(path=anchor['path'], name=anchor['name'], kind=anchor['kind'],
                            line_start=anchor['line'], line_end=anchor['line'],
                            column_start=anchor['column'], column_end=anchor['column'] + len(anchor['name']),
                            language='python', source='python-ast-lexical-anchor')


def _unit_for_location(root, location, source_reads=None):
    path, line = location['path'], location['line_start']
    target = _safe_path(root, path)
    event = {'path': path, 'locator_line': line, 'purpose': 'native_target_complete_unit_selection',
             'read_attempted': True, 'read_succeeded': False}
    if source_reads is not None:
        source_reads.append(event)
    try:
        text = target.read_text()
    except Exception as exc:
        event['read_error'] = type(exc).__name__
        raise
    event['read_succeeded'] = True
    event['source_sha256'] = hashlib.sha256(text.encode()).hexdigest()
    tree = ast.parse(text)
    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
             and _start(n) <= line <= n.end_lineno]
    if not nodes:
        return None
    node = min(nodes, key=lambda n: (n.end_lineno - _start(n), -n.lineno))
    return text, _start(node), node.end_lineno, type(node).__name__, node.name


def _lsp_state(status, operation):
    row = status.get('python', {})
    op = {'go_to_definition': 'definition', 'find_references': 'references'}[operation]
    return row.get('operations', {}).get(op, {}).get('state', row.get('state', 'unavailable'))


class _NativeRpcMeter:
    """Count successful lookup-message writes, independently of resolver attempts.

The product resolver increments its operation count before opening the source and
sending the RPC. That statistic is therefore retained separately. This per-instance
instrumentation observes the unchanged writer and sends no additional request.
Initialization, notifications, server replies and shutdown are excluded.
"""
    METHODS = frozenset(('textDocument/definition', 'textDocument/references'))

    def __init__(self, resolver):
        self.resolver = resolver
        self.original_client = getattr(resolver, '_client', None)
        self.supported = callable(self.original_client)
        self.events, self.clients = [], []
        if not self.supported:
            return

        def get_client(language):
            client = self.original_client(language)
            if client is None or any(c is client for c, _ in self.clients):
                return client
            writer = getattr(client, '_write', None)
            if not callable(writer):
                self.supported = False
                return client
            self.clients.append((client, writer))

            def write(message):
                method = message.get('method') if isinstance(message, dict) else None
                event = None
                if method in self.METHODS:
                    event = {'method': method, 'request_id': message.get('id'), 'write_succeeded': False}
                    self.events.append(event)
                try:
                    value = writer(message)
                except Exception as exc:
                    if event is not None:
                        event['error'] = type(exc).__name__
                    raise
                if event is not None:
                    event['write_succeeded'] = True
                return value

            client._write = write
            return client

        resolver._client = get_client

    def restore(self):
        if callable(self.original_client):
            self.resolver._client = self.original_client
        for client, writer in self.clients:
            client._write = writer

    @property
    def sent(self):
        return sum(e['write_succeeded'] for e in self.events) if self.supported else None


def _prepare_lsp(inputs, ast_desc, store, head_info, resolver_factory=None):
    key, root = inputs['key'], inputs['data']['workspace_root']
    snippets = copy.deepcopy(ast_desc['snippets'])
    requests, target_rows, notes, source_reads = [], [], [], []
    resolver, status = None, {}
    meter = None
    plan = _anchors(head_info)
    setup_error = None
    try:
        if plan:
            try:
                resolver = (resolver_factory or _default_resolver)(root, sorted(inputs['data']['workspace_sources']))
                meter = _NativeRpcMeter(resolver)
            except Exception as exc:
                setup_error = f'{type(exc).__name__}: {str(exc)[:180]}'
        for i, (operation, anchor) in enumerate(plan, 1):
            request = {'id': f'component-initial-{i}', 'operation': operation, 'anchor': anchor,
                       'path': anchor['path'], 'query': anchor['name'], 'start': anchor['line'], 'end': anchor['line'],
                       'logical_charge': 1, 'fallback_used': False, 'locations': []}
            requests.append(request)
            try:
                if resolver is None:
                    raise PacketError(f'native resolver unavailable: {setup_error}')
                locations = getattr(resolver, operation)(_symbol(anchor))
                status = resolver.status()
                request['state'] = _lsp_state(status, operation)
                for loc in locations:
                    row = dataclasses.asdict(loc) if dataclasses.is_dataclass(loc) else dict(loc)
                    request['locations'].append(row)
                    if not common.is_native(row.get('source', '')):
                        notes.append({'request_id': request['id'], 'state': 'non_native_location_rejected', 'location': row})
                        continue
                    try:
                        target = _safe_path(root, row['path'])
                        if not target.is_file():
                            raise PacketError('not a file')
                    except (PacketError, OSError) as exc:
                        notes.append({'request_id': request['id'], 'state': 'outside_workspace_or_non_python', 'location': row})
                        continue
                    target_rows.append((i, row['path'] == anchor['path'], row['path'], row['line_start'],
                                        row.get('column_start', 0), request['id'], row))
                request['result_count'] = len(locations)
                request['native_result_count'] = sum(common.is_native(x.get('source', '')) for x in request['locations'])
            except Exception as exc:
                request.update(state='failed', result_count=0, native_result_count=0,
                               error=f'{type(exc).__name__}: {str(exc)[:180]}')
        # Locator order is definition before references; each response ranks
        # cross-file targets before same-file targets, then path/line/column.
        for _, _, path, _, _, locator_id, loc in sorted(target_rows, key=lambda t: t[:5]):
            try:
                unit = _unit_for_location(root, loc, source_reads)
            except (PacketError, OSError, SyntaxError, ValueError) as exc:
                notes.append({'locator_id': locator_id, 'path': path, 'state': 'unit_unavailable', 'error': type(exc).__name__})
                continue
            if unit is None:
                notes.append({'locator_id': locator_id, 'path': path, 'state': 'no_enclosing_function_or_class'})
                continue
            text, start, end, kind, name = unit
            if store.covers('head', path, start, end):
                notes.append({'locator_id': locator_id, 'path': path, 'start': start, 'end': end, 'state': 'already_supplied'})
                continue
            read = {'id': 'component-initial-body', 'operation': 'read_lines', 'path': path, 'query': None,
                    'start': start, 'end': end, 'locator_id': locator_id, 'logical_charge': 1,
                    'locator_provenance': 'native_lsp', 'body_selection': 'python-ast complete function/class',
                    'fallback_used': False}
            requests.append(read)
            try:
                row = _row(key, path, 'head', start, end, _masked(text),
                           f'component native LSP located {kind} {name}; complete body; locator={locator_id}',
                           provenance='native_lsp', backend=loc['source'])
                admitted, why = _admit(store, row)
                read.update(state='completed' if admitted else 'budget_blocked', admitted=admitted, omitted_reason=why)
                if admitted:
                    snippets.append(row)
            except (ValueError, SyntaxError, tokenize.TokenError) as exc:
                read.update(state='failed', admitted=False, error=type(exc).__name__)
            break  # At most one real complete-body read, including a blocked read.
    finally:
        try:
            if resolver is not None:
                try:
                    resolver.close()
                finally:
                    status = resolver.status()
        finally:
            if meter is not None:
                meter.restore()
    rows = []
    for r in requests:
        if r['operation'] == 'read_lines':
            rows.append(f"read_lines {r['path']}:{r['start']}-{r['end']} {r['state']}; full unit; native locator={r['locator_id']}")
        else:
            a = r['anchor']
            rows.append(f"{r['operation']} {a['path']}:{a['line']}:{a['column']} {a['name']} state={r['state']} native_results={sum(common.is_native(x.get('source','')) for x in r['locations'])}; fallback=false")
            for loc in sorted(r['locations'], key=lambda x: (x['path'], x['line_start'], x.get('column_start', 0))):
                rows.append(f"  location {loc['path']}:{loc['line_start']}-{loc['line_end']} [{loc.get('source', 'unknown')}] (position only)")
    metadata = []
    if requests:
        content, omitted = common.fit_listing('Initial native Pyright lookup audit. Locations are not source bodies or runtime observations.', rows, METADATA_CAP)
        metadata = [{'id': f'agentic:{key}:component-initial-lsp', 'content': content,
                     'path': plan[0][1]['path'], 'provenance': 'native_lsp', 'backend': 'lsp:pyright',
                     'content_kind': 'reference_edge', 'omitted_rows': omitted}]
    count = len(requests)
    assert count <= 3
    queries = {'count': count, 'requests': requests,
               'batches': [{'label': 'component_initial', 'count': count, 'requests': [r['id'] for r in requests],
                            'operations': [r['operation'] for r in requests], 'running_total': count}] if count else []}
    resolver_requests = status.get('python', {}).get('requests', 0 if resolver is None else None)
    acquisitions = [r for r in requests if r['operation'] == 'read_lines']
    physical = {'preparation_only': True, 'native_operation_requests': resolver_requests,
                'logical_locator_attempts': len(plan), 'body_reads': sum(r['operation'] == 'read_lines' for r in requests),
                'server_status': status, 'native_returned_locations': sum(common.is_native(x.get('source', ''))
                    for r in requests for x in r.get('locations', [])), 'notes': notes,
                'normalized_queries': count,
                'native_rpc_queries': meter.sent if meter is not None else (0 if resolver is None else None),
                'native_resolver_operation_requests': resolver_requests,
                'native_rpc_write_attempts': len(meter.events) if meter is not None and meter.supported else
                    (0 if resolver is None else None),
                'native_rpc_write_events': meter.events if meter is not None else [],
                'native_rpc_meter_available': meter.supported if meter is not None else resolver is None,
                'local_source_file_read_attempts': len(source_reads),
                'local_source_file_reads_succeeded': sum(r['read_succeeded'] for r in source_reads),
                'local_source_file_reads': source_reads,
                'local_body_acquisitions': len(acquisitions),
                'local_body_acquisitions_supplied': sum(r.get('admitted', False) for r in acquisitions),
                'cost_semantics': {
                    'normalized_queries': 'per-review allocated locator attempts plus at most one selected complete-body acquisition; not physical RPCs',
                    'native_rpc_queries': 'successful client writes of textDocument/definition or textDocument/references; excludes initialize, didOpen, configuration and shutdown; null if custom resolver cannot be metered',
                    'native_resolver_operation_requests': 'original resolver status requests statistic; may increment before RPC is sent',
                    'local_source_file_read_attempts': 'all read_text attempts while resolving native target locations to complete AST units, including duplicate targets; excludes AST seed reads and LSP server didOpen reads',
                    'local_body_acquisitions': 'selected read_lines budget charge, whether supplied or source-budget blocked; body uses the source already read for unit selection, no additional file read',
                    'body_reads': 'compatibility alias for local_body_acquisitions, not OS/file read count',
                    'native_operation_requests': 'compatibility alias for native_resolver_operation_requests, not exact RPC count'}}
    return _descriptor(snippets, metadata, store), queries, physical


def prepare_packet(key: str, product=None, *, resolver_factory=None) -> dict:
    """One physical preparation. Save its result once and share lsp/search bytes."""
    product = product or common.load_product()
    inputs = common.build_inputs(key, product=product, diff_only=True)
    ast_desc, store, audit, head_info = _prepare_ast(inputs)
    lsp_desc, queries, physical = _prepare_lsp(inputs, ast_desc, store, head_info, resolver_factory)
    return {'version': VERSION, 'key': key, 'authoritative': inputs['data']['authoritative'],
            'workspace_tree_sha256': inputs['data']['workspace_tree_sha256'], 'ast': ast_desc, 'lsp': lsp_desc,
            'initial_queries': queries, 'audit': {'ast': audit, 'physical_preparation': physical,
                'selection_policy': 'diff/changed-file AST only; no public-contract symbols, gold, historical seed or model findings',
                'source_cap': SOURCE_CAP, 'metadata_cap': METADATA_CAP, 'retry_count': 0}}


def _check_descriptor(desc):
    body = {k: v for k, v in desc.items() if k != 'packet_sha256'}
    if _sha(body) != desc['packet_sha256']:
        raise PacketError('prepared packet checksum mismatch')
    store = core.EvidenceStore()
    for row in desc['snippets']:
        if [n for n, _ in row['lines']] != list(range(row['start'], row['end'] + 1)):
            raise PacketError('non-contiguous prepared snippet')
        store.add(_evidence(row))
    if store.union_tokens() != desc['source_reference_tokens'] or store.union_tokens() > SOURCE_CAP:
        raise PacketError('prepared source budget mismatch')
    if store.union_hash() != desc['source_union_sha256']:
        raise PacketError('prepared source union mismatch')
    metadata_tokens = sum(common.ref_tokens(m['content']) for m in desc['metadata'])
    if metadata_tokens != desc['metadata_reference_tokens'] or metadata_tokens > METADATA_CAP:
        raise PacketError('prepared metadata budget exceeded')


def build_inputs(key: str, arm: str, prepared_packet=None, product=None) -> dict:
    if arm not in ARMS:
        raise PacketError(f'unknown component arm {arm}')
    product = product or common.load_product()
    inputs = common.build_inputs(key, product=product, diff_only=True)
    if arm == 'diff':
        inputs.update(component_initial_queries={'count': 0, 'requests': [], 'batches': []},
                      component_initial_metadata=[], component_packet_audit={'arm': arm, 'packet_sha256': _sha([])})
        return inputs
    if prepared_packet is None:
        if arm == 'ast':
            desc, _, audit, _ = _prepare_ast(inputs)
            prepared_packet = {'key': key, 'authoritative': inputs['data']['authoritative'], 'ast': desc, 'audit': {'ast': audit}}
        else:
            raise PacketError('lsp/search require one shared frozen prepared_packet; call prepare_packet explicitly')
    if prepared_packet['key'] != key or prepared_packet['authoritative'] != inputs['data']['authoritative']:
        raise PacketError('prepared packet authoritative inputs mismatch')
    desc = copy.deepcopy(prepared_packet['ast' if arm == 'ast' else 'lsp'])
    _check_descriptor(desc)
    snippets, evidence, index = [], [], {}
    for row in desc['snippets']:
        snippet = product.models.ContextSnippet(path=row['path'], start=row['start'], end=row['end'],
                    content=common.render_lines(dict(row['lines'])), reason=row['reason'], score=0.0, provenance='structural')
        snippets.append(snippet)
        evidence.append(_evidence(row))
        index[common.snippet_key(snippet)] = {'id': row['id'], 'snapshot': row['snapshot'],
            'product_id': common.product_snippet_id(key, row['path'], row['start'], row['end']), 'historical_id': None}
    inputs['packet'] = dataclasses.replace(inputs['packet'], snippets=snippets)
    inputs.update(seed_evidence=evidence, seed_snippet_index=index, seed_id_map={}, seed_blocks=[],
                  component_initial_metadata=desc['metadata'],
                  component_initial_queries=copy.deepcopy(prepared_packet['initial_queries']) if arm != 'ast'
                      else {'count': 0, 'requests': [], 'batches': []},
                  component_packet_audit={'arm': arm, 'packet_sha256': desc['packet_sha256'],
                      'source_union_sha256': desc['source_union_sha256'], 'source_reference_tokens': desc['source_reference_tokens'],
                      'metadata_reference_tokens': desc['metadata_reference_tokens'],
                      'physical_preparation_shared': arm in ('lsp', 'search')})
    return inputs
