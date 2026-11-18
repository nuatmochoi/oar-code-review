"""Bounded, change-driven Python body frontier.

ASTs select structural candidates, never certify a native or runtime binding.
Only explicitly scoped files are indexed. Planning performs no I/O. A unit is
complete only when all its actual source lines were admitted to model evidence.
No contracts, labels, case identities, historical findings or global name index
participate in selection.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from pathlib import Path

VERSION = 1
READ_MAX_LINES = 200
MAX_DEPTH = 3
MAX_UNITS = 96
MAX_LOCATORS = 32
PROTOCOLS = {'len': ('__len__',), 'iter': ('__iter__', '__getitem__'),
             'list': ('__iter__', '__getitem__', '__len__'),
             'tuple': ('__iter__', '__getitem__', '__len__'),
             'zip': ('__iter__', '__getitem__')}
IGNORED_NAMES = {'self', 'cls', 'True', 'False', 'None'}


def _id(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:20]


def _start(n):
    return min([n.lineno] + [x.lineno for x in getattr(n, 'decorator_list', [])])


def _name(n):
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        prefix = _name(n.value)
        return prefix + '.' + n.attr if prefix else n.attr
    return ''


def _attrs(n):
    return {x.attr for x in ast.walk(n) if isinstance(x, ast.Attribute)
            and isinstance(x.value, ast.Name) and x.value.id in ('self', 'cls')}


def _coverage_rows(rows):
    for r in rows or []:
        if r.get('admitted') is False or r.get('snapshot', 'head') != 'head':
            continue
        if r.get('content_kind', r.get('kind', 'source_lines')) != 'source_lines':
            continue
        path = r.get('path') or r.get('file')
        if not path:
            continue
        lines = r.get('lines')
        if lines is not None:
            numbers = [int(x[0] if isinstance(x, (list, tuple)) else x) for x in lines]
        else:
            runs = r.get('runs', [])
            if not runs and r.get('start') is not None and r.get('end') is not None:
                runs = [(r['start'], r['end'])]
            numbers = [n for a, b in runs for n in range(int(a), int(b) + 1)]
        if numbers:
            yield path, numbers


class ScopedIndex:
    def __init__(self, root, audit):
        self.root = Path(root).resolve()
        self.cache, self.audit = {}, audit

    def path(self, rel):
        p = (self.root / rel).resolve()
        if Path(rel).is_absolute() or not p.is_relative_to(self.root) or p.suffix != '.py':
            raise ValueError('outside workspace Python scope')
        return p

    def index(self, rel, text=None):
        if rel in self.cache:
            return self.cache[rel]
        p = self.path(rel)
        if text is None:
            event = {'path': rel, 'purpose': 'explicit_frontier_scope', 'read_succeeded': False}
            self.audit.append(event)
            try:
                text = p.read_text()
            except Exception as exc:
                event['error'] = type(exc).__name__
                raise
            event['read_succeeded'] = True
            event['source_sha256'] = hashlib.sha256(text.encode()).hexdigest()
        tree = ast.parse(text)
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        records = []
        for n in ast.walk(tree):
            if not isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            ancestors, q = [], parents.get(n)
            while q is not None:
                if isinstance(q, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    ancestors.append(q)
                q = parents.get(q)
            owner = next((q for q in ancestors if isinstance(q, ast.ClassDef)), None)
            records.append({'node': n, 'kind': 'class' if isinstance(n, ast.ClassDef) else 'function',
                            'name': n.name, 'qualname': '.'.join([q.name for q in reversed(ancestors)] + [n.name]),
                            'start': _start(n), 'end': n.end_lineno, 'owner_node': owner,
                            'owner': owner.name if owner else None, 'attrs': sorted(_attrs(n))})
        data = {'text': text, 'tree': tree, 'parents': parents, 'records': records,
                'sha256': hashlib.sha256(text.encode()).hexdigest()}
        self.cache[rel] = data
        return data

    def at(self, path, line, *, prefer_class_header=True):
        d = self.index(path)
        candidates = [r for r in d['records'] if r['start'] <= line <= r['end']]
        exact = [r for r in candidates if r['kind'] == 'class' and r['node'].lineno == line]
        if prefer_class_header and exact:
            return min(exact, key=lambda r: r['end'] - r['start'])
        return min(candidates, key=lambda r: (r['end'] - r['start'], -r['start'])) if candidates else None


class BodyFrontier:
    def __init__(self, root, serialized=None):
        self.data = copy.deepcopy(serialized) if serialized is not None else {
            'version': VERSION, 'units': [], 'locators': [], 'coverage': {}, 'pending': {},
            'attempted': [], 'events': [], 'source_reads': [], 'deleted_symbols': [],
            'selection_policy': 'changed AST only; scoped structural candidates, never inferred native bindings'}
        if self.data.get('version') != VERSION:
            raise ValueError('unsupported frontier version')
        self.index = ScopedIndex(root, self.data['source_reads'])

    @classmethod
    def from_packet(cls, serialized, root, supplied_spans=None):
        obj = cls(root, serialized)
        # Reservations from packet construction must not leak into review runtime.
        if obj.data['pending']:
            raise ValueError('packet has unfinished reservations')
        obj.supply(supplied_spans or [])
        return obj

    def to_dict(self):
        return copy.deepcopy(self.data)

    def supply(self, rows):
        for path, numbers in _coverage_rows(rows):
            old = set(self.data['coverage'].get(path, []))
            self.data['coverage'][path] = sorted(old | set(numbers))
        for u in self.data['units']:
            cov = set(self.data['coverage'].get(u['path'], []))
            complete = all(n in cov for n in range(u['start'], u['end'] + 1))
            if complete:
                u['state'] = 'body_supplied'
            elif any(n in cov for n in range(u['start'], u['end'] + 1)) and u['state'] != 'blocked':
                u['state'] = 'partial'

    def _unit(self, path, r, *, relation, priority, depth=0, parent=None,
              provenance='ast_fallback', backend='python-ast', locator_id=None):
        uid = _id([path, r['start'], r['end'], 'head'])
        old = next((u for u in self.data['units'] if u['id'] == uid), None)
        if old:
            old['priority'] = min(old['priority'], priority)
            if relation not in old['relations']:
                old['relations'].append(relation)
            return old
        if len(self.data['units']) >= MAX_UNITS or depth > MAX_DEPTH:
            return None
        u = {k: r[k] for k in ('kind', 'name', 'qualname', 'start', 'end', 'owner', 'attrs')}
        u.update(id=uid, path=path, snapshot='head', relations=[relation], priority=priority,
                 depth=depth, parent=parent, provenance=provenance, backend=backend,
                 locator_id=locator_id, state='unread', expanded=False,
                 source_sha256=self.index.index(path)['sha256'])
        self.data['units'].append(u)
        self.supply([])
        return u

    def _anchor(self, path, node, *, relation, priority, operation='go_to_definition', depth=0, parent=None):
        if depth > MAX_DEPTH or len(self.data['locators']) >= MAX_LOCATORS:
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name, line = node.name, node.lineno
            raw = self.index.index(path)['text'].splitlines()[line - 1]
            start = raw.find(name, node.col_offset)
        elif isinstance(node, ast.Attribute):
            name, line = node.attr, node.end_lineno
            raw = self.index.index(path)['text'].splitlines()[line - 1]
            # Python AST columns are UTF-8 byte offsets, LSP positions UTF-16.
            end = len(raw.encode()[:node.end_col_offset].decode())
            start = end - len(name)
        elif isinstance(node, ast.Name):
            name, line = node.id, node.lineno
            raw = self.index.index(path)['text'].splitlines()[line - 1]
            start = len(raw.encode()[:node.col_offset].decode())
        else:
            return
        if name in IGNORED_NAMES or not name or start < 0:
            return
        column = len(raw[:start].encode('utf-16-le')) // 2 + 1
        lid = _id([operation, path, line, column, name])
        if any(x['id'] == lid for x in self.data['locators']):
            return
        self.data['locators'].append({'id': lid, 'operation': operation, 'path': path,
            'line': line, 'column': column, 'name': name, 'kind': 'change_driven_use_site',
            'relation': relation, 'priority': priority, 'depth': depth, 'parent': parent,
            'state': 'unresolved'})

    def seed_file(self, path, head, base, changed_head, changed_base):
        d = self.index.index(path, head)
        btree = ast.parse(base)
        bparents = {c: n for n in ast.walk(btree) for c in ast.iter_child_nodes(n)}
        def base_qualname(n):
            names, p = [n.name], bparents.get(n)
            while p is not None:
                if isinstance(p, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.append(p.name)
                p = bparents.get(p)
            return '.'.join(reversed(names))
        old_nodes = [n for n in ast.walk(btree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and any(_start(n) <= line <= n.end_lineno for line in changed_base)]
        head_qualnames = {r['qualname'] for r in d['records']}
        removed = [n for n in old_nodes if base_qualname(n) not in head_qualnames]
        deleted = {n.name for n in removed}
        for node in removed:
            name = node.name
            self.data['deleted_symbols'].append({'path': path, 'name': name, 'snapshot': 'base',
                                                 'qualname': base_qualname(node),
                                                 'state': 'removed_identity_not_head_neighbor'})
        selected = [r for r in d['records'] if r['kind'] == 'function'
                    and (any(r['start'] <= line <= r['end'] for line in changed_head)
                         or r['qualname'] in {base_qualname(n) for n in old_nodes})]
        # A removal retains its owner identity, not the function beside the hunk.
        for n in removed:
            owner = bparents.get(n)
            if isinstance(owner, ast.ClassDef):
                owners = [r for r in d['records'] if r['kind'] == 'class' and r['qualname'] == base_qualname(owner)]
                if len(owners) == 1:
                    u = self._unit(path, owners[0], relation='deleted_override_owner', priority=1)
                    self.expand(u, changed_fields=_attrs(n), deleted_names=deleted)
        for r in selected:
            # Keep the smallest selected function for each changed line.
            if any(o is not r and r['start'] <= o['start'] and o['end'] <= r['end']
                   and (o['start'], o['end']) != (r['start'], r['end']) for o in selected):
                continue
            n = r['node']
            old = next((x for x in old_nodes if base_qualname(x) == r['qualname']), None)
            old_calls = {ast.dump(x, include_attributes=False) for x in ast.walk(old) if isinstance(x, ast.Call)} if old else set()
            old_attrs = _attrs(old) if old else set()
            fields = _attrs(n) | old_attrs
            u = self._unit(path, r, relation='changed_function', priority=20)
            for x in ast.walk(n):
                if isinstance(x, ast.Call):
                    affected = any(x.lineno <= line <= x.end_lineno for line in changed_head)
                    if affected and ast.dump(x, include_attributes=False) not in old_calls:
                        # Calls inside changed arguments outrank an unchanged outer wrapper.
                        nested_changed = any(y is not x and isinstance(y, ast.Call)
                                             and ast.dump(y, include_attributes=False) not in old_calls
                                             for y in ast.walk(x))
                        if _name(x.func).split('.')[0] not in PROTOCOLS:
                            self._anchor(path, x.func, relation='changed_call_argument', priority=4 if nested_changed else 1, parent=u['id'])
                elif isinstance(x, ast.Attribute) and isinstance(x.ctx, ast.Load) and any(x.lineno <= line <= x.end_lineno for line in changed_head):
                    if isinstance(x.value, ast.Name) and x.value.id in ('self', 'cls'):
                        self._anchor(path, x, relation='changed_field_read', priority=2, parent=u['id'])
            self.expand(u, changed_fields=fields)
            self._anchor(path, n, relation='changed_return_callers', priority=9,
                         operation='find_references', parent=u['id'])
        # Direct class-level value edits have no enclosing function.
        for line in changed_head:
            if any(r['start'] <= line <= r['end'] for r in selected):
                continue
            owners = [r for r in d['records'] if r['kind'] == 'class' and r['start'] <= line <= r['end']]
            if owners:
                r = min(owners, key=lambda x: x['end'] - x['start'])
                u = self._unit(path, r, relation='changed_class_value_owner', priority=1)
                self.expand(u)

    def expand(self, unit, *, changed_fields=(), deleted_names=()):
        if not unit or unit['depth'] >= MAX_DEPTH:
            return
        if unit['expanded'] and (unit['state'] != 'body_supplied' or unit.get('source_expanded')):
            return
        unit['expanded'] = True
        if unit['state'] == 'body_supplied':
            unit['source_expanded'] = True
        d = self.index.index(unit['path'])
        r = next((x for x in d['records'] if x['start'] == unit['start'] and x['end'] == unit['end']), None)
        if r is None:
            return
        n, depth = r['node'], unit['depth'] + 1
        owner = n if isinstance(n, ast.ClassDef) else r['owner_node']
        fields = set(changed_fields) | set(unit.get('focus_attrs', []))
        if not isinstance(n, ast.ClassDef):
            fields.update(_attrs(n))
        protocols = set()
        for x in ast.walk(n):
            if isinstance(x, ast.Call) and isinstance(x.func, ast.Name):
                protocols.update(PROTOCOLS.get(x.func.id, ()))
            elif isinstance(x, (ast.For, ast.ListComp, ast.GeneratorExp)):
                protocols.update(('__iter__', '__getitem__'))
            elif isinstance(x, ast.Subscript):
                protocols.add('__getitem__')
        if owner is not None:
            rec = next(x for x in d['records'] if x['node'] is owner)
            self._unit(unit['path'], rec, relation='enclosing_owner_context', priority=2, depth=depth, parent=unit['id'])
            for base in owner.bases:
                self._anchor(unit['path'], base, relation='owner_base_scope', priority=0 if fields or deleted_names or protocols else 4,
                             depth=depth, parent=unit['id'])
            for other in d['records']:
                if other['owner_node'] is owner and other['node'] is not n:
                    relation = None
                    if fields & set(other['attrs']):
                        relation = 'same_owner_field_consumer'
                    elif other['name'] in protocols:
                        relation = 'receiver_protocol_candidate'
                    elif protocols and other['name'] in ('__new__', '__init__'):
                        relation = 'receiver_constructor_candidate'
                    elif isinstance(n, ast.ClassDef) and other['name'] in ('__new__', '__init__'):
                        relation = 'class_constructor_candidate'
                    elif deleted_names and any(isinstance(x, ast.Call) and _name(x.func) in ('getattr', 'getattribute') for x in ast.walk(other['node'])):
                        relation = 'dynamic_member_dispatch_candidate'
                    if relation:
                        self._unit(unit['path'], other, relation=relation, priority=1, depth=depth, parent=unit['id'])
            # Direct same-file subclasses are structural candidates, not proven runtime receivers.
            for other in d['records']:
                if other['kind'] == 'class' and any(isinstance(x, ast.Name) and x.id == owner.name for x in other['node'].bases):
                    self._unit(unit['path'], other, relation='same_file_subclass_candidate', priority=4, depth=depth, parent=unit['id'])
        for call in [x for x in ast.walk(n) if isinstance(x, ast.Call)]:
            target = None
            if isinstance(call.func, ast.Name):
                bound = set()
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    bound.update(a.arg for a in [*n.args.posonlyargs, *n.args.args, *n.args.kwonlyargs])
                    for x in ast.walk(n):
                        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
                            bound.add(x.id)
                        elif isinstance(x, (ast.Import, ast.ImportFrom)):
                            bound.update(a.asname or a.name.split('.')[0] for a in x.names)
                matches = [x for x in d['records'] if x['name'] == call.func.id and x['qualname'] == x['name']
                           and call.func.id not in bound]
                # Module-only exact declaration; no global or same-name fallback.
                if len(matches) == 1:
                    target = matches[0]
            elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                receiver = call.func.value.id
                cls = owner if receiver in ('self', 'cls') else None
                if cls is None and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    arg = next((a for a in n.args.args if a.arg == receiver), None)
                    named = _name(arg.annotation) if arg and arg.annotation else ''
                    cs = [x['node'] for x in d['records'] if x['kind'] == 'class' and x['name'] == named]
                    cls = cs[0] if len(cs) == 1 else None
                matches = [x for x in d['records'] if cls is not None and x['owner_node'] is cls and x['name'] == call.func.attr]
                if len(matches) == 1:
                    target = matches[0]
            if target and target['node'] is not n:
                self._unit(unit['path'], target, relation='scoped_lexical_callee', priority=3, depth=depth, parent=unit['id'])
            # Once a supplied unit expands, exact use-sites can support a later native lookup.
            if unit['state'] == 'body_supplied' and isinstance(call.func, (ast.Name, ast.Attribute)) and not target:
                if _name(call.func).split('.')[0] not in PROTOCOLS:
                    self._anchor(unit['path'], call.func, relation='supplied_body_callee', priority=6, depth=depth, parent=unit['id'])
        if unit['state'] == 'body_supplied' and not isinstance(n, ast.ClassDef):
            self._anchor(unit['path'], n, relation='supplied_return_callers', priority=8,
                         operation='find_references', depth=depth, parent=unit['id'])

    def native_anchor(self):
        rows = [a for a in self.data['locators'] if a['state'] == 'unresolved']
        return min(rows, key=lambda x: (x['priority'], x['depth'], x['path'], x['line'], x['column'])) if rows else None

    def add_locations(self, locations, *, locator=None):
        locator = locator or {}
        for loc in sorted(locations or [], key=lambda x: (x.get('path', ''), x.get('line_start', x.get('line', 0)), x.get('column_start', 0))):
            source = loc.get('source', '')
            tags = loc.get('sources') or [source]
            native = any('lsp' in t.lower() and 'fallback' not in t.lower() for t in tags)
            if loc.get('ambiguity') or loc.get('ambiguous'):
                self.data['events'].append({'kind': 'ambiguous_location', 'location': loc})
                continue
            path, line = loc.get('path'), loc.get('line_start', loc.get('line'))
            try:
                if not path or not line:
                    raise ValueError('missing path/line')
                r = self.index.at(path, int(line))
            except (ValueError, OSError, SyntaxError) as exc:
                self.data['events'].append({'kind': 'target_unavailable', 'path': path, 'error': type(exc).__name__})
                continue
            if r is None:
                self.data['events'].append({'kind': 'no_complete_unit', 'path': path, 'line': line})
                continue
            u = self._unit(path, r, relation='native_target' if native else 'structural_locator_candidate',
                priority=0 if native else 5, depth=locator.get('depth', 0) + 1, parent=locator.get('parent'),
                provenance='native_lsp' if native else 'ast_fallback',
                backend=next((t for t in tags if 'lsp' in t.lower()), 'python-ast'), locator_id=locator.get('id'))
            if u:
                parent = next((p for p in self.data['units'] if p['id'] == locator.get('parent')), None)
                u['focus_attrs'] = sorted(set(u.get('focus_attrs', [])) | set((parent or {}).get('attrs', [])))
            # Full class context can be selected immediately, but nested dependencies
            # are generated only after its source is supplied or the unit is a root scope.
            if u and u['state'] == 'body_supplied':
                self.expand(u)

    def _missing_chunks(self, unit, max_lines):
        covered = set(self.data['coverage'].get(unit['path'], []))
        reserved = {(r['path'], n) for r in self.data['pending'].values() if r['operation'] == 'read_lines'
                    for n in range(r['start'], r['end'] + 1)}
        out, start, end = [], None, None
        for n in range(unit['start'], unit['end'] + 1):
            if n in covered or (unit['path'], n) in reserved:
                if start is not None:
                    out.append((start, end)); start = None
                continue
            if start is None:
                start = n
            end = n
            if end - start + 1 == max_lines:
                out.append((start, end)); start = None
        if start is not None:
            out.append((start, end))
        return out

    def next_request(self, max_lines=READ_MAX_LINES, remaining_queries=None, source_tokens_remaining=None):
        """Reserve one real atomic request; no filesystem or resolver access."""
        if max_lines < 1 or max_lines > READ_MAX_LINES:
            raise ValueError('read chunk limit must be between 1 and 200')
        if remaining_queries is not None and remaining_queries <= 0:
            return None
        for u in sorted(self.data['units'], key=lambda x: (x['state'] != 'partial', x.get('claim_priority', 1), x['priority'], x['depth'], x['path'], x['start'], x['end'])):
            if u['state'] in ('body_supplied', 'blocked'):
                continue
            chunks = self._missing_chunks(u, max_lines)
            if remaining_queries is not None and len(chunks) > remaining_queries:
                continue
            if source_tokens_remaining is not None and u.get('remaining_token_estimate', 0) > source_tokens_remaining:
                continue
            for start, end in chunks:
                key = ['read_lines', u['path'], start, end]
                if key in self.data['attempted']:
                    continue
                request = {'id': 'frontier-' + _id(key), 'operation': 'read_lines', 'path': u['path'],
                    'query': None, 'start': start, 'end': end,
                    'reason': f"Acquire complete {u['kind']} {u['qualname']}; unit {u['start']}-{u['end']}; relation {u['relations'][0]}; {u['provenance']}",
                    'origin': 'body-frontier', 'rule_id': u['id']}
                self.data['pending'][request['id']] = {**request, 'unit_id': u['id'], 'key': key}
                return request
        pending_ids = {r.get('locator_id') for r in self.data['pending'].values()}
        locators = [a for a in self.data['locators'] if a['state'] == 'unresolved' and a['id'] not in pending_ids]
        if remaining_queries is not None and remaining_queries < 2:
            return None  # Do not spend the last query on a position with no body slot.
        if not locators:
            return None
        a = min(locators, key=lambda x: (x.get('claim_priority', 1), x['priority'], x['depth'], x['path'], x['line'], x['column']))
        request = {'id': 'frontier-' + a['id'], 'operation': a['operation'], 'path': a['path'],
                   'query': a['name'], 'start': a['line'], 'end': a['line'],
                   'reason': f"Locate {a['relation']} at exact changed/source use-site; body must be read separately",
                   'origin': 'body-frontier', 'rule_id': a['id']}
        self.data['pending'][request['id']] = {**request, 'locator_id': a['id'], 'key': [a['operation'], a['id']]}
        return request

    def release(self, request_or_id, reason='not_dispatched'):
        rid = request_or_id if isinstance(request_or_id, str) else request_or_id['id']
        item = self.data['pending'].pop(rid, None)
        if item:
            self.data['events'].append({'kind': 'reservation_released', 'request_id': rid, 'reason': reason})

    def observe(self, request, supplied_spans=None, locator_locations=None, status='completed'):
        request = request if isinstance(request, dict) else vars(request)
        item = self.data['pending'].pop(request['id'], None)
        self.supply(supplied_spans or [])
        if item:
            self.data['attempted'].append(item['key'])
        a = next((x for x in self.data['locators'] if x['id'] == (item or {}).get('locator_id')), None)
        if a:
            a['state'] = 'located' if status == 'completed' and locator_locations else ('empty' if status == 'completed' else 'failed')
        if status == 'completed' and locator_locations:
            self.add_locations(locator_locations, locator=a or {'id': request['id']})
        if item and item.get('unit_id'):
            u = next(x for x in self.data['units'] if x['id'] == item['unit_id'])
            if status != 'completed' and u['state'] != 'body_supplied':
                u['state'] = 'blocked'
            elif u['state'] != 'body_supplied' and not self._missing_chunks(u, READ_MAX_LINES):
                u['state'] = 'partial'
        for u in list(self.data['units']):
            if u['state'] == 'body_supplied' and (not u['expanded'] or not u.get('source_expanded')):
                try:
                    self.expand(u)
                except (ValueError, OSError, SyntaxError) as exc:
                    self.data['events'].append({'kind': 'expansion_unavailable', 'unit_id': u['id'], 'error': type(exc).__name__})
        self.data['events'].append({'kind': 'observed_request', 'request_id': request['id'], 'status': status,
                                    'body_complete_count': sum(x['state'] == 'body_supplied' for x in self.data['units'])})

    def prioritize_claims(self, payload):
        """Reorder existing candidates only; never creates a symbol, path or binding."""
        tokens = set(re.findall(r'[A-Za-z_]\w*', json.dumps(payload, ensure_ascii=False)[:20000]))
        for collection in ('units', 'locators'):
            for item in self.data[collection]:
                names = set(item.get('attrs', [])) | {item['name']}
                item['claim_priority'] = 0 if (names - IGNORED_NAMES) & tokens else 1
