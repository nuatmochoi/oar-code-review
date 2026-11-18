"""Bounded consumer-first ordering over the existing source frontier.

No case IDs, contracts, labels, gold paths, global name scan, or history inputs.
AST relations propose bodies, not verified runtime/native bindings. Scoped local
index reads are retained in the inherited source_reads audit. Query planning is
I/O-free and inherited actual-delivery coverage remains authoritative.
"""
from __future__ import annotations

import ast
import re

from body_frontier import BodyFrontier, READ_MAX_LINES, MAX_DEPTH, _id, _name, PROTOCOLS

MAX_INDEX_PATHS_PER_PASS = 4
MAX_NEW_FUNCTIONS_PER_PASS = 24


def _identifiers(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _feature(record):
    node = record['node']
    reads = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load)}
    writes = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)}
    guards = set()
    returns, calls, protocols = set(), set(), set()
    for n in ast.walk(node):
        if isinstance(n, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            guards |= _identifiers(n.test)
        if isinstance(n, ast.Return) and n.value is not None:
            returns |= _identifiers(n.value)
        if isinstance(n, ast.Call):
            name = _name(n.func)
            if name:
                calls.add(name)
            if isinstance(n.func, ast.Name):
                protocols.update(PROTOCOLS.get(n.func.id, ()))
        if isinstance(n, (ast.For, ast.ListComp, ast.GeneratorExp)):
            protocols.update(('__iter__', '__getitem__'))
        if isinstance(n, ast.Subscript):
            protocols.add('__getitem__')
    return {'reads': sorted(reads), 'writes': sorted(writes), 'guards': sorted(guards),
            'returns': sorted(returns), 'calls': sorted(calls), 'protocols': sorted(protocols),
            'executable_nodes': sum(isinstance(n, ast.stmt) and not isinstance(n, (ast.FunctionDef, ast.ClassDef))
                                    for n in ast.walk(node))}


def _claim_tokens(payload):
    # Claims may rank existing source-derived candidates, never introduce paths.
    rows = list(payload.get('findings', []))
    rows += payload.get('review', {}).get('observations', [])
    text = ' '.join(str(row.get(k, '')) for row in rows if isinstance(row, dict)
                    for k in ('title', 'trigger', 'mechanism', 'impact', 'body'))[:16000]
    return set(re.findall(r'[A-Za-z_]\w*', text)) - {'self', 'cls', 'True', 'False', 'None'}


def rank(frontier, payload):
    """Pure deterministic ranks for known units/locators, with no I/O."""
    tokens = _claim_tokens(payload)
    data = frontier.data if hasattr(frontier, 'data') else frontier
    units, locators = [], []
    for u in data.get('units', []):
        f = u.get('consumer_features', {})
        relations = set(u.get('relations', []))
        focus = set(u.get('consumer_focus', []))
        read_overlap = focus & set(f.get('reads', []))
        guard_overlap = focus & set(f.get('guards', []))
        mentions = tokens & (set(f.get('reads', [])) | set(f.get('guards', [])) |
                             set(f.get('calls', [])) | {u['name']})
        if u['kind'] == 'class':
            bucket = 8  # Never let partial class gaps eclipse a relevant function.
        elif guard_overlap or 'consumer_guard' in relations:
            bucket = 0
        elif read_overlap or 'consumer_field_read' in relations:
            bucket = 1
        elif relations & {'consumer_protocol', 'receiver_protocol_candidate'}:
            bucket = 2
        elif relations & {'consumer_factory_constructor', 'consumer_direct_callee',
                           'scoped_lexical_callee', 'native_target'}:
            bucket = 3
        elif relations & {'same_owner_field_consumer', 'dynamic_member_dispatch_candidate'}:
            bucket = 4
        elif relations & {'receiver_constructor_candidate', 'class_constructor_candidate'}:
            bucket = 5
        else:
            bucket = 6
        order = (bucket, 0 if mentions else 1, 0 if u['state'] == 'partial' else 1,
                 u.get('depth', 0), u['end']-u['start'], u['path'], u['start'], u['end'], u['id'])
        units.append({'id':u['id'], 'order':list(order), 'bucket':bucket,
                      'relation':sorted(relations), 'focus_reads':sorted(read_overlap),
                      'focus_guards':sorted(guard_overlap), 'claim_source_overlap':sorted(mentions)})
    for l in data.get('locators', []):
        relation = l.get('relation', '')
        # Exact-site callee/base lookups can uncover a missing consumer body.
        bucket = 0 if relation in ('consumer_external_callee', 'consumer_inherited_protocol') else (
                 1 if relation in ('supplied_body_callee', 'changed_call_argument', 'changed_field_read') else (
                 2 if relation == 'owner_base_scope' else 3))
        order=(bucket,0 if l['name'] in tokens else 1,l.get('depth',0),l['path'],l['line'],l['column'],l['id'])
        locators.append({'id':l['id'],'order':list(order),'bucket':bucket,'relation':relation})
    units.sort(key=lambda r:r['order']); locators.sort(key=lambda r:r['order'])
    return {'version':1,'units':units,'locators':locators,'selection_only':True,
            'native_binding_inferred':False,'new_paths_from_claims':False}


class ConsumerFrontier(BodyFrontier):
    """Factory-compatible frontier retaining reservations and exact coverage."""
    def __init__(self, root, serialized=None):
        super().__init__(root, serialized)
        self.data.setdefault('consumer_audit', [])
        self._payload = {}

    def _enrich(self):
        # The input path set is solely source-derived frontier scope. No payload
        # path/name is used to create a file scope or a native binding.
        known = list(self.data['units'])
        paths = sorted({u['path'] for u in known})[:MAX_INDEX_PATHS_PER_PASS]
        added, indexed = 0, []
        for path in paths:
            try:
                d = self.index.index(path)
            except (ValueError, OSError, SyntaxError) as exc:
                self.data['consumer_audit'].append({'kind':'index_unavailable','path':path,'error':type(exc).__name__})
                continue
            indexed.append(path)
            records = d['records']
            scoped = [u for u in known if u['path'] == path]
            relevant = [u for u in scoped if set(u.get('relations', [])) & {
                'changed_function','deleted_override_owner','changed_class_value_owner'}]
            if not relevant:
                relevant = [u for u in scoped if u.get('focus_attrs')]
            focus, protocols = set(), set()
            for u in relevant:
                rec = next((r for r in records if (r['start'],r['end'])==(u['start'],u['end'])),None)
                if rec:
                    features=_feature(rec);u['consumer_features']=features
                    focus |= set(u.get('focus_attrs', [])) | set(u.get('attrs', [])) | set(features['writes'])
                    protocols |= set(features['protocols'])
            for u in scoped:
                rec = next((r for r in records if (r['start'],r['end'])==(u['start'],u['end'])),None)
                if rec:
                    u['consumer_features']=_feature(rec);u['consumer_focus']=sorted(focus)
            # Derive methods inside already-known class scopes, plus structurally
            # resolved same-file direct callees/factory constructors.
            scopes = [u for u in scoped if u['kind']=='class']
            for owner in scopes:
                if added >= MAX_NEW_FUNCTIONS_PER_PASS:
                    break
                owner_rec=next((r for r in records if r['kind']=='class' and r['start']==owner['start'] and r['end']==owner['end']),None)
                if owner_rec is None:
                    continue
                members=[r for r in records if r['kind']=='function' and r['owner_node'] is owner_rec['node']]
                for r in members:
                    if added >= MAX_NEW_FUNCTIONS_PER_PASS:
                        break
                    features=_feature(r);relation=None
                    if focus & set(features['guards']):relation='consumer_guard'
                    elif focus & set(features['reads']):relation='consumer_field_read'
                    elif r['name'] in protocols:relation='consumer_protocol'
                    elif r['name'] in ('__init__','__new__') and set(owner.get('relations',[])) & {'same_file_subclass_candidate','native_target'}:
                        relation='consumer_factory_constructor'
                    if relation:
                        before=len(self.data['units'])
                        u=self._unit(path,r,relation=relation,priority=0,depth=owner['depth']+1,
                                     parent=owner['id'],provenance='ast_fallback',backend='python-ast')
                        if u:
                            u['consumer_features']=features;u['consumer_focus']=sorted(focus)
                        added+=len(self.data['units'])-before
            for source in scoped:
                if source['kind']!='function' or source['state']!='body_supplied':
                    continue
                rec=next((r for r in records if r['start']==source['start'] and r['end']==source['end']),None)
                if rec is None:continue
                bound={x.arg for x in [*rec['node'].args.posonlyargs,*rec['node'].args.args,*rec['node'].args.kwonlyargs]}
                bound|={x.id for x in ast.walk(rec['node']) if isinstance(x,ast.Name) and isinstance(x.ctx,(ast.Store,ast.Del))}
                for imported in ast.walk(rec['node']):
                    if isinstance(imported,(ast.Import,ast.ImportFrom)):
                        bound.update(a.asname or a.name.split('.')[0] for a in imported.names)
                for call in [x for x in ast.walk(rec['node']) if isinstance(x,ast.Call)]:
                    if added>=MAX_NEW_FUNCTIONS_PER_PASS:break
                    name=_name(call.func);targets=[]
                    if isinstance(call.func,ast.Name) and name not in bound:
                        targets=[r for r in records if r['name']==name and r['qualname']==name]
                    elif isinstance(call.func,ast.Attribute) and isinstance(call.func.value,ast.Name) and call.func.value.id in ('self','cls'):
                        targets=[r for r in records if r['owner_node'] is rec['owner_node'] and r['name']==call.func.attr]
                    if len(targets)==1:
                        target=targets[0]
                        if target['kind']=='class':
                            targets=[r for r in records if r['owner_node'] is target['node'] and r['name'] in ('__new__','__init__')]
                        for target in targets:
                            before=len(self.data['units'])
                            u=self._unit(path,target,relation='consumer_factory_constructor' if target['name'] in ('__new__','__init__') else 'consumer_direct_callee',
                                priority=0,depth=source['depth']+1,parent=source['id'])
                            if u:u['consumer_features']=_feature(target);u['consumer_focus']=sorted(focus)
                            added+=len(self.data['units'])-before
                    elif not targets and isinstance(call.func,(ast.Name,ast.Attribute)) and name.split('.')[0] not in PROTOCOLS:
                        self._anchor(path,call.func,relation='consumer_external_callee',priority=0,
                                     depth=source['depth']+1,parent=source['id'])
                if protocols and rec['owner_node'] is not None:
                    for base in rec['owner_node'].bases:
                        self._anchor(path,base,relation='consumer_inherited_protocol',priority=0,
                                     depth=source['depth']+1,parent=source['id'])
        self.data['consumer_audit'].append({'kind':'enriched_known_scopes','paths':indexed,
            'added_function_units':added,'local_index_reads_are_separately_metered':True,
            'candidate_provenance':'structural_ast_not_runtime_binding'})

    def prioritize_claims(self, payload):
        self._payload=payload or {}
        self._enrich()
        ranked=rank(self,self._payload)
        self.data['consumer_audit'].append({'kind':'rank','ranking':ranked})

    def next_request(self,max_lines=READ_MAX_LINES,remaining_queries=None,source_tokens_remaining=None):
        if not 1<=max_lines<=READ_MAX_LINES:raise ValueError('read chunk limit must be between 1 and 200')
        if remaining_queries is not None and remaining_queries<=0:return None
        ranked=rank(self,self._payload)
        units={u['id']:u for u in self.data['units']}
        pending_ids={r.get('locator_id') for r in self.data['pending'].values()}
        locators={a['id']:a for a in self.data['locators'] if a['state']=='unresolved' and a['id'] not in pending_ids}
        # A useful exact-site lookup can precede generic/class gaps, but never
        # consumes the last body slot. High-priority known consumers come first.
        candidates=[('unit',r) for r in ranked['units'] if r['bucket']<6]
        candidates += [('locator',r) for r in ranked['locators'] if r['bucket']<3]
        candidates += [('unit',r) for r in ranked['units'] if r['bucket']>=6]
        candidates += [('locator',r) for r in ranked['locators'] if r['bucket']>=3]
        for kind,row in candidates:
            if kind=='locator':
                a=locators.get(row['id'])
                if not a or remaining_queries is not None and remaining_queries<2:continue
                request={'id':'frontier-'+a['id'],'operation':a['operation'],'path':a['path'],'query':a['name'],
                    'start':a['line'],'end':a['line'],'reason':f"Consumer-first exact source lookup: {a['relation']}; body required separately",
                    'origin':'body-frontier','rule_id':a['id']}
                self.data['pending'][request['id']]={**request,'locator_id':a['id'],'key':[a['operation'],a['id']]}
            else:
                u=units[row['id']]
                if u['state'] in ('body_supplied','blocked'):continue
                chunks=self._missing_chunks(u,max_lines)
                if remaining_queries is not None and len(chunks)>remaining_queries:continue
                if source_tokens_remaining is not None and u.get('remaining_token_estimate',0)>source_tokens_remaining:continue
                chunks=[(start,end) for start,end in chunks if ['read_lines',u['path'],start,end] not in self.data['attempted']]
                if not chunks:continue
                start,end=chunks[0];key=['read_lines',u['path'],start,end]
                request={'id':'frontier-'+_id(key),'operation':'read_lines','path':u['path'],'query':None,'start':start,'end':end,
                    'reason':f"Consumer-first body {u['qualname']} ({u['start']}-{u['end']}); {','.join(u['relations'])}; {u['provenance']}",
                    'origin':'body-frontier','rule_id':u['id']}
                self.data['pending'][request['id']]={**request,'unit_id':u['id'],'key':key}
            self.data['consumer_audit'].append({'kind':'reserved','request':dict(request),'ranking_bucket':row['bucket']})
            return request
        return None
