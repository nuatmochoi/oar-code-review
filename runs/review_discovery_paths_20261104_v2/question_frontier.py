"""Question-conditioned static acquisition, layered over existing native tools."""
import copy,heapq,re
from collections import Counter
import context48 as core
from controller_frontier import ControllerFrontier,compact
from discovery_policy import intent_for
from context48 import SOURCE_KINDS,source_tokens,put

class QuestionFrontier(ControllerFrontier):
    def __init__(self,collector,*,phase,intents):
        super().__init__(collector,phase=phase);self.intents=intents;self.current_goal=None;self.selection=copy.deepcopy(getattr(collector,'question_selection_state',{}));self.goals=copy.deepcopy(getattr(collector,'question_goals',{}));self.flow_scopes=set(getattr(collector,'question_flow_scopes',[]));self.reference_targets=set(getattr(collector,'question_reference_targets',[]))
    def add(self,q,priority=3,depth=0,parent=None,relation='static_candidate'):
        goal=self.current_goal or intent_for(q,self.intents);fact=goal['needed_fact'];name=q.get('symbol','').rsplit('.',1)[-1];focus=goal.get('focus_symbol','').rsplit('.',1)[-1]
        operation=q['query'];blocking=int(goal.get('blocking',False));resolved=any(e.get('kind')in SOURCE_KINDS and e['snapshot']==q['snapshot']and e['path']==q['file']and e['start_line']<=q['line']<=e['end_line']for e in self.c.evidence)
        relevance=3 if name==focus and focus else 0
        if operation=='references':relevance+=7 if fact in('consumer','value_transfer','producer')else 1
        if operation=='flow':relevance+=8 if fact in('consumer','producer','value_transfer','input_guard')else 3
        if 'condition' in relation or 'returned_value_to_consumer' in relation:relevance+=5
        if relation=='resolved_type_receiver_method_source_candidate':relevance+=7 if fact in('type_binding','callee_behavior','api_contract')else 2
        if relation=='function_global_value_or_type_definition_candidate':relevance+=4 if fact in('input_guard','type_binding','api_contract')else 0
        primitive=name in{'int','str','string','bool','error','nil','true','false','None','True','False'}
        expected_native=0 if operation=='body'and resolved else 1 if operation in('body','flow')else 3
        duplicate=4 if resolved and operation=='body'else 0
        score=6*blocking+relevance-.5*expected_native-duplicate-6*primitive-.2*depth
        selected_priority=-score if not operation.startswith('_')else priority
        old=self.current_goal;self.current_goal=goal
        super().add(q,selected_priority,depth,parent,relation)
        key=self.key(q);self.goals[str(key)]=copy.deepcopy(goal);self.selection[str(key)]=dict(goal=goal,score=score,priority=selected_priority,estimated_native_calls=expected_native,already_delivered_enclosing_source=resolved,semantic_or_runtime_proven=False)
        self.current_goal=old
    def seed(self,questions=(),include_changed=True,claims=()):
        # Existing claim sources still expand, but generic recursive inventory
        # no longer outranks concrete missing facts.
        super().seed(questions,False,claims)
        if include_changed and not questions and not claims:
            for snapshot in('HEAD','BASE'):
                if snapshot not in self.c.snapshots:continue
                for path,lines in sorted(self.c.changed[snapshot].items()):
                    if not self.c.language_for(path)or not lines:continue
                    self.add(self.question(snapshot,path,min(lines),0,'','flow','value_path'),3,relation='candidate_free_changed_value_flow')
    def expand_answer(self,item,answer):
        q=item['question'];old=self.current_goal;self.current_goal=self.goals.get(str(self.key(q)),intent_for(q,self.intents));goal=self.current_goal;fact=goal['needed_fact']
        try:
            if self.c.language_for(q['file']):
                data=self.c._parse(q['snapshot'],q['file']);owner=self.c._unit_at(data,q['line'])
                if owner and owner['kind']in('function','closure'):
                    scope=(q['snapshot'],q['file'],owner['start_line'],owner['end_line'])
                    if fact in('producer','consumer','value_transfer','input_guard','exception_path')and scope not in self.flow_scopes:
                        self.flow_scopes.add(scope);self.add({**q,'query':'flow','kind':'value_path'},0,item['depth']+1,q['id'],'question_selected_local_and_boundary_flow')
            ids=set(answer.get('selected_evidence_ids',[])+answer.get('already_available_evidence_ids',[])+[e['id']for e in answer.get('evidence',[])])
            if fact in('consumer','value_transfer'):
                for e in self.c.evidence:
                    if e['id']not in ids or e.get('kind')not in SOURCE_KINDS or not self.c.language_for(e['path']):continue
                    data=self.c._parse(e['snapshot'],e['path']);unit=self.c._unit_at(data,e['start_line'],definition=True)
                    if unit and unit.get('name_line')and unit['kind']=='function':
                        target=(e['snapshot'],e['path'],unit['name_line'],unit['name'])
                        if target not in self.reference_targets:
                            self.reference_targets.add(target);self.add(self.question(e['snapshot'],e['path'],unit['name_line'],unit['name_column'],unit['name'],'references','value_path'),0,item['depth']+1,q['id'],'question_selected_consumer_references')
            super().expand_answer(item,answer)
        except (core.Limit,ValueError,OSError,RuntimeError)as e:self.errors.append(dict(stage='question_selected_expansion',node=q.get('id'),error=str(e)))
        finally:self.current_goal=old
    def run(self,**kwargs):
        result=super().run(**kwargs);self.c.question_goals=self.goals;self.c.question_selection_state=self.selection;self.c.question_flow_scopes=list(self.flow_scopes);self.c.question_reference_targets=list(self.reference_targets)
        result.update(selection_policy='Question missing-fact relevance and estimated cost; not QAFD algorithm reproduction.',question_selections=[dict(node=r['node'],selection=self.selection.get(str(self.key(r['question']))))for r in result['records']],query_operations=dict(Counter(r['question']['query']for r in result['records']if not r['question']['query'].startswith('_'))))
        put(self.c.out/f'question-policy-{self.phase}.json',result);return result

def acquire(collector,questions=(),*,phase,claims=(),completed_answers=(),intents=()):
    f=QuestionFrontier(collector,phase=phase,intents=intents)
    for a in completed_answers:
        if a.get('execution')not in('backend_query','cached_answer_reused'):continue
        q=a['question']
        if (a.get('callee_binding')or{}).get('target_body_ids'):f.visited.add(f.key(q))
        f.sequence+=1;control={**q,'query':'_expand_existing'};item=dict(question=control,priority=-30,depth=0,parent=None,relation='question_answer_structural_expansion',answer=a);heapq.heappush(f.heap,(-30,0,f.sequence,item))
    f.seed(questions,phase=='before_replan',claims)
    remaining=max(0,collector.budgets['max_source_tokens']-source_tokens(collector.evidence));reserve=min(2000,remaining//2)
    return f.run(source_cap=collector.budgets['max_source_tokens']-reserve if phase=='before_replan'else collector.budgets['max_source_tokens'],native_cap=34 if phase=='before_replan'else 46)
