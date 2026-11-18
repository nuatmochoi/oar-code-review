"""Controller-owned static acquisition frontier, separate from causal judgment."""
import ast,copy,heapq,re
from collections import Counter
import context48 as core
from context48 import digest,source_tokens,SOURCE_KINDS,Limit,put

BUILTINS={'_'} # Names such as make/len/error can be shadowed; never drop them.

class ControllerFrontier:
    def __init__(self,collector,*,phase):
        self.c=collector;self.phase=phase;self.heap=[];self.sequence=0;self.visited=set();self.queued=set();self.records=[];self.edges=[];self.errors=[];self.deferred=[]
        state=getattr(collector,'controller_frontier_state',{})or{}
        self.visited={tuple(k)for k in state.get('visited',[])}
        for item in state.get('pending',[]):
            self.sequence+=1;item=copy.deepcopy(item);heapq.heappush(self.heap,(item['priority'],item.get('depth',0),self.sequence,item));self.queued.add(self.key(item['question']))

    @staticmethod
    def key(q):return tuple(q.get(k)for k in('snapshot','file','line','column','query','symbol','kind'))

    def add(self,q,priority=3,depth=0,parent=None,relation='static_candidate'):
        if q.get('symbol')in BUILTINS:return
        q={**q,'id':'AQ-'+digest(list(self.key(q)))[:14],'claim_id':'controller_exploration','condition_ids':[],'challenge_ids':[],'exploration_id':'','reason':'Controller static candidate: '+relation+'; identity/source only, no causal or runtime truth.'}
        key=self.key(q)
        if key in self.visited or key in self.queued:return
        self.sequence+=1;item=dict(question=q,priority=priority,depth=depth,parent=parent,relation=relation)
        heapq.heappush(self.heap,(priority,depth,self.sequence,item));self.queued.add(key)

    def question(self,snapshot,path,line,column,symbol,operation='definition',kind='value_path'):
        return dict(snapshot=snapshot,file=path,line=line,column=column,symbol=symbol,query=operation,kind=kind)

    def seed(self,questions=(),include_changed=True,claims=()):
        claim_evidence={x for c in claims for v in c.get('conditions',[])for x in v.get('evidence_ids',[])}
        focused={(e['snapshot'],e['path'])for e in self.c.evidence if e['id']in claim_evidence and e.get('kind')in SOURCE_KINDS}
        for e in self.c.evidence:
            if e['id']not in claim_evidence or e.get('kind')not in SOURCE_KINDS:continue
            self.sequence+=1;q=self.question(e['snapshot'],e['path'],e['start_line'],0,e['id'],'_expand_existing');q['id']='AQ-'+digest(list(self.key(q)))[:14]
            item=dict(question=q,priority=-2,depth=0,parent=None,relation='current_claim_source_structural_expansion',answer={'question':q,'selected_evidence_ids':[e['id']]})
            if self.key(q)not in self.visited:heapq.heappush(self.heap,(-2,0,self.sequence,item))
        for q in questions:
            if q.get('query')not in('runtime','environment'):
                priority=1 if q['query']=='search'else 0
                self.add(copy.deepcopy(q),priority,relation='existing_question_target_with_typed_anchor_recovery')
        if not include_changed:return
        visible={(e['snapshot'],e['path'])for e in self.c.evidence if e.get('kind')in SOURCE_KINDS}
        for snapshot in('HEAD','BASE'):
            for path,changed in sorted(self.c.changed[snapshot].items()):
                if not self.c.language_for(path):continue
                self.add(self.question(snapshot,path,1,0,'AST-file','_seed_ast'),-3 if(snapshot,path)in focused else 4 if(snapshot,path)in visible else 6,relation='changed_file_AST_inventory')

    def seed_file(self,q):
        snapshot=q['snapshot'];path=q['file'];changed=self.c.changed[snapshot][path];data=self.c._parse(snapshot,path)
        call_tokens={(c['line'],c.get('column',0),c['name'])for c in data['calls']}
        focused=any(item[3].get('relation')=='current_claim_source_structural_expansion' and item[3]['question']['snapshot']==snapshot and item[3]['question']['file']==path for item in self.heap)
        for symbol in data['_identifiers']:
            if symbol['line']not in changed:continue
            row=data['rows'][symbol['line']-1];tail=row.encode()[symbol['column']-1+len(symbol['name'].encode()):]
            if tail.startswith(b'.'):continue
            if symbol.get('role')=='declaration':continue
            salient=(symbol['line'],symbol['column'],symbol['name'])in call_tokens or symbol['name'].lstrip('_')[:1].isupper()
            priority=0 if focused and salient else 2 if salient else 5
            self.add(self.question(snapshot,path,symbol['line'],symbol['column'],symbol['name']),priority,relation='changed_AST_identifier_definition_candidate')
        for unit in data['units']:
            if unit.get('name_line')and any(unit['start_line']<=n<=unit['end_line']for n in changed):self.add(self.question(snapshot,path,unit['name_line'],unit['name_column'],unit['name'],'references','definition_location'),3,relation='changed_unit_reference_candidates')

    def _consumers(self,data,call,node_id,depth,snapshot,path):
        calls=data['calls'];assigned=set()
        if path.endswith('.go'):
            assigned={x['id']for x in call.get('assigned',[])if x.get('id')}
            for other in calls:
                args={x['id']for group in other.get('arg_bindings',[])for x in group if x.get('id')}
                if assigned&args:
                    q=self.question(snapshot,path,other['line'],other.get('column',0),other['name'],'definition','implementation_body')
                    self.add(q,1,depth+1,node_id,'returned_value_to_consumer_argument_candidate')
                    self.edges.append(dict(parent=node_id,relation='return_assignment_to_argument_candidate',bindings=sorted(assigned&args),file=path,line=other['line'],runtime_or_path_proven=False))
        else:
            node=call.get('node');owner=self.c._unit_at(data,call['line']);aliases=set()
            if node is None or owner is None:return
            for item in self.c._scope_nodes(owner['node']):
                if isinstance(item,ast.Assign)and item.value is node:aliases|={n.id for t in item.targets for n in ast.walk(t)if isinstance(n,ast.Name)}
                elif isinstance(item,ast.AnnAssign)and item.value is node and isinstance(item.target,ast.Name):aliases.add(item.target.id)
            for item in sorted(self.c._scope_nodes(owner['node']),key=lambda n:getattr(n,'lineno',0)):
                if getattr(item,'lineno',0)<=call['line']:continue
                if isinstance(item,ast.Assign)and isinstance(item.value,ast.Name)and item.value.id in aliases:
                    aliases|={x.id for t in item.targets for x in ast.walk(t)if isinstance(x,ast.Name)}
                if isinstance(item,ast.Call):
                    used={n.id for arg in list(item.args)+[a.value for a in item.keywords]for n in ast.walk(arg)if isinstance(n,ast.Name)}
                    if used&aliases:
                        target=next((c for c in calls if c.get('node')is item),None)
                        if target:
                            self.add(self.question(snapshot,path,target['line'],target.get('column',0),target['name'],'definition','implementation_body'),1,depth+1,node_id,'returned_value_to_consumer_argument_candidate')
                            self.edges.append(dict(parent=node_id,relation='simple_alias_return_to_argument_candidate',names=sorted(used&aliases),file=path,line=target['line'],runtime_or_path_proven=False))
                # Reassignment ends this simple lexical candidate; branches,
                # mutation, heap aliases and closure dispatch are unresolved.
                if isinstance(item,(ast.Assign,ast.AnnAssign)):
                    targets=item.targets if isinstance(item,ast.Assign)else[item.target]
                    writes={x.id for t in targets for x in ast.walk(t)if isinstance(x,ast.Name)}
                    if not(isinstance(item.value,ast.Name)and item.value.id in aliases):aliases-=writes

    def expand_answer(self,item,answer):
        q=item['question'];node_id=q['id'];depth=item['depth'];snapshot=q['snapshot'];path=q['file']
        # Local return/assignment consumers are inspected at the actual call
        # token, independent of a model asking another question.
        try:
            caller=self.c._parse(snapshot,path)
            for call in caller['calls']:
                if call['name']==q['symbol'].rsplit('.',1)[-1]and abs(call['line']-q['line'])<=3:self._consumers(caller,call,node_id,depth,snapshot,path)
        except (ValueError,OSError,RuntimeError)as e:self.errors.append(dict(stage='consumer_candidates',node=node_id,error=str(e)))
        ids=set(answer.get('selected_evidence_ids',[])+answer.get('already_available_evidence_ids',[])+[e['id']for e in answer.get('evidence',[])])
        for evidence in self.c.evidence:
            if evidence['id']not in ids:continue
            if evidence.get('kind')in('definition_location','reference_edge'):
                try:
                    data=self.c._parse(evidence['snapshot'],evidence['path']);line=evidence['start_line'];is_definition=evidence['kind']=='definition_location'
                    unit=self.c._unit_at(data,line,definition=is_definition)
                    if unit and unit.get('name_line'):
                        self.add(self.question(evidence['snapshot'],evidence['path'],unit['name_line'],unit['name_column'],unit['name'],'body','value_path'),1 if is_definition else 3,depth+1,node_id,'definition_declaration_body'if is_definition else'reference_enclosing_context_candidate')
                    if not is_definition:
                        call=next((c for c in data['calls']if c['line']==line and c['name']==q['symbol'].rsplit('.',1)[-1]),None)
                        self.edges.append(dict(parent=node_id,relation='AST_classified_direct_call_reference'if call else'noncall_reference_candidate',file=evidence['path'],line=line,runtime_or_path_proven=False))
                        if call:self._consumers(data,call,node_id,depth,evidence['snapshot'],evidence['path'])
                except (ValueError,OSError,RuntimeError)as e:self.errors.append(dict(stage='reference_or_declaration_classification',node=node_id,error=str(e)))
                continue
            if evidence.get('kind')not in SOURCE_KINDS:continue
            try:
                data=self.c._parse(evidence['snapshot'],evidence['path']);snap=evidence['snapshot'];file=evidence['path']
                units=[u for u in data['units']+data['_declarations']if u['start_line']==evidence['start_line']and u['end_line']==evidence['end_line']]
                for unit in units:
                    if unit['kind']in('function','closure'):
                        global_names={u['name']for u in data['_declarations']if (lambda owner:owner is None or owner['kind']not in('function','closure','class'))(self.c._unit_at(data,u['name_line']))}
                        if file.endswith('.py'):
                            for imported in data['tree'].body:
                                if isinstance(imported,(ast.Import,ast.ImportFrom)):global_names|={a.asname or a.name.split('.')[0]for a in imported.names}
                        call_tokens={(c['name'],c['line'],c.get('column',0))for c in data['calls']}
                        for ident in data['_identifiers']:
                            if ident['name']not in global_names and not ident['name'].lstrip('_')[:1].isupper():continue
                            if not unit['start_line']<=ident['line']<=unit['end_line']:continue
                            if (ident['name'],ident['line'],ident['column'])in call_tokens:continue
                            row=data['rows'][ident['line']-1];tail=row.encode()[ident['column']-1+len(ident['name'].encode()):]
                            if tail.startswith(b'.'):continue
                            self.add(self.question(snap,file,ident['line'],ident['column'],ident['name']),0,depth+1,node_id,'function_global_value_or_type_definition_candidate')
                    if unit['kind']in('type_declaration','class'):
                        for method in data['units']:
                            receiver=method.get('receiver_type','').lstrip('*')
                            python_method=file.endswith('.py')and method.get('is_method')and unit['start_line']<=method['start_line']<=method['end_line']<=unit['end_line']
                            if receiver==unit['name']or python_method:
                                self.add(self.question(snap,file,method['name_line'],method['name_column'],method['name'],'body','implementation_body'),-1,depth+1,node_id,'resolved_type_receiver_method_source_candidate')
                    if unit['kind'].endswith('_declaration'):
                        refs=unit.get('declaration_refs',[])
                        if file.endswith('.py')and unit.get('node')is not None:
                            value=unit['node'].value;refs=[dict(code=n.id,start_line=n.lineno,start_column=n.col_offset+1)for n in ast.walk(value)if isinstance(n,ast.Name)]if value is not None else[]
                        for ref in refs:
                            name=ref['code'];row=data['rows'][ref['start_line']-1];tail=row.encode()[ref['start_column']-1+len(name.encode()):]
                            if name==unit['name']or tail.startswith(b'.'):continue
                            self.add(self.question(snap,file,ref['start_line'],ref['start_column'],name),0,depth+1,node_id,'declaration_type_or_initializer_dependency_candidate')
                    for call in data['calls']:
                        if unit['start_line']<=call['line']<=unit['end_line']:
                            discriminating=any(c.get('start_line',0)<=call['line']<=c.get('end_line',-1)for c in unit.get('conditions',[]))
                            self.add(self.question(snap,file,call['line'],call.get('column',0),call['name'],'definition','implementation_body'),1 if discriminating else 4+depth,depth+1,node_id,'condition_call_definition_candidate'if discriminating else'source_body_call_definition_candidate')
            except (ValueError,OSError,RuntimeError)as e:self.errors.append(dict(stage='expand_source',node=node_id,evidence_id=evidence['id'],error=str(e)))

    def run(self,*,source_cap,native_cap):
        before=copy.deepcopy(self.c.stats);old_native=self.c.budgets['max_native_calls'];old_source=self.c.active_source_cap;self.c.budgets['max_native_calls']=min(old_native,native_cap);self.c.active_source_cap=source_cap
        reason='frontier_exhausted'
        try:
            while self.heap:
                self.c.query_started=__import__('time').monotonic()
                nextq=self.heap[0][3]['question'];cheap=nextq['query']in('_seed_ast','_expand_existing','body')
                expected_native=1 if cheap and str(nextq['file']).endswith('.go')else 0 if cheap else 3
                if self.c.stats['native_calls']+expected_native>native_cap:reason='native_budget_reserved_or_exhausted';break
                if source_tokens(self.c.evidence)>=source_cap-64:reason='source_budget_reserved_or_exhausted';break
                if self.c.stats['file_reads']>=self.c.budgets['max_file_reads']-4:reason='file_read_budget_reserved_or_exhausted';break
                self.c._check();_,_,_,item=heapq.heappop(self.heap);q=item['question'];key=self.key(q);self.queued.discard(key)
                if key in self.visited:continue
                if q['query']=='_seed_ast':
                    start=copy.deepcopy(self.c.stats)
                    try:self.seed_file(q)
                    except (ValueError,OSError,RuntimeError)as e:self.errors.append(dict(stage='seed_changed',file=q['file'],error=str(e)))
                    self.visited.add(key);self.records.append(dict(node=q['id'],parent=None,relation=item['relation'],depth=0,question=q,receipt=None,binding={'status':'AST_inventory_only'},evidence_ids=[],unresolved=[],cost={k:self.c.stats.get(k,0)-start.get(k,0)for k in self.c.stats}));continue
                if q['query']=='_expand_existing':
                    start=copy.deepcopy(self.c.stats)
                    try:self.expand_answer(dict(question=item['answer']['question'],depth=0),item['answer'])
                    except (ValueError,OSError,RuntimeError)as e:self.errors.append(dict(stage='expand_existing_answer',error=str(e)))
                    self.visited.add(key);self.records.append(dict(node=q['id'],parent=None,relation=item['relation'],depth=0,question=q,receipt=item['answer'].get('query_receipt'),binding={'status':'existing_query_structural_expansion'},evidence_ids=item['answer'].get('selected_evidence_ids',[]),unresolved=[],cost={k:self.c.stats.get(k,0)-start.get(k,0)for k in self.c.stats}));continue
                answer=self.c.query(q,keep_stage_cap=True);self.visited.add(key)
                self.records.append(dict(node=q['id'],parent=item['parent'],relation=item['relation'],depth=item['depth'],question=q,receipt=answer['query_receipt'],binding=answer.get('callee_binding'),evidence_ids=answer.get('selected_evidence_ids',[]),unresolved=answer.get('unresolved',[]),cost=answer['cost']))
                try:self.expand_answer(item,answer)
                except Limit as e:self.errors.append(dict(stage='expand_budget',node=q['id'],error=str(e)))
                for issue in answer.get('unresolved',[]):
                    if issue.get('reason')=='whole_body_source_token_cap'and source_cap<self.c.budgets['max_source_tokens']and issue.get('union_tokens_if_added',10**9)<=self.c.budgets['max_source_tokens']:
                        self.visited.discard(key);self.deferred.append(item);break
        except Limit as e:reason=str(e)
        finally:self.c.budgets['max_native_calls']=old_native;self.c.active_source_cap=old_source
        pending=[item for _,_,_,item in self.heap]+self.deferred
        self.c.controller_frontier_state=dict(visited=[list(k)for k in sorted(self.visited,key=str)],pending=pending)
        result=dict(phase=self.phase,records=self.records,static_edges=self.edges,errors=self.errors,remaining_candidates=len(pending),termination_reason=reason,incremental_cost={k:self.c.stats.get(k,0)-before.get(k,0)for k in self.c.stats},source_tokens_after=source_tokens(self.c.evidence),source_cap=source_cap,native_cap=native_cap,semantic_truth_guaranteed=False,runtime_dispatch_confirmed=False)
        put(self.c.out/f'controller-frontier-{self.phase}.json',result);return result

def compact(report):
    value=dict(phase=report['phase'],queried_nodes=len(report['records']),source_queries=sum(not r['question']['query'].startswith('_')for r in report['records']),structural_steps=sum(r['question']['query'].startswith('_')for r in report['records']),remaining_candidates=report['remaining_candidates'],termination_reason=report['termination_reason'],static_edge_candidates=len(report['static_edges']),semantic_truth_guaranteed=False,
        unresolved_reasons=dict(Counter(x.get('reason','unknown')for r in report['records']for x in r.get('unresolved',[]))),targets=[dict(symbol=r['question']['symbol'],file=r['question']['file'],relation=r['relation'],binding=(r.get('binding')or{}).get('status'),evidence_ids=r['evidence_ids'])for r in report['records']])
    while core.tokens(__import__('json').dumps(value,ensure_ascii=False))>800 and value['targets']:value['targets'].pop()
    return value

def acquire(collector,questions=(),*,phase,include_changed=True,completed_answers=(),claims=()):
    frontier=ControllerFrontier(collector,phase=phase)
    for answer in completed_answers:
        if answer.get('execution')!='backend_query':continue
        binding=answer.get('callee_binding')or{}
        q=answer['question']
        if binding.get('target_body_ids'):frontier.visited.add(frontier.key(q))
        frontier.sequence+=1;control={**q,'query':'_expand_existing'};item=dict(question=control,priority=-2,depth=0,parent=None,relation='existing_model_query_structural_expansion',answer=answer)
        heapq.heappush(frontier.heap,(item['priority'],0,frontier.sequence,item))
    frontier.seed(questions,include_changed,claims)
    remaining=max(0,collector.budgets['max_source_tokens']-source_tokens(collector.evidence));reserve=min(2000,remaining//2)
    return frontier.run(source_cap=collector.budgets['max_source_tokens']-reserve if phase=='before_replan'else collector.budgets['max_source_tokens'],native_cap=34 if phase=='before_replan'else 46)
