"""Bounded source-route selection; syntax supplies candidates, never a verdict.

The immutable GapSelector supplies the source/hash/coverage contract. This
policy changes which anchored caller, receiver and consumer bodies are read.
Native preselection is I/O-free and keeps original native dictionaries. A
separate bounded postselection checks lexical owners and actual call/catch
structure before any position reaches the fallback frontier.
"""
from __future__ import annotations

import ast
import copy
import math
import re
from pathlib import PurePosixPath
from gap_selector import GapSelector, _expr, _local_walk, _sha, _syntax_terms

MAX_NATIVE_LOCATIONS = 8
MAX_NEW_INDEX_FILES = 12
MAX_ROUTE_UNITS = 48
MAX_BODIES_PER_NATIVE = 2
MAX_READ_LINES = 200
PROTOCOLS = {'len': ('__len__',), 'iter': ('__iter__', '__getitem__'),
             'list': ('__iter__', '__getitem__', '__len__'),
             'tuple': ('__iter__', '__getitem__', '__len__')}


def _leaf(text):
    return str(text or '').split('(')[0].rsplit('.', 1)[-1]


def _position(position):
    return position.get('path'), position.get('start', position.get('line_start')), position.get('column', position.get('column_start', 1))


def _handler_names(node):
    if node is None:
        return {'*'}
    if isinstance(node, ast.Tuple):
        return {_leaf(_expr(n)) for n in node.elts}
    return {_leaf(_expr(node))}


class PathSelector(GapSelector):
    def __init__(self, critic_payload, supplied_sources, saved_frontier, root, manifest):
        self._initial_paths = {r.get('file', r.get('path')) for r in supplied_sources or []
                               if r.get('snapshot', 'head') == 'head'}
        self._new_index_paths = set()
        self._active_unit = None
        self._route_decisions = []
        self._route_edges = []
        self._native_filter_cache = {}
        self._call_cache = {}
        self._observing_request = None
        super().__init__(critic_payload, supplied_sources, saved_frontier, root, manifest)
        self.data['version'] = 2
        self.data['policy'].update(policy_name='bounded-source-causal-path',
            native_location_cap=MAX_NATIVE_LOCATIONS, new_index_file_cap=MAX_NEW_INDEX_FILES,
            route_unit_cap=MAX_ROUTE_UNITS, body_chunk_lines=MAX_READ_LINES,
            body_candidates_per_native=MAX_BODIES_PER_NATIVE,
            prioritization='receiver/import ownership; actual call/catch consumers; related same-class protocol/storage routes; executable source before masked documentation',
            depth_claim='bounded positions/files/queries, no runtime dispatch certification',
            executable_coverage_is_whole_body_coverage=False,
            no_function_or_case_specific_rules=True)

    def _index(self, path):
        if isinstance(path, str) and path not in self._initial_paths and path not in self._files:
            if path not in self._new_index_paths:
                if len(self._new_index_paths) >= MAX_NEW_INDEX_FILES:
                    self._route_decisions.append(dict(kind='index_file_budget_denied', path=path,
                                                     cap=MAX_NEW_INDEX_FILES, coverage_changed=False))
                    return None
                # Unique attempted new files count even if a hash or parse fails.
                self._new_index_paths.add(path)
        return super()._index(path)

    def _cached_record(self, path, line):
        d = self._files.get(path)
        if not d or type(line) is not int:
            return None
        records = [r for r in d['records'] if r['start'] <= line <= r['end']]
        classes = [r for r in records if r['kind'] == 'class' and r['node'].lineno == line]
        if classes:
            return min(classes, key=lambda r: r['end']-r['start'])
        return min(records, key=lambda r: (r['end']-r['start'], -r['start'])) if records else None

    def _binding(self,path,d,node,receiver):
        binding=super()._binding(path,d,node,receiver)
        scope=self._scope(d,node)
        if (binding['kind']=='method_receiver' and isinstance(scope,(ast.FunctionDef,ast.AsyncFunctionDef))
            and any(_leaf(_expr(deco))=='staticmethod' for deco in scope.decorator_list)):
            binding.update(kind='parameter',owner=None,static_parameter_not_implicit_receiver=True)
        return binding

    def _node_at(self, path, line, name):
        d = self._files.get(path)
        if not d or type(line) is not int:
            return None
        if path not in self._call_cache:
            groups={}
            for n in ast.walk(d['tree']):
                if isinstance(n,ast.Call):groups.setdefault(_leaf(_expr(n.func)),[]).append(n)
            self._call_cache[path]=groups
        nodes = [n for n in self._call_cache[path].get(_leaf(name),[])
                 if n.lineno <= line <= n.end_lineno]
        return min(nodes, key=lambda n: (n.end_lineno-n.lineno, n.col_offset)) if nodes else None

    def _source_exception_names(self, request):
        rec = self._cached_record(request.get('path'), request.get('line', request.get('start')))
        if not rec:
            return set()
        return {_leaf(_expr(n.exc.func if isinstance(n.exc, ast.Call) else n.exc))
                for n in _local_walk(rec['node']) if isinstance(n, ast.Raise) and n.exc is not None
                and (request.get('path'),n.lineno) in self._sources}

    def _request_anchor(self,request):
        return next((a for a in self.data['anchors'] if a['path']==request.get('path')
                     and a['line']==request.get('line',request.get('start'))
                     and a['name']==_leaf(request.get('name',request.get('query')))
                     and (not request.get('column') or a['column']==request['column'])),{})

    def _needs_for(self, request):
        ids = set(request.get('need_ids', []))
        if request.get('locator_id'):
            loc = next((l for l in self.data['locators'] if l['id'] == request['locator_id']), None)
            ids |= {e['need_id'] for e in (loc or {}).get('edges', [])}
        return [n for n in self.data['needs'] if n['id'] in ids] if ids else self.data['needs']

    def _import_modules(self, request):
        d = self._files.get(request.get('path'))
        if not d:
            return set()
        name = request.get('receiver_expression') or self._request_anchor(request).get('receiver') or request.get('name') or request.get('query') or ''
        root_name = str(name).split('.')[0]
        modules = set()
        for node in ast.walk(d['tree']):
            if not isinstance(node, (ast.Import, ast.ImportFrom)) or (request['path'], node.lineno) not in self._sources:
                continue
            for alias in node.names:
                local = alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name)
                if local != root_name:
                    continue
                if isinstance(node,ast.Import):module=alias.name
                else:
                    module=node.module or ''
                    if node.level:
                        package=list(PurePosixPath(request['path']).parent.parts)
                        keep=package[:max(0,len(package)-node.level+1)]
                        module='.'.join([*keep,*([module] if module else [])])
                modules.add(module)
        return modules

    def _expected_owners(self, request):
        binding = request.get('receiver_binding') or self._request_anchor(request).get('receiver_binding') or {}
        owners = {_leaf(binding.get('owner')), _leaf(binding.get('annotation'))} - {''}
        d = self._files.get(request.get('path'))
        rec=self._cached_record(request.get('path'),request.get('line',request.get('start')))
        if (rec and rec.get('owner') and rec['kind']=='function' and rec['name']==_leaf(request.get('name',request.get('query')))
            and rec['node'].lineno==request.get('line',request.get('start'))):owners.add(rec['owner'])
        if d:
            changed=True
            while changed:
                before=set(owners)
                for record in d['records']:
                    if record['kind'] == 'class' and record['name'] in owners:
                        owners |= {_leaf(_expr(b)) for b in record['node'].bases}
                changed=owners!=before
        return owners

    def _owner_chain_unresolved(self,path,owners):
        d=self._files.get(path)
        if not owners:return False
        if not d:return True
        classes={r['name']:r for r in d['records'] if r['kind']=='class'}
        for owner in owners:
            record=classes.get(owner)
            if record is None:return True
            if any(_leaf(_expr(base)) not in classes for base in record['node'].bases):return True
        return False

    def _route_score(self, request, position, *, require_scope=False):
        """Pure cached-AST ranking. No filesystem/native/coverage mutations."""
        path, line, column = _position(position)
        name = request.get('name') or request.get('query') or ''
        op = request.get('operation')
        source_path = request.get('path')
        rec = self._cached_record(path, line)
        modules = self._import_modules(request)
        module_match = any(str(path or '').removesuffix('.py').replace('/', '.').endswith(m)
                           or str(path or '').removesuffix('/__init__.py').replace('/', '.').endswith(m)
                           for m in modules if m)
        source_package = str(source_path or '').split('/')[0]
        path_rank = 0 if path == source_path else 1 if module_match else 2 if source_package and str(path or '').startswith(source_package+'/') else 3
        reasons = ['same_source_path' if path_rank == 0 else 'visible_import_module' if path_rank == 1 else 'same_package' if path_rank == 2 else 'native_discovered_scope']
        if op == 'symbol_search':
            target = _leaf(name)
            if _leaf(position.get('name')) != target:
                return None, ['native_symbol_name_mismatch']
        owners = self._expected_owners(request)
        unresolved_owner=False
        if op in ('go_to_definition', 'symbol_search') and owners and rec and rec.get('owner') and rec['owner'] not in owners:
            if not self._owner_chain_unresolved(source_path,owners):
                return None, ['different_receiver_owner', rec['owner'], sorted(owners)]
            unresolved_owner=True
            reasons.append('receiver_inheritance_unresolved_native_candidate')
        owner_rank = 0 if owners and rec and rec.get('owner') in owners else 1
        if unresolved_owner:owner_rank=2
        if owner_rank == 0:
            reasons.append('lexical_receiver_or_declared_base_owner')
        need_relations = {n['relation'] for n in self._needs_for(request)}
        if rec is None:
            return ((3 if 'exception_route' in need_relations else path_rank, 4, owner_rank, 2, path_rank, 10**9,
                     str(path or ''), line or 0, column or 0), reasons+['scope_not_indexed']) if not require_scope else (None, ['no_enclosing_callable_scope'])
        node = self._node_at(path, line, name)
        handler_rank = 2
        call_rank = 0 if node else 2
        if node is not None:
            reasons.append('actual_call_target')
            d = self._files[path]
            if owners and op in ('find_references','incoming_calls') and isinstance(node.func,ast.Attribute):
                actual=self._binding(path,d,node.func,_expr(node.func.value))
                actual_owner=_leaf(actual.get('owner') or actual.get('annotation'))
                possible={actual_owner} if actual_owner else set()
                for record in d['records']:
                    if record['kind']=='class' and record['name']==actual_owner:
                        possible|={cls.name for cls in self._owner_chain(path,record['node'])}
                if possible and not possible&owners:
                    if not self._owner_chain_unresolved(path,possible) and not self._owner_chain_unresolved(source_path,owners):
                        return None,['reference_uses_different_declared_receiver',actual_owner,sorted(owners)]
                    reasons.append('reference_receiver_inheritance_unresolved')
            parent = d['parents'].get(node)
            while parent is not None:
                if isinstance(parent, (ast.Try, getattr(ast, 'TryStar', ast.Try))):
                    names = set().union(*(_handler_names(h.type) for h in parent.handlers)) if parent.handlers else set()
                    if names:
                        raises = self._source_exception_names(request)
                        handler_rank = min(handler_rank, 0 if names & raises or '*' in names else 1)
                        reasons.append('caller_catches_source_exception' if handler_rank == 0 else 'caller_has_exception_handler')
                parent = d['parents'].get(parent)
        if 'exception_route' not in need_relations:
            handler_rank = 1
        # A method that invokes the lower-level operation with the same API
        # name is a structurally plausible public consumer, not runtime proof.
        interface_rank = 0 if rec.get('owner') and rec['name'] == _leaf(name) and node else 1
        if interface_rank == 0:
            reasons.append('same_name_method_consumer')
        if op in ('find_references', 'incoming_calls', 'outgoing_calls') and node is None:
            if rec['node'].lineno == line and rec['name'] == _leaf(name):
                return None, ['reference_is_target_declaration_not_consumer']
            call_rank = 3
        return (handler_rank if 'exception_route' in need_relations else path_rank,
                call_rank, owner_rank, interface_rank, path_rank, rec['end']-rec['start'],
                str(path or ''), line or 0, column or 0), reasons

    def select_native_locations(self, request, raw_locations):
        """Executor callback: original native dictionaries, at most eight, I/O-free."""
        ranked, rejected = [], []
        for position in raw_locations or []:
            score, reasons = self._route_score(request, position)
            if score is None:
                rejected.append(dict(location=copy.deepcopy(position), reasons=reasons))
            else:
                ranked.append((score, position, reasons))
        ranked.sort(key=lambda row: row[0])
        selected=[];seen=set()
        for _, position, _ in ranked:
            sig=_sha(position)
            if sig not in seen and len(selected)<MAX_NATIVE_LOCATIONS:
                selected.append(position);seen.add(sig)
        self._route_decisions.append(dict(kind='native_preselection',request_id=request.get('id'),
            operation=request.get('operation'),returned=len(raw_locations or []),selected=len(selected),
            cap=MAX_NATIVE_LOCATIONS,excluded=len(raw_locations or [])-len(selected),
            ranked=[dict(location=copy.deepcopy(p),reasons=why,score=list(score)) for score,p,why in ranked[:MAX_NATIVE_LOCATIONS]],
            rejected=rejected[:MAX_NATIVE_LOCATIONS],excluded_hash=_sha([p for p in raw_locations or [] if p not in selected]),
            filesystem_reads=0,positions_are_source=False))
        return selected

    def filter_native_locations(self, request, verified_locations):
        """Bounded post-index selection before both selector and frontier planning."""
        key=_sha([request,verified_locations])
        if key in self._native_filter_cache:
            return copy.deepcopy(self._native_filter_cache[key])
        ranked=[];rejected=[]
        for position in verified_locations or []:
            path,line,_=_position(position)
            d=self._index(path) if isinstance(path,str) else None
            score,reasons=self._route_score(request,position,require_scope=True) if d else (None,['index_unavailable_or_budget_denied'])
            if score is None:
                rejected.append(dict(location=copy.deepcopy(position),reasons=reasons))
            else:
                ranked.append((score,position,reasons))
        ranked.sort(key=lambda row:row[0])
        if any(n['relation']=='exception_route' for n in self._needs_for(request)) and ranked and ranked[0][0][0]<2:
            best=ranked[0][0][0]
            lower=[row for row in ranked if row[0][0]>best]
            rejected.extend(dict(location=copy.deepcopy(row[1]),reasons=['lower_priority_than_actual_exception_consumer']) for row in lower)
            ranked=[row for row in ranked if row[0][0]==best]
        selected=[p for _,p,_ in ranked[:MAX_BODIES_PER_NATIVE]]
        self._route_decisions.append(dict(kind='native_postselection',request_id=request.get('id'),
            returned=len(verified_locations or []),selected=len(selected),
            new_index_files=len(self._new_index_paths),rejected=rejected,
            body_candidate_cap=MAX_BODIES_PER_NATIVE,
            ranked=[dict(location=copy.deepcopy(p),score=list(score),reasons=why) for score,p,why in ranked[:MAX_NATIVE_LOCATIONS]],
            binding_certainty='candidate_only',coverage_changed=False))
        self._native_filter_cache[key]=copy.deepcopy(selected)
        return selected

    def _explicit_code_symbols(self, need):
        allowed=set()
        # Invalid code fragments such as paths/language tags cannot become
        # global uppercase symbol searches merely because they contain letters.
        for match in re.finditer(r'`([^`]+)`',need['text']):
            parsed=_syntax_terms(match.group(1))
            if parsed:
                allowed.update(parsed[0])
        if need['pointer'].startswith('/context_requests/'):
            for line in need['text'].splitlines()[:3]:
                parsed=_syntax_terms(line)
                if parsed:allowed.update(parsed[0])
                declaration=re.match(r'\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)',line)
                if declaration:allowed.add(declaration.group(1))
        return allowed

    def _bind_needs(self):
        visible={a['name'] for a in self.data['anchors']}|{a['expression'] for a in self.data['anchors']}
        for need in self.data['needs']:
            masked=list(need['text']);invalid=set()
            for match in re.finditer(r'`([^`]+)`',need['text']):
                parsed=_syntax_terms(match.group(1))
                if parsed is None or not parsed[0]:
                    invalid.update(re.findall(r'[A-Za-z_]\w*',match.group(1)))
                    masked[match.start():match.end()]=' '*(match.end()-match.start())
            outside=set(re.findall(r'[A-Za-z_]\w*',''.join(masked)))
            removed=invalid-outside-self._explicit_code_symbols(need)
            need['words']=[s for s in need['words'] if s not in removed]
            need['explicit_symbols']=[s for s in need['explicit_symbols'] if s not in removed]
            allowed=self._explicit_code_symbols(need)|visible
            old=list(need['explicit_symbols'])
            need['explicit_symbols']=[s for s in old if s in allowed]
            suppressed=set(old)-set(need['explicit_symbols'])
            if suppressed:need['non_code_unbound_symbols_suppressed']=sorted(suppressed)
        super()._bind_needs()
        # Exception names in actual raise/except syntax locate callers of the
        # containing operation, not unrelated global raisers of that exception.
        exception_names=set()
        for path,d in self._files.items():
            for node in ast.walk(d['tree']):
                if isinstance(node,ast.Raise) and node.exc is not None and (path,node.lineno) in self._sources:
                    exception_names.add(_leaf(_expr(node.exc.func if isinstance(node.exc,ast.Call) else node.exc)))
                elif isinstance(node,ast.ExceptHandler) and (path,node.lineno) in self._sources:
                    exception_names|=_handler_names(node.type)-{'*'}
        for loc in self.data['locators']:
            if loc['operation']=='symbol_search' and _leaf(loc['name']) in exception_names:
                loc['state']='suppressed_exception_name_fanout'
            elif loc['operation'] in ('find_references','incoming_calls') and _leaf(loc['name']) in exception_names:
                loc['state']='deferred_exception_carrier_header'
        self._add_structural_routes()

    def _add_unit(self,path,rec,need,*,relation,origin,symbol_rank=0):
        origin=copy.deepcopy(origin)
        origin.setdefault('binding_certainty','candidate_only')
        if len(self.data['units'])>=MAX_ROUTE_UNITS and not any(u['path']==path and u['start']==rec['start'] and u['end']==rec['end'] for u in self.data['units']):
            self._route_decisions.append(dict(kind='route_unit_cap',cap=MAX_ROUTE_UNITS,path=path))
            return
        super()._add_unit(path,rec,need,relation=relation,origin=origin,symbol_rank=symbol_rank)
        unit=next((u for u in self.data['units'] if u['path']==path and u['start']==rec['start'] and u['end']==rec['end']),None)
        if unit:
            native=origin.get('native_location')
            if native and self._observing_request:
                score,_=self._route_score(self._observing_request,native,require_scope=True)
                if score is not None:
                    unit['native_route_score']=list(min(tuple(unit.get('native_route_score',score)),score))
            unit['path_priority']=min(unit.get('path_priority',2),origin.get('path_priority',1 if native else 2))
            doclines=set()
            for node in ast.walk(rec['node']):
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and node.body:
                    first=node.body[0]
                    if isinstance(first,ast.Expr) and isinstance(first.value,ast.Constant) and isinstance(first.value.value,str):
                        doclines.update(range(first.lineno,first.end_lineno+1))
            unit['executable_source_lines']=[n for n in range(rec['start'],rec['end']+1)
                if n not in doclines and self._files[path]['masked_lines'][n-1].strip()]

    def _owner_chain(self,path,owner):
        d=self._files[path];result=[];queue=[owner];seen=set()
        while queue:
            node=queue.pop(0)
            if node in seen:continue
            seen.add(node);result.append(node)
            for base in node.bases:
                name=_expr(base)
                matches=[r['node'] for r in d['records'] if r['kind']=='class' and r['name']==name and d['parents'].get(r['node']) is d['tree']]
                if len(matches)==1:queue.extend(matches)
        return result

    def _add_structural_routes(self):
        # These routes use only owners of actually supplied functions and
        # same-file lexical inheritance, never a repository-wide name match.
        for need in self.data['needs']:
            protocols={method for token,methods in PROTOCOLS.items() if token in need['words'] or token in need['explicit_symbols'] for method in methods}
            owners=[];broad=[]
            for anchor in self.data['anchors']:
                if need.get('path_hint') and anchor['path']!=need['path_hint']:continue
                if need['evidence_ids'] and not set(need['evidence_ids'])&set(anchor['evidence_ids']):continue
                rec=self._cached_record(anchor['path'],anchor['line'])
                if (rec and rec['kind']=='function' and rec['owner_node'] is not None
                    and self._files[anchor['path']]['parents'].get(rec['node']) is rec['owner_node']
                    and not any(_leaf(_expr(deco))=='staticmethod' for deco in rec['node'].decorator_list)):
                    if anchor['kind']=='attribute' and (anchor['receiver_binding']['kind']!='method_receiver'
                        or anchor['receiver_binding']['ambiguous_rebinding']):continue
                    row=(anchor['path'],rec['owner_node'],anchor['id']);broad.append(row)
                    words=set(need['words'])|set(need['explicit_symbols'])
                    if (anchor['id'] in need.get('anchor_ids',[]) or anchor['name'] in words or anchor['expression'] in words
                        or rec['name'] in words or rec['owner'] in words or need['evidence_ids']):owners.append(row)
            if not owners:
                unique={(p,id(o)) for p,o,_ in broad}
                if len(unique)==1:owners=broad
                elif unique:self._route_decisions.append(dict(kind='ambiguous_protocol_owner',need_id=need['id'],owner_count=len(unique)))
            for path,owner,anchor_id in owners:
                for cls in self._owner_chain(path,owner):
                    for rec in self._files[path]['records']:
                        if rec['owner_node'] is cls and rec['name'] in protocols and self._files[path]['parents'].get(rec['node']) is cls:
                            self._add_unit(path,rec,need,relation='same_owner_protocol_consumer',symbol_rank=0,
                                origin=dict(selection='source_owner_then_same_file_declared_base_protocol',anchor_id=anchor_id,
                                            owner=owner.name,declared_owner=cls.name,path_priority=0,binding_certainty='candidate_only'))
            self._storage_producer_routes(need)

    def _storage_producer_routes(self,need):
        """Visible same-owner field use -> visible assignment -> actual callsite.

        A local object is associated with that lexical owner only when its
        visible construction forwards the method's class receiver to __new__.
        This is a structural candidate, never a runtime alias certificate.
        """
        words=set(need['words'])|set(need['explicit_symbols'])
        for path,d in self._files.items():
            fields=[]
            for anchor in self.data['anchors']:
                if anchor['path']!=path:continue
                if need.get('path_hint') and path!=need['path_hint']:continue
                if need['evidence_ids'] and not set(need['evidence_ids'])&set(anchor['evidence_ids']):continue
                rec=self._cached_record(path,anchor['line'])
                if not rec or rec['owner_node'] is None:continue
                if (rec['kind']=='function' and any(_leaf(_expr(deco))=='staticmethod' for deco in rec['node'].decorator_list)):continue
                if anchor['name'] not in words and anchor['expression'] not in words:continue
                if (anchor['kind']=='attribute' and anchor['receiver_binding']['kind']=='method_receiver'
                    and not anchor['receiver_binding']['ambiguous_rebinding']):fields.append((rec['owner_node'],anchor['name'],anchor['id']))
                elif anchor['kind']=='declaration' and rec['kind']=='function':
                    args=[*rec['node'].args.posonlyargs,*rec['node'].args.args]
                    receiver=args[0].arg if args else None
                    for node in _local_walk(rec['node']):
                        if (isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id==receiver
                            and (path,node.lineno) in self._sources
                            and not self._binding(path,d,node,receiver)['ambiguous_rebinding']):
                            fields.append((rec['owner_node'],node.attr,anchor['id']))
            for owner,field,anchor_id in fields:
                for node in ast.walk(owner):
                    if not isinstance(node,(ast.Assign,ast.AnnAssign)) or (path,node.lineno) not in self._sources:continue
                    value=node.value
                    if not isinstance(value,ast.Call):continue
                    targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                    producer_rec=self._cached_record(path,node.lineno)
                    if (not producer_rec or producer_rec['owner_node'] is not owner
                        or d['parents'].get(producer_rec['node']) is not owner):continue
                    args=[*producer_rec['node'].args.posonlyargs,*producer_rec['node'].args.args]
                    receiver=args[0].arg if args else None
                    for target in targets:
                        if not (isinstance(target,ast.Attribute) and target.attr==field and isinstance(target.value,ast.Name)):continue
                        static=any(_leaf(_expr(deco))=='staticmethod' for deco in producer_rec['node'].decorator_list)
                        object_name=target.value.id;owner_association='method_receiver' if object_name==receiver and not static else None
                        if owner_association is None:
                            for earlier in _local_walk(producer_rec['node']):
                                if not isinstance(earlier,ast.Assign) or earlier.lineno>=node.lineno or (path,earlier.lineno) not in self._sources:continue
                                call=earlier.value
                                if (any(isinstance(t,ast.Name) and t.id==object_name for t in earlier.targets)
                                    and isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and call.func.attr=='__new__'
                                    and call.args and isinstance(call.args[0],ast.Name) and call.args[0].id==receiver):
                                    owner_association='visible_constructor_forwards_class_receiver'
                        if owner_association is None:continue
                        name=_leaf(_expr(value.func))
                        source_anchor=next((a for a in self.data['anchors'] if a['path']==path and a['name']==name
                                            and a['line']==value.func.end_lineno and a['syntactic_role']=='call_target'),None)
                        if source_anchor is None:continue
                        route_need=dict(need,requested_operation='go_to_definition',relation='callee_body')
                        self._locator(route_need,source_anchor,symbol_rank=0)
                        edge=dict(need_id=need['id'],source_anchor_id=anchor_id,producer_anchor_id=source_anchor['id'],
                                  path=path,field=field,lexical_owner=owner.name,
                                  owner_association=owner_association,binding_certainty='candidate_only')
                        if edge not in self._route_edges:self._route_edges.append(edge)

    def next_request(self,remaining_queries,remaining_source_tokens):
        if self.data['pending'] or remaining_queries<=0 or remaining_source_tokens<=0:
            return None
        units=sorted(self.data['units'],key=lambda u:(u.get('path_priority',2),u['id']!=self._active_unit,tuple(u.get('native_route_score',[99])),
            u['priority'],u['symbol_rank'],u['kind']=='class',u['end']-u['start'],u['path'],u['start']))
        for unit in units:
            useful=[n for n in unit.get('executable_source_lines',[]) if (unit['path'],n) not in self._sources
                    and ('read_lines',unit['path'],n) not in self._attempted]
            if not useful:continue
            start=useful[0];last=min(start+MAX_READ_LINES-1,unit['end'])
            possible=[n for n in useful if n<=last];end=possible[-1]
            lines=self._files[unit['path']]['masked_lines'];size=0;fit_end=None
            for n in range(start,end+1):
                cost=max(1,math.ceil((len(lines[n-1])+len(str(n))+3)/4))
                if size+cost>remaining_source_tokens:break
                size+=cost;fit_end=n
            if fit_end is None:continue
            # Never use a slot solely for masked documentation or blank lines.
            useful_fit=[n for n in useful if start<=n<=fit_end]
            if not useful_fit:continue
            fit_end=useful_fit[-1]
            req=dict(operation='read_lines',path=unit['path'],start=start,end=fit_end,snapshot='head',
                unit_id=unit['id'],origin='causal-path-selector',source_token_estimate=size,
                reason='Read remaining executable source on an anchored caller/receiver/consumer route; whole-body coverage remains separate',
                need_ids=sorted({e['need_id'] for e in unit['edges']}))
            self._active_unit=unit['id'];return self._reserve(req)
        if remaining_queries<2:
            self.data['decisions'].append(dict(kind='locator_budget_denied',remaining_queries=remaining_queries,reason='reserve_one_body_query'))
            return None
        locators=sorted(self.data['locators'],key=lambda l:(l['operation']=='symbol_search',l['priority'],l['symbol_rank'],
            l.get('path',''),l.get('line',0),l.get('column',0),l['name'],l['id']))
        for loc in locators:
            if loc['state']!='unresolved':continue
            req={k:copy.deepcopy(v) for k,v in loc.items() if k not in ('state','id','priority','edges')}
            req.update(locator_id=loc['id'],origin='causal-path-selector',reserved_body_queries=1,
                need_ids=sorted({e['need_id'] for e in loc['edges']}),reason='Locate a source-anchored causal route; native positions do not prove body delivery')
            loc['state']='pending';return self._reserve(req)
        return None

    def observe(self,request,result,admitted_spans):
        if self.data['pending'].get(request.get('id'))!=request:
            raise ValueError('unknown_or_modified_selector_request')
        planning=copy.deepcopy(result)
        if request['operation']!='read_lines' and result.get('locations'):
            planning['locations']=self.filter_native_locations(request,result['locations'])
        self._observing_request=request
        try:super().observe(request,planning,admitted_spans)
        finally:self._observing_request=None
        return planning

    def should_skip_frontier_read(self,request):
        """I/O-free fallback guard; skipping never grants whole-body coverage."""
        if request.get('operation')!='read_lines':return False
        path=request.get('path');d=self._files.get(path)
        start,end=request.get('start'),request.get('end')
        if not d or type(start) is not int or type(end) is not int or start<1 or end>len(d['masked_lines']):return False
        docs=set()
        for node in ast.walk(d['tree']):
            if isinstance(node,(ast.Module,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and node.body:
                first=node.body[0]
                if isinstance(first,ast.Expr) and isinstance(first.value,ast.Constant) and isinstance(first.value.value,str):
                    docs.update(range(first.lineno,first.end_lineno+1))
        missing=[n for n in range(start,end+1) if (path,n) not in self._sources]
        skip=bool(missing) and not any(n not in docs and d['masked_lines'][n-1].strip() for n in missing)
        if skip:self._route_decisions.append(dict(kind='documentation_only_fallback_read_skipped',path=path,start=start,end=end,
                                                 whole_body_coverage_changed=False,logical_execution_charged=False))
        return skip

    def summary(self):
        out=super().summary()
        out['route_decisions']=copy.deepcopy(self._route_decisions)
        out['storage_route_edges']=copy.deepcopy(self._route_edges)
        out['new_index_paths']=sorted(self._new_index_paths)
        for unit in out['units']:
            useful=set(unit.get('executable_source_lines',[]))
            initial=set(out['initial_delivered'].get(unit['path'],[]))
            admitted=set(out['newly_admitted'].get(unit['path'],[]))
            unit['executable_source_complete_initially']=useful<=initial
            unit['executable_source_complete_acquired']=useful<=initial|admitted
            unit['executable_source_missing_lines']=sorted(useful-initial-admitted)
            unit['executable_source_coverage_is_whole_body_coverage']=False
        out['counters']['new_index_file_attempts']=len(self._new_index_paths)
        out['counters']['planner_observed_native_requests']=out['counters'].pop('native_locator_queries')
        out['counters']['planner_observed_body_requests']=out['counters'].pop('body_read_queries')
        out['counter_semantics']='Planner observations include scheduler skips; authoritative execution costs are executor events, never these planner counters.'
        return out
