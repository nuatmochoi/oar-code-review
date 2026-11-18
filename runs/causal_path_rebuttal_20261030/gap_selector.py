"""Critic-directed, source-anchored missing-body selection (no verdict gate).

Only summary/observation gap statements and explicit context requests create
needs. Exact supplied code tokens anchor native lookups; unbound *explicit code*
names may use a charged native workspace symbol query. Python ASTs delimit
bodies and lexical bindings, never certify runtime dispatch. next_request is
I/O-free; constructor/observe index only supplied/native paths after manifest
verification, recording every physical read. Acquired/admitted source is kept
separate from source actually delivered to the critic.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import keyword
import math
import re
from pathlib import Path

import bootstrap  # noqa: F401  (adds the immutable dependencies, no product load)
from ace_review.protocol_hypothesis import mask_source
from body_frontier import ScopedIndex

VERSION = 1
MAX_ITEMS = 96
READ_MAX_LINES = 200
LOCATORS = frozenset({'go_to_definition', 'find_references', 'symbol_search',
                     'document_symbols', 'incoming_calls', 'outgoing_calls'})
# Fixed linguistic triggers, not defect types, benchmark identities or contracts.
GAP_RE = re.compile(r'\b(?:missing|absent|omitted|unseen|unavailable|unknown|unclear|'
                    r'incomplete|unverified|not (?:shown|supplied|provided|visible)|'
                    r'need(?:s|ed)?|inspect|confirm|verify|cannot (?:tell|determine|establish))\b|'
                    r'누락|미제공|미공급|미확인|부족|불명|알 수 없|확인(?:이|할|해야|필요)|'
                    r'추가.{0,12}(?:근거|코드|본문|확인)|본문.{0,12}(?:필요|없|확인)|'
                    r'제공되지|제시되지|확인되지|포함되지|보이지|확정할 수 없|판단할 수 없', re.I)
CONSUMER_RE = re.compile(r'\b(?:caller|callers|consumer|consumers|usage|references?)\b|호출자|사용처|소비|참조', re.I)
EXCEPTION_RE = re.compile(r'\b(?:exception|exceptions|raises?|except|catch|handler)\b|예외|처리 경로', re.I)
IDENT_RE = re.compile(r'(?<![\w/])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*')
SCOPES = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
NON_SYMBOLS = set(keyword.kwlist) | {'True', 'False', 'None', 'self', 'cls'}


def _sha(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(value).hexdigest()


def _expr(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _expr(node.value)
        return base + '.' + node.attr if base else ''
    return ''


def _local_walk(node):
    """Walk one lexical scope; nested function/class bodies never leak into it."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, SCOPES):
            yield child
        else:
            yield from _local_walk(child)


def _start(node):
    return min([node.lineno] + [x.lineno for x in getattr(node, 'decorator_list', [])])


def _chunks(numbers, size=READ_MAX_LINES):
    out = []
    for n in sorted(numbers):
        if out and n == out[-1][-1] + 1 and len(out[-1]) < size:
            out[-1].append(n)
        else:
            out.append([n])
    return out


def _syntax_terms(code):
    """Code expressions contribute API targets, never arbitrary argument Names."""
    try:
        tree = ast.parse(code.strip(), mode='eval')
    except SyntaxError:
        return None
    targets, argument_names, keywords = [], set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _expr(node.func) or (node.func.attr if isinstance(node.func, ast.Attribute) else '')
            if name and name not in targets: targets.append(name)
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                argument_names.update(n.id for n in ast.walk(arg) if isinstance(n, ast.Name))
            keywords.update(k.arg for k in node.keywords if k.arg)
        elif isinstance(node, ast.Attribute):
            name = _expr(node) or node.attr
            if name and name not in targets: targets.append(name)
    if isinstance(tree.body, ast.Name):
        targets.append(tree.body.id)
    targets = [t for t in targets if t not in NON_SYMBOLS]
    argument_names -= {t for t in targets if '.' not in t}
    return targets, argument_names, keywords


def _inline_calls(text):
    """Extract balanced call expressions for syntax analysis, without execution."""
    spans, until = [], -1
    for match in re.finditer(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*\(', text):
        if match.start() < until: continue
        begin, pos, depth, quote, escape = match.start(), match.end()-1, 0, None, False
        end = None
        while pos < len(text):
            char = text[pos]
            if quote:
                if escape: escape = False
                elif char == '\\': escape = True
                elif char == quote: quote = None
            elif char in ('"', "'"): quote = char
            elif char == '(': depth += 1
            elif char == ')':
                depth -= 1
                if depth == 0:
                    end = pos+1
                    chain = re.match(r'\.[A-Za-z_]\w*\s*\(', text[end:])
                    if chain:
                        pos = end+chain.end()-1
                        continue
                    break
            pos += 1
        if end and _syntax_terms(text[begin:end]) is not None:
            spans.append((begin, end, text[begin:end])); until = end
    return spans


def _terms(text, explicit=False):
    """Bind visible API names; unbound search requires an explicit code target.

    Call argument names are removed even if they happen to name a local variable
    in another supplied function. Keyword API names can match supplied code but
    do not create standalone workspace searches.
    """
    if explicit:
        parsed = _syntax_terms(text)
        if parsed is not None:
            targets, ignored, keys = parsed
            return sorted(set(targets) | keys), list(dict.fromkeys(targets))
    parsed_targets, ignored, keys = [], set(), set()
    fragments = [(m.start(), m.end(), m.group(1)) for m in re.finditer(r'`([^`]+)`', text)]
    fragments += _inline_calls(text)
    rest = list(text)
    for start, end, code in fragments:
        parsed = _syntax_terms(code)
        if parsed is None: continue
        targets, args, kws = parsed
        parsed_targets.extend(targets); ignored |= args; keys |= kws
        rest[start:end] = ' '*(end-start)
    ordinary = set(IDENT_RE.findall(''.join(rest))) - NON_SYMBOLS - ignored
    marked = sorted(x for x in ordinary if '.' in x or '_' in x or any(c.isupper() for c in x[1:]))
    if explicit and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', text.strip()):
        marked.append(text.strip())
    symbols = list(dict.fromkeys(parsed_targets + marked))
    return sorted(ordinary | set(parsed_targets) | keys), symbols


class GapSelector:
    def __init__(self, critic_payload, supplied_sources, saved_frontier, root, manifest):
        self.root = Path(root).resolve()
        rows = manifest.get('files', manifest) if isinstance(manifest, dict) else manifest
        pairs = [(r['path'], r['sha256']) for r in rows] if isinstance(rows, list) else list(rows.items())
        self.manifest = {}
        for path, value in pairs:
            digest = value.get('sha256', value.get('source_sha256')) if isinstance(value, dict) else value
            if path in self.manifest:
                raise ValueError('duplicate_manifest_path')
            self.manifest[path] = digest.lower() if isinstance(digest, str) else digest
        self.data = {'version': VERSION, 'policy': {
            'input_fields': ['review.summary', 'review.observations[].title/body', 'context_requests[]'],
            'gap_expression': GAP_RE.pattern,
            'tie_order': 'known body, anchored locator, unbound native symbol; request/observation/summary; path,line,column,name',
            'no_semantic_verdict': True, 'native_binding_inferred': False,
            'newly_admitted_is_not_model_delivered': True,
            'scope_and_receiver_associations': 'AST lexical bindings only, not runtime types',
            'body_limit_lines': READ_MAX_LINES, 'locator_minimum_remaining_queries': 2,
            'max_items_per_collection': MAX_ITEMS,
        }, 'critic_sha256': _sha(critic_payload), 'needs': [], 'anchors': [], 'units': [],
            'locators': [], 'events': [], 'source_reads': [], 'decisions': [], 'pending': {},
            'initial_delivered': {}, 'newly_admitted': {}, 'attempted_requests': [],
            'saved_frontier_sha256': _sha(saved_frontier or {}),
            'saved_frontier_note': 'Not trusted as coverage or as a new source scope; source anchors reconstructed.'}
        self.index = ScopedIndex(self.root, [])  # we supply verified text, own physical audit below
        self._files = {}
        self._invalid_paths = set()
        self._sources = {}
        self._attempted = set()
        self._ingest(supplied_sources, initial=True)
        self._extract_needs(critic_payload)
        self._rebuild_anchors()
        self._bind_needs()

    def _index(self, path):
        if path in self._files:
            return self._files[path]
        if path in self._invalid_paths:
            return None
        self._invalid_paths.add(path)
        try:
            p = self.index.path(path)
            expected = self.manifest.get(path)
            if isinstance(expected, dict):
                expected = expected.get('sha256', expected.get('source_sha256'))
            if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
                raise ValueError('path_not_in_head_manifest')
        except (ValueError, TypeError) as exc:
            self.data['events'].append({'kind': 'scope_rejected', 'path': path, 'reason': str(exc)})
            return None
        event = {'path': path, 'purpose': 'explicit_supplied_or_native_scope_index',
                 'read_succeeded': False, 'hash_verified': False, 'model_delivered': False}
        self.data['source_reads'].append(event)
        try:
            raw = p.read_bytes()
            event.update(read_succeeded=True, source_sha256=_sha(raw), byte_count=len(raw))
            if _sha(raw) != expected:
                raise ValueError('manifest_hash_mismatch')
            event['hash_verified'] = True
            text = raw.decode('utf-8')
            d = self.index.index(path, text)
            d['masked_lines'] = mask_source(text).splitlines()
            self._files[path] = d
            return d
        except (ValueError, OSError, SyntaxError, UnicodeError) as exc:
            event['error'] = str(exc)
            self.data['events'].append({'kind': 'index_rejected', 'path': path, 'reason': str(exc)})
            return None

    def _ingest(self, rows, *, initial, request=None):
        for row in rows or []:
            path = row.get('file', row.get('path'))
            snapshot = row.get('snapshot', 'head')
            if not path or snapshot != 'head' or row.get('admitted') is False:
                self.data['events'].append({'kind': 'source_not_anchor', 'path': path,
                                            'snapshot': snapshot, 'reason': 'head_admitted_source_only'})
                continue
            if request and (path != request.get('path') or request.get('operation') != 'read_lines'):
                self.data['events'].append({'kind': 'admission_rejected', 'path': path, 'reason': 'outside_read_request'})
                continue
            d = self._index(path)
            if d is None:
                continue
            accepted = []
            for pair in row.get('lines', []):
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    continue
                n, text = pair
                if (not isinstance(n, int) or isinstance(n, bool) or not isinstance(text, str)
                    or n < 1 or n > len(d['masked_lines'])
                    or (request and not request['start'] <= n <= request['end'])
                    or d['masked_lines'][n - 1] != text):
                    self.data['events'].append({'kind': 'source_line_rejected', 'path': path,
                                                'line': n, 'reason': 'not_exact_masked_head_or_request_range'})
                    continue
                key = (path, n)
                entry = self._sources.setdefault(key, {'text': text, 'evidence_ids': [], 'origins': []})
                if row.get('evidence_id') and row['evidence_id'] not in entry['evidence_ids']:
                    entry['evidence_ids'].append(row['evidence_id'])
                origin = 'initial_delivered' if initial else 'newly_admitted_not_yet_delivered'
                if origin not in entry['origins']:
                    entry['origins'].append(origin)
                accepted.append(n)
            target = self.data['initial_delivered' if initial else 'newly_admitted']
            target[path] = sorted(set(target.get(path, [])) | set(accepted))

    def _extract_needs(self, payload):
        review = payload.get('review', {}) if isinstance(payload, dict) else {}
        fields = []
        if isinstance(review.get('summary'), str):
            fields.append(('/review/summary', review['summary'], {}, 2, False))
        for i, obs in enumerate(review.get('observations', []) or []):
            if isinstance(obs, dict):
                text = '\n'.join(str(obs.get(k, '')) for k in ('title', 'body'))
                fields.append((f'/review/observations/{i}', text, obs, 1, False))
        for i, req in enumerate(payload.get('context_requests', []) or []):
            if isinstance(req, dict):
                text = '\n'.join(str(req.get(k) or '') for k in ('query', 'name', 'symbol', 'reason'))
                fields.append((f'/context_requests/{i}', text, req, 0, True))
        for ptr, text, meta, priority, explicit in fields:
            if not explicit and not GAP_RE.search(text):
                continue
            words, symbols = _terms(str(meta.get('reason') or '')) if explicit else _terms(text)
            if explicit:
                request_symbols = []
                for key in ('query', 'name', 'symbol'):
                    if isinstance(meta.get(key), str):
                        query_words, query_symbols = _terms(meta[key], explicit=True)
                        words.extend(query_words); request_symbols.extend(query_symbols)
                symbols = request_symbols + symbols
            refs = []
            for ref in meta.get('evidence', []) or []:
                if isinstance(ref, dict):
                    eid = ref.get('evidence_id', ref.get('id'))
                    if isinstance(eid, str): refs.append(eid)
            need = {'id': 'gap-' + _sha([ptr, text])[:20], 'pointer': ptr, 'text': text,
                    'text_sha256': _sha(text), 'words': sorted(set(words)), 'explicit_symbols': list(dict.fromkeys(symbols)),
                    'evidence_ids': sorted(set(refs)), 'priority': priority,
                    'requested_operation': meta.get('operation'), 'path_hint': meta.get('path', meta.get('file')),
                    'line_hint': meta.get('start', meta.get('line')), 'end_hint': meta.get('end'),
                    'relation': 'exception_route' if EXCEPTION_RE.search(text) else (
                        'value_consumer' if CONSUMER_RE.search(text) else 'callee_body'),
                    'state': 'unbound'}
            if len(self.data['needs']) >= MAX_ITEMS:
                self.data['events'].append({'kind': 'need_overflow', 'pointer': ptr, 'text_sha256': need['text_sha256']})
            else:
                self.data['needs'].append(need)

    def _scope(self, d, node):
        p = d['parents'].get(node)
        while p is not None and not isinstance(p, SCOPES):
            p = d['parents'].get(p)
        return p

    def _binding(self, path, d, node, receiver):
        scope = self._scope(d, node)
        root_name = receiver.split('.')[0] if receiver else ''
        current = scope
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = [*current.args.posonlyargs, *current.args.args, *current.args.kwonlyargs]
                args += [x for x in (current.args.vararg, current.args.kwarg) if x]
                stores = [n for n in _local_walk(current) if isinstance(n, ast.Name)
                          and isinstance(n.ctx, (ast.Store, ast.Del)) and n.id == root_name]
                arg = next((a for a in args if a.arg == root_name), None)
                if arg or stores:
                    rebound = any(n.lineno <= node.lineno for n in stores)
                    owner = d['parents'].get(current)
                    kind = 'parameter' if arg else 'local'
                    # Method receiver only if the direct enclosing scope is a class;
                    # a nested function's parameter named self is a different binding.
                    if arg and args and arg is args[0] and isinstance(owner, ast.ClassDef) and not rebound:
                        kind = 'method_receiver'
                    return {'id': _sha([path, current.lineno, root_name])[:20],
                            'name': root_name, 'scope_line': current.lineno, 'kind': kind,
                            'ambiguous_rebinding': rebound,
                            'owner': owner.name if kind == 'method_receiver' else None,
                            'annotation': _expr(arg.annotation) if arg and arg.annotation else None}
            current = self._scope(d, current)
        return {'id': _sha([path, 'module', root_name])[:20], 'name': root_name,
                'scope_line': 0, 'kind': 'module_or_unresolved', 'ambiguous_rebinding': False, 'owner': None}

    def _rebuild_anchors(self):
        anchors = []
        for path in sorted(self._files):
            d = self._files[path]
            for n in ast.walk(d['tree']):
                if isinstance(n, ast.Attribute):
                    name, line = n.attr, n.end_lineno
                    raw = d['text'].splitlines()[line-1]
                    start = len(raw.encode()[:n.end_col_offset].decode()) - len(name)
                    expression, receiver, kind = _expr(n), _expr(n.value), 'attribute'
                elif isinstance(n, ast.Name):
                    name, line = n.id, n.lineno
                    raw = d['text'].splitlines()[line-1]
                    start = len(raw.encode()[:n.col_offset].decode())
                    expression, receiver, kind = name, '', 'name'
                elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name, line = n.name, n.lineno
                    raw = d['text'].splitlines()[line-1]
                    cp = len(raw.encode()[:n.col_offset].decode())
                    match = re.search(r'\b(?:async\s+def|def|class)\s+(' + re.escape(name) + r')\b', raw[cp:])
                    if not match: continue
                    start = cp + match.start(1)
                    expression, receiver, kind = name, '', 'declaration'
                else:
                    continue
                source = self._sources.get((path, line))
                if source is None or source['text'][start:start+len(name)] != name or name in NON_SYMBOLS:
                    continue
                scope = n if kind == 'declaration' else self._scope(d, n)
                sid = _sha([path, getattr(scope, 'lineno', 0), type(scope).__name__])[:20]
                anchor = {'path': path, 'line': line, 'column': len(raw[:start].encode('utf-16-le'))//2+1,
                          'name': name, 'expression': expression, 'receiver': receiver, 'kind': kind,
                          'scope_id': sid, 'scope_line': getattr(scope, 'lineno', 0),
                          'receiver_binding': self._binding(path, d, n, receiver or name),
                          'evidence_ids': list(source['evidence_ids']), 'origins': list(source['origins']),
                          'line_sha256': _sha(source['text']),
                          'syntactic_role': 'call_target' if isinstance(d['parents'].get(n), ast.Call) and d['parents'][n].func is n else kind}
                anchor['id'] = 'anchor-' + _sha([path, line, anchor['column'], name])[:20]
                anchors.append(anchor)
        self.data['anchors'] = sorted({a['id']: a for a in anchors}.values(), key=lambda a: (a['path'], a['line'], a['column'], a['name']))

    def _add_unit(self, path, rec, need, *, relation, origin, symbol_rank=0):
        uid = 'unit-' + _sha([path, rec['start'], rec['end']])[:20]
        unit = next((u for u in self.data['units'] if u['id'] == uid), None)
        edge = {'need_id': need['id'], 'relation': relation, **copy.deepcopy(origin)}
        if unit:
            if edge not in unit['edges']: unit['edges'].append(edge)
            unit['priority'] = min(unit['priority'], need['priority'])
            unit['symbol_rank'] = min(unit['symbol_rank'], symbol_rank)
            return
        if len(self.data['units']) >= MAX_ITEMS:
            self.data['events'].append({'kind': 'unit_overflow', 'id': uid})
            return
        self.data['units'].append({'id': uid, 'path': path, 'snapshot': 'head', 'start': rec['start'],
            'end': rec['end'], 'name': rec['name'], 'qualname': rec['qualname'], 'kind': rec['kind'],
            'source_sha256': self._files[path]['sha256'], 'priority': need['priority'], 'symbol_rank': symbol_rank, 'edges': [edge]})

    def _enclosing(self, path, line):
        return self.index.at(path, line, prefer_class_header=True)

    def _locator(self, need, anchor=None, *, symbol=None, member_hint=None, symbol_rank=0):
        op = need.get('requested_operation')
        if op not in LOCATORS or op in ('symbol_search', 'document_symbols'):
            op = 'find_references' if need['relation'] in ('value_consumer', 'exception_route') else 'go_to_definition'
        if anchor is None:
            op = 'symbol_search'
            fields = {'query': symbol, 'name': symbol, 'binding': 'unbound_native_candidate_search'}
        else:
            fields = {k: anchor[k] for k in ('path', 'line', 'column', 'name')}
            fields.update(anchor_id=anchor['id'], binding='exact_supplied_use_site',
                          scope_id=anchor['scope_id'], receiver_binding=anchor['receiver_binding'],
                          receiver_expression=anchor['receiver'], source_expression=anchor['expression'])
        key = [op, fields.get('path'), fields.get('line'), fields.get('column'), fields['name']]
        lid = 'locator-' + _sha(key)[:20]
        old = next((l for l in self.data['locators'] if l['id'] == lid), None)
        edge = {'need_id': need['id'], 'relation': need['relation'], 'member_hint': member_hint}
        if old:
            if edge not in old['edges']: old['edges'].append(edge)
            old['priority'] = min(old['priority'], need['priority'])
            old['symbol_rank'] = min(old['symbol_rank'], symbol_rank)
        elif len(self.data['locators']) < MAX_ITEMS:
            self.data['locators'].append({'id': lid, 'operation': op, **fields, 'edges': [edge],
                                         'priority': need['priority'], 'symbol_rank': symbol_rank, 'state': 'unresolved'})
        else:
            self.data['events'].append({'kind': 'locator_overflow', 'id': lid})

    def _bind_needs(self):
        for need in self.data['needs']:
            matched_terms = set()
            suppressed_locals = set()
            candidates = []
            words = set(need['words']) | set(need['explicit_symbols'])
            # A qualified name constrains the receiver; do not reduce left.run to
            # the bare run token and accidentally select right.run.
            qualified_leaves = {s.rsplit('.', 1)[-1] for s in words if '.' in s}
            exact_qualified = {a['expression'] for a in self.data['anchors'] if a['expression'] in words and '.' in a['expression']}
            for a in self.data['anchors']:
                if need.get('path_hint') and a['path'] != need['path_hint']: continue
                if need['evidence_ids'] and not set(need['evidence_ids']) & set(a['evidence_ids']): continue
                matched = {s for s in words if s == a['expression'] or ('.' not in s and s == a['name'] and s not in qualified_leaves)}
                # A qualified declaration request may locate a supplied receiver
                # first, then select only that native class's exact member.
                roots = {s for s in words if '.' in s and s not in exact_qualified and s.split('.')[0] == a['name'] and a['kind'] != 'attribute'}
                if not matched and not roots: continue
                # A local value name is not a globally meaningful API symbol.
                # Without an explicit source range, keep it unresolved instead
                # of borrowing an unrelated lexical scope's parameter.
                scoped_hint = (need.get('path_hint') == a['path'] and isinstance(need.get('line_hint'), int)
                               and need['line_hint'] <= a['line'] <= (need.get('end_hint') or need['line_hint']))
                local_value = (a['kind'] == 'name' and a['syntactic_role'] != 'call_target'
                               and a['receiver_binding']['kind'] in ('parameter', 'local', 'method_receiver'))
                if local_value and not scoped_hint:
                    suppressed_locals |= matched | roots
                    continue
                member = sorted(roots)[0].split('.')[-1] if roots and not matched else None
                matched_terms |= matched | roots
                rank = min((need['explicit_symbols'].index(t) for t in matched | roots if t in need['explicit_symbols']), default=MAX_ITEMS)
                candidates.append((a, member, rank))
            # Explicit range requests can complete only a currently supplied scope.
            if not candidates and need.get('path_hint') in self._files and isinstance(need.get('line_hint'), int):
                path, line = need['path_hint'], need['line_hint']
                if (path, line) in self._sources:
                    rec = self._enclosing(path, line)
                    if rec:
                        self._add_unit(path, rec, need, relation='explicit_supplied_scope_gap',
                                       origin={'selection': 'verified_ast_boundary', 'anchor_line': line})
                        need['state'] = 'anchored_scope'
            for a, member, rank in candidates:
                rec = self._enclosing(a['path'], a['line'])
                if rec and (a['kind'] == 'declaration' or need['relation'] == 'exception_route'):
                    self._add_unit(a['path'], rec, need, relation='known_missing_unit_body',
                                   origin={'selection': 'verified_ast_boundary', 'anchor_id': a['id']}, symbol_rank=rank)
                if need['relation'] == 'exception_route' and rec and rec['kind'] == 'function':
                    decl = next((x for x in self.data['anchors'] if x['path'] == a['path'] and
                                 x['kind'] == 'declaration' and x['name'] == rec['name'] and
                                 x['line'] == rec['node'].lineno), None)
                    if decl: self._locator(need, decl, symbol_rank=rank)
                    elif a['kind'] != 'declaration': self._locator(need, a, member_hint=member, symbol_rank=rank)
                elif a['kind'] != 'declaration' or need['relation'] == 'value_consumer' or member:
                    self._locator(need, a, member_hint=member, symbol_rank=rank)
            if candidates:
                need['state'] = 'anchored'
                need['anchor_ids'] = sorted({a['id'] for a, _, _ in candidates})
                if len({a['receiver_binding']['id'] for a, _, _ in candidates}) > 1:
                    need['multiple_bindings'] = True
            for symbol in need['explicit_symbols']:
                if symbol not in matched_terms and symbol not in suppressed_locals:
                    self._locator(need, symbol=symbol, symbol_rank=need['explicit_symbols'].index(symbol))
            if suppressed_locals:
                need['unbound_local_symbols'] = sorted(suppressed_locals)
            if not candidates and need['state'] != 'anchored_scope':
                need['state'] = 'unbound_native_search' if set(need['explicit_symbols'])-suppressed_locals else 'unbound_subject'

    def next_request(self, remaining_queries, remaining_source_tokens):
        """Reserve one atomic request. No filesystem/LSP/model calls occur here."""
        if self.data['pending'] or remaining_queries <= 0 or remaining_source_tokens <= 0:
            return None
        for unit in sorted(self.data['units'], key=lambda u: (u['priority'], u['symbol_rank'], u['kind'] == 'class', u['end']-u['start'], u['path'], u['start'])):
            missing = [n for n in range(unit['start'], unit['end']+1) if (unit['path'], n) not in self._sources]
            # Attempted lines stay missing if admission failed; never reread
            # them, while still allowing other missing lines to be requested.
            unattempted = [n for n in missing if ('read_lines', unit['path'], n) not in self._attempted]
            for chunk in _chunks(unattempted):
                lines = self._files[unit['path']]['masked_lines']
                fit, size = [], 0
                for n in chunk:
                    cost = max(1, math.ceil((len(lines[n-1])+len(str(n))+3)/4))
                    if size + cost > remaining_source_tokens: break
                    fit.append(n); size += cost
                if not fit: continue
                req = {'operation': 'read_lines', 'path': unit['path'], 'start': fit[0], 'end': fit[-1],
                       'snapshot': 'head', 'unit_id': unit['id'], 'origin': 'critic-gap-selector',
                       'source_token_estimate': size, 'reason': 'Read missing body selected from critic/source relation',
                       'need_ids': sorted({e['need_id'] for e in unit['edges']})}
                return self._reserve(req)
        if remaining_queries < 2:
            self.data['decisions'].append({'kind': 'locator_budget_denied', 'remaining_queries': remaining_queries,
                                          'reason': 'reserve_one_body_query'})
            return None
        for loc in sorted(self.data['locators'], key=lambda l: (l['operation'] == 'symbol_search', l['priority'], l['symbol_rank'],
                    l.get('path', ''), l.get('line', 0), l.get('column', 0), l['name'], l['id'])):
            if loc['state'] != 'unresolved': continue
            req = {k: copy.deepcopy(v) for k, v in loc.items() if k not in ('state', 'id', 'priority', 'edges')}
            req.update(locator_id=loc['id'], origin='critic-gap-selector', reserved_body_queries=1,
                       need_ids=sorted({e['need_id'] for e in loc['edges']}),
                       reason='Locate critic-requested relation; a location is not body evidence')
            loc['state'] = 'pending'
            return self._reserve(req)
        return None

    def _reserve(self, request):
        request['id'] = 'gap-request-' + _sha(request)[:20]
        self.data['pending'][request['id']] = copy.deepcopy(request)
        self.data['decisions'].append({'kind': 'request_reserved', 'request': copy.deepcopy(request)})
        return request

    def observe(self, request, result, admitted_spans):
        pending = self.data['pending'].get(request.get('id'))
        if pending is None or pending != request:
            raise ValueError('unknown_or_modified_selector_request')
        self.data['pending'].pop(request['id'])
        self.data['attempted_requests'].append(copy.deepcopy(request))
        status = result.get('status', 'unknown')
        completed = status in ('completed', 'ok', 'success')
        if request['operation'] == 'read_lines':
            for n in range(request['start'], request['end']+1):
                self._attempted.add(('read_lines', request['path'], n))
            if completed:
                self._ingest_read_admission(request, result, admitted_spans)
                self._rebuild_anchors()
                self._bind_needs()
        else:
            loc = next(l for l in self.data['locators'] if l['id'] == request['locator_id'])
            locations = result.get('locations', []) if completed else []
            loc['state'] = 'located' if locations else ('empty' if completed else 'failed')
            ordered = sorted(locations, key=lambda p: (p.get('path', ''), p.get('start', 0), p.get('column', 0), p.get('name', '')))
            if len(ordered) > MAX_ITEMS:
                self.data['events'].append({'kind': 'native_location_overflow', 'count': len(ordered)-MAX_ITEMS,
                                            'omitted_sha256': _sha(ordered[MAX_ITEMS:])})
            for position in ordered[:MAX_ITEMS]:
                path, line = position.get('path'), position.get('start', position.get('line_start'))
                d = self._index(path) if isinstance(path, str) else None
                if not d or not isinstance(line, int) or not 1 <= line <= len(d['masked_lines']):
                    self.data['events'].append({'kind': 'native_location_rejected', 'location': copy.deepcopy(position)})
                    continue
                if request['operation'] == 'symbol_search':
                    requested = request['name'].rsplit('.', 1)[-1]
                    native_name = str(position.get('name') or '').split('(')[0].rsplit('.', 1)[-1]
                    if native_name != requested:
                        self.data['events'].append({'kind': 'native_symbol_name_mismatch', 'location': copy.deepcopy(position), 'requested': requested})
                        continue
                rec = self._enclosing(path, line)
                if rec is None:
                    self.data['events'].append({'kind': 'native_position_without_function_scope', 'location': copy.deepcopy(position)})
                    continue
                for edge in loc['edges']:
                    need = next(n for n in self.data['needs'] if n['id'] == edge['need_id'])
                    selection = rec
                    member = edge.get('member_hint')
                    if member and rec['kind'] == 'class':
                        members = [r for r in d['records'] if r['owner_node'] is rec['node'] and r['name'] == member
                                   and d['parents'].get(r['node']) is rec['node']]
                        if len(members) == 1: selection = members[0]
                    self._add_unit(path, selection, need, relation=edge['relation'], origin={
                        'selection': 'native_position_then_structural_member' if selection is not rec else 'native_position_then_ast_enclosing_body',
                        'native_operation': request['operation'], 'locator_id': loc['id'],
                        'native_location': copy.deepcopy(position), 'binding_certainty': 'candidate_only'}, symbol_rank=loc['symbol_rank'])
        self.data['events'].append({'kind': 'request_observed', 'request_id': request['id'], 'status': status,
                                    'admitted_is_delivered': False})

    def _ingest_read_admission(self, request, result, admitted_spans):
        # Admission cannot invent bytes absent from the executed read. Native
        # positions, metadata and frontier completion flags are never source.
        returned = {n: t for n, t in result.get('lines', [])
                    if isinstance(n, int) and not isinstance(n, bool) and isinstance(t, str)}
        checked = []
        for row in admitted_spans or []:
            row = copy.deepcopy(row)
            row['lines'] = [[n, t] for n, t in row.get('lines', []) if returned.get(n) == t]
            checked.append(row)
        self._ingest(checked, initial=False, request=request)

    def observe_external(self, request, result, admitted_spans):
        """Admit an executed fallback read without consuming a selector action.

        Own reservations, attempted request counts and locator states are left
        untouched. Newly available code may bind previously unbound needs and
        create ordinary new candidates; it is still not model-delivered source.
        """
        if (request.get('operation') != 'read_lines'
                or result.get('status') not in ('completed', 'ok', 'success')):
            self.data['events'].append({'kind': 'external_non_source_observed',
                'request_id': request.get('id'), 'operation': request.get('operation'),
                'status': result.get('status'), 'coverage_changed': False})
            return
        start, end = request.get('start'), request.get('end')
        if (type(start) is not int or type(end) is not int or start < 1 or end < start
                or end-start+1 > READ_MAX_LINES or not isinstance(request.get('path'), str)):
            raise ValueError('invalid_external_read_range')
        self._ingest_read_admission(request, result, admitted_spans)
        self._rebuild_anchors()
        self._bind_needs()
        self.data['events'].append({'kind': 'external_read_admission',
            'request_id': request.get('id'), 'path': request['path'], 'start': start, 'end': end,
            'admitted_is_delivered': False})

    def summary(self):
        out = copy.deepcopy(self.data)
        for unit in out['units']:
            expected = set(range(unit['start'], unit['end']+1))
            initial = set(out['initial_delivered'].get(unit['path'], [])) & expected
            admitted = set(out['newly_admitted'].get(unit['path'], [])) & expected
            unit.update(initial_delivered_lines=sorted(initial), newly_admitted_lines=sorted(admitted),
                        missing_lines=sorted(expected-initial-admitted),
                        initial_delivered_complete=expected <= initial,
                        acquired_and_admitted_complete=expected <= initial | admitted,
                        model_delivered_complete=expected <= initial)
        out['counters'] = {'native_locator_queries': sum(r['operation'] != 'read_lines' for r in out['attempted_requests']),
                           'body_read_queries': sum(r['operation'] == 'read_lines' for r in out['attempted_requests']),
                           'scoped_index_read_attempts': len(out['source_reads']),
                           'scoped_index_reads_succeeded': sum(r['read_succeeded'] for r in out['source_reads']),
                           'newly_admitted_lines': sum(map(len, out['newly_admitted'].values())),
                           'initial_delivered_lines': sum(map(len, out['initial_delivered'].values()))}
        return out
