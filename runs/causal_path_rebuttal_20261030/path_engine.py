"""One shared bounded pre-final tool phase. No extra LLM judgment or finding gate."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path

from bootstrap import ROOT
import oar_core as oc
from body_frontier import BodyFrontier, ScopedIndex
from source_context import SourceAccount, SOURCE_CAP

ARMS=('path',)
QUERY_CAP=6

class HashScopedIndex(ScopedIndex):
    def __init__(self,root,audit,manifest):
        super().__init__(root,audit);self.manifest=manifest;self.file_cap=12

    def index(self,rel,text=None):
        if rel in self.cache:return self.cache[rel]
        if rel not in self.manifest:raise ValueError('index path outside frozen manifest')
        if len(self.cache)>=self.file_cap:
            self.audit.append(dict(path=rel,purpose='frontier_file_cap',read_succeeded=False,denied=True))
            raise ValueError('bounded frontier index file cap')
        event=dict(path=rel,purpose='manifest_verified_frontier_scope',read_succeeded=False)
        self.audit.append(event)
        raw=self.path(rel).read_bytes();event['read_succeeded']=True
        digest=hashlib.sha256(raw).hexdigest();event['source_sha256']=digest
        if digest!=self.manifest[rel]:raise ValueError('frontier source hash mismatch')
        return super().index(rel,raw.decode('utf-8',errors='replace'))

def normalized_frontier(c):
    data=copy.deepcopy(c['frontier']);data['coverage']={};data['pending']={}
    reset=[]
    for u in data['units']:
        reason=u.get('delivery_blocked_reason','')
        if u['state']=='blocked' and reason in ('review_ended_before_delivery_completion','no_following_body_slot_or_round',
                                               'source_budget_exhausted_new_source_blocked','model_or_query_budget_exhausted'):
            u['state']='unread';u.pop('delivery_blocked_reason',None);reset.append(u['id'])
        if u['state']!='body_supplied':u['expanded']=u['source_expanded']=False
    spans=[dict(s,lines=[n for n,_ in s['lines']],admitted=True) for s in c['sources'] if s['snapshot']=='head']
    f=BodyFrontier.from_packet(data,c['manifest']['root'],spans)
    f.index=HashScopedIndex(c['manifest']['root'],f.data['source_reads'],c['manifest']['sources'])
    f.prioritize_claims(json.loads(c['critic']['raw_response']))
    return f,reset

def converted(frontier,request):
    q=copy.deepcopy(request)
    if q['operation'] in ('go_to_definition','find_references','incoming_calls','outgoing_calls'):
        anchor=next((x for x in frontier.data['locators'] if x['id']==q.get('rule_id')),None)
        if anchor:
            q.update(path=anchor['path'],line=anchor['line'],column=anchor['column'],name=anchor['name'])
    return q

def identity(q):
    op=q['operation']
    if op=='read_lines':return op,q.get('path'),q.get('start'),q.get('end')
    if op=='symbol_search':return op,q.get('query',q.get('name'))
    if op=='document_symbols':return op,q.get('path')
    return op,q.get('path'),q.get('line'),q.get('column')

def skip_frontier_duplicate(frontier,request,reason='duplicate_new_phase_query'):
    # release alone makes an unresolved locator immediately eligible again.
    # Record a scheduler skip (not a new failed native execution).
    item=frontier.data['pending'].get(request['id'],{})
    frontier.release(request,reason=reason)
    if item.get('locator_id'):
        for a in frontier.data['locators']:
            if a['id']==item['locator_id']:a['state']='duplicate_skipped'
    elif item.get('key') and item['key'] not in frontier.data['attempted']:
        frontier.data['attempted'].append(item['key'])

def external_frontier(frontier,initial_sources):
    # The reused queue's internal enum 'body_supplied' drives structural
    # expansion. Here its new coverage is ONLY acquisition for planning.
    # Never expose that enum as proof the final model actually saw the body.
    data=frontier.to_dict()
    old={(s['file'],n) for s in initial_sources if s['snapshot']=='head' for n,_ in s['lines']}
    for u in data['units']:
        was_delivered=all((u['path'],n) in old for n in range(u['start'],u['end']+1))
        u['initially_delivered']=was_delivered
        u['delivered_by_new_call']=False
        if u['state']=='body_supplied' and not was_delivered:u['state']='acquired_pending_final_composition'
    return data

def run_retrieval(c,arm):
    assert arm in ARMS
    account=SourceAccount(c['sources']);frontier,reset=normalized_frontier(c)
    events=[];selector=None;new_queries=0;duplicate_skips=[]
    if arm!='replay':
        from native_executor import NativeExecutor
        from causal_path_selector import PathSelector
        if arm=='path':
            selector=PathSelector(critic_payload=json.loads(c['critic']['raw_response']),
                supplied_sources=c['sources'],saved_frontier=c['frontier'],root=c['manifest']['root'],manifest=c['manifest']['sources'])
        seen=set()
        # Preserve executed historical exact positions, including empty/failure.
        # A fresh budget does not silently reset unsuccessful native searches.
        for a in c['frontier']['locators']:
            if a['state'] in ('located','empty','failed'):
                seen.add(identity(dict(operation=a['operation'],path=a['path'],line=a['line'],column=a['column'])))
        for key in c['frontier'].get('attempted',[]):
            if len(key)==4 and key[0]=='read_lines':seen.add(tuple(key))
        with NativeExecutor(c['manifest']['root'],c['manifest']['sources'], location_selector=selector.select_native_locations, max_native_locations=8) as executor:
            for _ in range(64):
                remaining=QUERY_CAP-new_queries
                if remaining<=0:break
                is_gap=False
                request=selector.next_request(remaining,SOURCE_CAP-account.tokens) if selector else None
                if request is not None:is_gap=True
                else:
                    request=frontier.next_request(max_lines=200,remaining_queries=remaining,
                                                  source_tokens_remaining=SOURCE_CAP-account.tokens)
                if request is None:break
                request=copy.deepcopy(request)
                native_request=request if is_gap else converted(frontier,request)
                if not is_gap and request['operation']=='read_lines' and selector.should_skip_frontier_read(request):
                    duplicate_skips.append(dict(request=native_request,reason='masked_documentation_or_blank_only_chunk'))
                    skip_frontier_duplicate(frontier,request,reason='masked_documentation_or_blank_only_chunk')
                    continue
                if request['operation']!='read_lines' and remaining<2:
                    duplicate_skips.append(dict(request=native_request,reason='no_reserved_body_query'))
                    if is_gap:selector.observe(request,dict(status='budget_deferred',locations=[],lines=[]),[])
                    else:frontier.release(request,reason='no_reserved_body_query')
                    continue
                sig=identity(native_request)
                if request['operation']=='read_lines' and account.covered(request['path'],request['start'],request['end']):
                    ids=account.store.covers('head',request['path'],request['start'],request['end'])
                    existing=[dict(file=account.store.get(eid).file,snapshot='head',evidence_id=eid,
                                   lines=[list(x) for x in account.store.get(eid).lines]) for eid in ids]
                    duplicate_skips.append(dict(request=native_request,reason='source_already_visible_or_admitted'))
                    if is_gap:selector.observe(request,dict(status='already_supplied',locations=[],lines=[]),existing)
                    else:frontier.release(request,reason='source_already_visible_or_admitted')
                    continue
                if sig in seen:
                    duplicate_skips.append(native_request)
                    if is_gap:selector.observe(request,dict(status='duplicate_skipped',locations=[],lines=[]),[])
                    else:skip_frontier_duplicate(frontier,request)
                    continue
                seen.add(sig);new_queries+=1
                result=executor.execute(native_request)
                assert result['logical_queries']==1
                planning_result=copy.deepcopy(result)
                policy_locations=[]
                if request['operation']!='read_lines':
                    policy_locations=selector.filter_native_locations(native_request,result.get('locations',[]))
                    assert all(p in result.get('locations',[]) for p in policy_locations)
                    planning_result['locations']=policy_locations
                    if result['status']=='completed' and not policy_locations:planning_result['status']='filtered'
                admitted=[]
                if request['operation']=='read_lines' and result.get('lines'):
                    source=account.admit(request['path'],result['lines'],request,
                                         'causal_path' if is_gap else 'bounded_frontier')
                    if source:admitted.append(source)
                spans=[dict(s,admitted=True) for s in admitted]
                status=planning_result['status']
                if status=='empty':status='completed'
                if request['operation']=='read_lines' and result.get('lines') and not admitted:status='source_budget_blocked'
                locations=[dict(p,line_start=p.get('start',p.get('line_start')),
                                line_end=p.get('end',p.get('line_end',p.get('start'))),
                                column_start=p.get('column',p.get('column_start')))
                           for p in policy_locations] if request['operation']!='read_lines' else []
                frontier.observe(request,supplied_spans=spans,locator_locations=locations,status=status)
                if selector:
                    if status=='source_budget_blocked':planning_result['status']='source_budget_blocked'
                    if is_gap:selector.observe(request,planning_result,admitted)
                    else:selector.observe_external(request,planning_result,admitted)
                events.append(dict(sequence=new_queries,selector='gap' if is_gap else 'frontier',
                                   request=native_request,result=result,admitted_sources=admitted,
                                   policy_locations=policy_locations,planning_status=status,
                                   source_union_tokens=account.tokens))
            executor_audit=copy.deepcopy(executor.audit)
        # Include the actual close/reap proof, populated by __exit__.
        executor_audit=copy.deepcopy(executor.audit)
    else:executor_audit=dict(logical_queries=0,cleanup={'not_started':True})
    raw=copy.deepcopy(c['raw']);extra=account.rendered()
    if extra:
        opening='<causal_review_state>';pos=raw['suffix'].index(opening)
        raw['suffix']=raw['suffix'][:pos]+'<retrieval_evidence>\n'+extra+'</retrieval_evidence>\n'+raw['suffix'][pos:]
        assert raw['suffix'].replace('<retrieval_evidence>\n'+extra+'</retrieval_evidence>\n','',1)==c['raw']['suffix']
    # Composition coverage only. Actual completion input is audited separately.
    observed=account.verify_delivery(raw,c['prepared']['provided_evidence_ids'])
    assert account.tokens<=SOURCE_CAP and new_queries<=QUERY_CAP
    selector_summary=selector.summary() if selector else None
    if selector_summary and 'counters' in selector_summary:
        for key in ('native_locator_queries','body_read_queries'):
            if key in selector_summary['counters']:
                selector_summary['counters']['planner_observed_'+key]=selector_summary['counters'].pop(key)
        selector_summary['counter_semantics']='planner observed requests include duplicate/coverage skips; actual execution counts are trace.events and native_executor'
    return dict(raw=raw,provided_evidence_ids=list(dict.fromkeys(c['prepared']['provided_evidence_ids']+[s['evidence_id'] for s in account.added])),
                extra_sources=account.added,composed_visibility=observed,
                trace=dict(arm=arm,new_query_cap=QUERY_CAP,new_logical_queries=new_queries,events=events,
                           inherited_queries=c['inherited_queries'],initial_source_tokens=account.initial_tokens,
                           final_source_tokens=account.tokens,source_cap=SOURCE_CAP,source_admission=account.log,
                           normalized_budget_or_end_blocks=reset,duplicate_skips=duplicate_skips,
                           acquisition_frontier=external_frontier(frontier,c['sources']),
                           gap_selector=selector_summary,native_executor=executor_audit,
                           visibility_stage='prepared_composition_not_yet_a_model_call',
                           source_output_gate=False))
