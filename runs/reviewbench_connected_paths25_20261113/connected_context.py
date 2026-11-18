"""Controller-owned occurrence binding and connected static source exploration.

No private labels, runtime dispatch claims, relation inference from name equality,
or PR-specific branches. The initial query fixes are common to both policies.
"""
import copy, hashlib, json, re, time
from collections import deque
from pathlib import Path
from multilingual import ContextCollector as Multilingual
from symbol_context import AmbiguousSymbol
from context48 import Limit, SOURCE_KINDS, digest
from common import source_tokens


def occurrence_id(snapshot, path, source_hash, name, line, column):
    return 'O-' + digest([snapshot, path, source_hash, name, line, column])[:20]


def exact_occurrence(q, data):
    """Never silently jump from an external use to a same-named local definition."""
    name = q.get('symbol', '').rsplit('.', 1)[-1]
    if not re.fullmatch(r'[^\W\d]\w*', name):
        return None, [], 'non_identifier_description'
    hits = {(x['line'], x['column']): x for x in data.get('_identifiers', [])
            if x['name'] == name and x['line'] == q['line']}
    exact = [x for (line, column), x in hits.items() if column == q.get('column')]
    choices = exact if exact else list(hits.values())
    catalog = [dict(id=occurrence_id(q['snapshot'], q['file'], data['sha256'], name,
                                   x['line'], x['column']),
                    name=name, line=x['line'], column=x['column'], role=x.get('role'))
               for x in choices]
    if len(choices) != 1:
        return None, catalog, 'ambiguous_same_line' if choices else 'no_same_line_symbol'
    x = choices[0]
    return {**q, 'symbol': name, 'line': x['line'], 'column': x['column']}, catalog, 'exact_AST_occurrence'


class Collector(Multilingual):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.binding_audit = []
        self._paired_parse_cache = {}

    def _parse(self, snapshot, path):
        # The multilingual adapter checked its cache after reading the file.
        # Reuse only a hash-authorized, unchanged physical file; count hits apart
        # from actual reads. Same implementation is used by both policies.
        self._check()
        physical, expected, _ = self._target(snapshot, path)
        st=physical.stat();key=(snapshot,path,expected)
        stamp=(st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
        cached=self._paired_parse_cache.get(key)
        if cached and cached[0]==stamp:
            self.stats['ast_memory_cache_hits']+=1
            return cached[1]
        data=super()._parse(snapshot,path)
        self._paired_parse_cache[key]=(stamp,data)
        return data

    def _symbol_anchor(self, q):
        data = self._parse(q['snapshot'], q['file'])
        actual, catalog, state = exact_occurrence(q, data)
        row = dict(requested={k:q.get(k) for k in ('snapshot','file','line','column','symbol')},
                   state=state, candidates=catalog, semantic_target_guaranteed=False)
        self.binding_audit.append(row)
        if actual is None:
            self.unresolved.append(dict(reason='occurrence_binding_unresolved', **row))
            raise AmbiguousSymbol(state)
        self._anchor(actual, data)
        self.events.append(dict(event='controller_occurrence_binding', **row,
                                actual={k:actual[k] for k in ('line','column','symbol')}))
        return actual, catalog

    def _neutral_context(self, q, data=None):
        """A body fallback is context only; it does not satisfy a callee request."""
        data = data or self._read(q['snapshot'], q['file'])
        owner = self._unit_at(data, q['line']) if 'units' in data else None
        if owner is None:
            owner = dict(start_line=1, end_line=len(data['rows']), kind='statement')
        if owner['end_line'] < 1:
            return []
        body = self._body(q['snapshot'], q['file'], owner,
                          'neutral_context_after_unbound_description',
                          relation='enclosing_or_file_context_not_requested_callee_binding')
        self.unresolved.append(dict(reason='neutral_context_not_callee_or_flow_binding',
                                    file=q['file'], requested_symbol=q.get('symbol')))
        return [body] if body else []

    def _query_body(self, q):
        if not self.language_for(q['file']):
            return self._neutral_context(q)
        data = self._parse(q['snapshot'], q['file'])
        simple = q.get('symbol', '').rsplit('.', 1)[-1]
        if not re.fullmatch(r'[^\W\d]\w*', simple):
            return self._neutral_context(q, data)
        result = super()._query_body(q)
        if not result:
            result = self._neutral_context(q, data)
        return result

    def _lookup(self, q, operation):
        result = super()._lookup(q, operation)
        binding = self._binding or {}
        for target in binding.get('LSP_targets', []):
            self._metadata(q['snapshot'], q['file'], q['line'], 'reference_edge',
                dict(relation='LSP_'+operation+'_candidate',
                     origin=dict(path=q['file'],line=q['line'],symbol=q.get('symbol')),
                     target=target, runtime_dispatch_proven=False,
                     dataflow_proven=False), 'connected_source_relation_receipt')
        return result

    def _flow(self, q):
        if self.language_for(q['file']) in ('Python', 'Go'):
            return super()._flow(q)
        if not self.language_for(q['file']):
            self._neutral_context(q)
            self.unresolved.append(dict(reason='flow_language_not_supported', file=q['file']))
            return
        data = self._parse(q['snapshot'], q['file'])
        name = q.get('symbol','').rsplit('.',1)[-1]
        named = [u for u in data['units'] if u['name']==name and u['start_line']<=q['line']<=u['end_line']]
        owner = named[0] if len(named)==1 else self._unit_at(data,q['line'])
        if owner is None:
            self._neutral_context(q,data)
            return
        self._body(q['snapshot'],q['file'],owner,'flow_source_owner',relation='static_enclosing_body')
        node=owner.get('node');raw=data['text'].encode()
        def walk(n):
            if n is None:return
            yield n
            for ch in n.named_children:yield from walk(ch)
        kinds={'variable_declarator','assignment_expression','return_statement'}
        for n in walk(node):
            if n.type not in kinds or n.has_error:continue
            left=n.child_by_field_name('name') or n.child_by_field_name('left')
            right=n.child_by_field_name('value') or n.child_by_field_name('right')
            if n.type=='return_statement':
                relation='return_expression_syntax';value={'expression':raw[n.start_byte:n.end_byte].decode()}
            else:
                relation='assignment_syntax'
                value={'left':raw[left.start_byte:left.end_byte].decode() if left else None,
                       'right':raw[right.start_byte:right.end_byte].decode() if right else None}
            self._metadata(q['snapshot'],q['file'],n.start_point.row+1,'value_path',
                dict(relation=relation,**value,start_line=n.start_point.row+1,
                     end_line=n.end_point.row+1,scope_start=owner['start_line'],
                     CFG_heap_alias_or_cross_function_binding_proven=False),
                'tree_sitter_syntax_fact_not_runtime_value_proof')
        self.unresolved.append(dict(reason='cross_function_value_binding_unresolved',file=q['file'],
                                    partial_local_syntax_obtained=True))


def candidates(tool, evidence, parent=None, depth=1):
    """All supported calls/decl references in complete bodies; no relevance top-k."""
    groups=[];failures=[]
    for e in evidence:
        if e.get('kind') not in SOURCE_KINDS or not e.get('mechanical_checks',{}).get('complete_unit'):continue
        if not tool.language_for(e['path']):continue
        tool.query_started=time.monotonic()
        try:
            data=tool._parse(e['snapshot'],e['path'])
            rows=[]
            for c in data['calls']:
                if not e['start_line']<=c.get('start_line',c['line'])<=c.get('end_line',c['line'])<=e['end_line']:continue
                rows.append(dict(snapshot=e['snapshot'],file=e['path'],line=c['line'],column=c.get('column',0),
                    symbol=c['name'],query='definition',kind='implementation_body',
                    reason='Follow an actual AST call occurrence to static definition candidates.',
                    edge_kind='AST_callsite_to_LSP_definition_candidate'))
            for u in data['units']:
                if (u.get('name_line') and u['start_line']==e['start_line'] and u['end_line']==e['end_line']):
                    rows.insert(0,dict(snapshot=e['snapshot'],file=e['path'],line=u['name_line'],column=u['name_column'],
                        symbol=u['name'],query='references',kind='value_path',
                        reason='Find reference contexts of this complete unit; references need not be calls.',
                        edge_kind='LSP_reference_context_candidate'))
            for q in rows:
                q.update(id='G-'+digest([q,e['id'],parent,depth])[:16],claim_id='public_source_exploration',
                         parent_evidence_id=e['id'],parent_query_id=parent,depth=depth)
            groups.append(deque(rows))
        except (ValueError,OSError,SyntaxError,RuntimeError,Limit) as exc:
            failures.append(dict(evidence_id=e['id'],reason=type(exc).__name__,detail=str(exc)))
    result=[]
    while any(groups):
        for group in groups:
            if group:result.append(group.popleft())
    return result,failures


def query_key(q):
    return tuple(q.get(k) for k in ('snapshot','file','line','column','symbol','query','kind'))


def expand(tool, seed, policy):
    """FLAT: fixed first-hop pool. PATH: add descendants after actual retrieval.

    Both retain breadth; a path's discovery is not proof it can execute.
    """
    broad=deque(copy.deepcopy(seed));linked=deque();seen=set();records=[];deferred=[];errors=[];step=0
    while broad or linked:
        tool.query_started=time.monotonic()
        if (tool.stats['native_calls']>=tool.budgets['max_native_calls']-2 or
            tool.stats['file_reads']>=tool.budgets['max_file_reads']-8 or
            source_tokens(tool.evidence)>=tool.budgets['max_source_tokens']-64 or
            time.monotonic()-tool.started>=tool.budgets['max_wall_seconds']-20):
            deferred=list(broad)+list(linked);break
        queue=linked if linked and (not broad or step%3!=2) else broad
        q=queue.popleft();key=query_key(q)
        if key in seen:continue
        seen.add(key);step+=1
        answer=tool.query(q)
        records.append(dict(question=q,answer=answer))
        if policy=='PATH':
            # Only newly admitted bodies extend the route; cached cycles cannot
            # create infinite depth. Unvisited broad seeds remain in the queue.
            nxt,err=candidates(tool,answer.get('evidence',[]),parent=q['id'],depth=q['depth']+1)
            linked.extend(nxt);errors.extend(err)
    return dict(records=records,deferred=deferred,enumeration_errors=errors,
                policy=policy,static_connectivity_not_runtime_proof=True)
