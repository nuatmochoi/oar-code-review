"""AST call coordinates -> LSP definition -> source body, with explicit binding.

No PR-specific symbols/paths or private labels. Ambiguous nearby calls remain
ambiguous. Literal matches cannot certify a requested callee implementation.
"""
import copy,re
from context48 import SOURCE_KINDS,put
from search_context import ContextCollector as SearchCollector

class ContextCollector(SearchCollector):
    def _callsite(self,q):
        symbol=q.get('symbol','').rsplit('.',1)[-1]
        if not re.fullmatch(r'[^\W\d]\w*',symbol):return None,[]
        if not self.language_for(q['file']):return None,[]
        data=self._parse(q['snapshot'],q['file']);line=q['line']
        # AST occurrences are calls, never comments or enclosing caller names.
        matches={(c['line'],c.get('column',0)):c for c in data['calls']if c['name']==symbol}
        exact=[c for c in matches.values()if c['line']==line]
        if exact:candidates=exact
        else:
            owner=self._unit_at(data,line)
            candidates=[c for c in matches.values()if owner and abs(c['line']-line)<=3 and self._unit_at(data,c['line'])==owner]
        catalog=[dict(line=c['line'],column=c.get('column',0),symbol=c['name'])for c in candidates]
        if len(candidates)!=1:return None,catalog
        c=candidates[0];bound={**q,'line':c['line'],'column':c.get('column',0),'symbol':c['name']}
        self._anchor(bound,data) # Check actual UTF-8 token before native RPC.
        return bound,catalog

    def _lookup(self,q,operation):
        if operation not in('definition','implementation'):
            return super()._lookup(q,operation)
        bound,candidates=self._callsite(q)
        if len(candidates)>1:
            self.unresolved.append(dict(reason='ambiguous_AST_callsite_candidates',candidates=candidates,requested_symbol=q.get('symbol')))
            self._binding=dict(status='ambiguous_callsite',candidates=candidates,target_body_ids=[],semantic_truth_guaranteed=False)
            return []
        effective=bound or q
        # A concrete call's declaration is found with definition. The LSP
        # implementation operation is not a synonym for obtaining its body.
        selected_operation='definition'if bound and q.get('kind')in('implementation_body','exception_branch','value_path')else operation
        answer=super()._lookup(effective,selected_operation)
        key=(effective['snapshot'],effective['file'],effective.get('line',0),effective.get('column',0),selected_operation,effective.get('symbol',''))
        native=self.native_lookup_cache.get(key,{})
        targets=[];body_ids=[]
        for row in native.get('locations',[]):
            path=self._native_path(q['snapshot'],row)
            targets.append(dict(path=path,line=row['line_start'],column=row.get('column_start')))
            body_ids.extend(e['id']for e in answer if e.get('kind')in SOURCE_KINDS and e.get('mechanical_checks',{}).get('complete_unit')and e['path']==path and e['start_line']<=row['line_start']<=e['end_line'])
        status='multiple_LSP_definition_candidates'if len(targets)>1 else'LSP_definition_to_complete_body'if body_ids and selected_operation=='definition'else'LSP_locations_only'if targets else'unresolved'
        if len(targets)>1:self.unresolved.append(dict(reason='multiple_LSP_definition_candidates',targets=targets,single_runtime_target_confirmed=False))
        self._binding=dict(status=status,
            requested_symbol=q.get('symbol'),original_anchor={k:q.get(k)for k in('snapshot','file','line','column')},
            actual_anchor={k:effective.get(k)for k in('snapshot','file','line','column')},AST_callsite_candidates=candidates,
            requested_operation=operation,effective_operation=selected_operation,LSP_targets=targets,target_body_ids=list(dict.fromkeys(body_ids)),
            actual_static_callsite_bound=bound is not None,multiple_definition_candidates=len(targets)>1,runtime_dispatch_confirmed=False,semantic_truth_guaranteed=False)
        self.events.append(dict(event='requested_symbol_binding',binding=copy.deepcopy(self._binding)))
        return answer

    def _search(self,q):
        bound,candidates=self._callsite(q)
        if bound is not None or candidates:
            # A search hint referring to a call is resolved as a call. Do not
            # spend the source budget collecting unrelated enclosing callers.
            return self._lookup(q,'definition')
        result=super()._search(q)
        self._binding=dict(status='literal_candidates_only',target_body_ids=[],requested_symbol=q.get('symbol'),runtime_dispatch_confirmed=False,semantic_truth_guaranteed=False)
        return result

    def query(self,q,*,keep_stage_cap=False):
        self._binding=None
        answer=super().query(q,keep_stage_cap=keep_stage_cap)
        answer['raw_query_receipt']=answer['query_receipt']
        answer['callee_binding']=copy.deepcopy(self._binding or dict(status='not_established',target_body_ids=[],semantic_truth_guaranteed=False))
        # Keep the original backend receipt immutable and persist the binding
        # annotation in its own canonical replay input.
        path=self.out/f'bound-query-{self.sequence:04d}.json'
        put(path,{k:v for k,v in answer.items()if k!='query_receipt'})
        answer['query_receipt']=str(path)
        return answer
