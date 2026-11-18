"""Question admission is independent of semantic-ledger/coverage acceptance.

A rejected plan remains rejected. Safe reads can still gather evidence needed
to repair it; they never promote supported/refuted, retire obligations, or make
a reference edge an execution path.
"""
import copy
from collections import Counter
import jsonschema
from schemas import CLAIM
from frontier import Frontier as LedgerFrontier,_structure,_targets,_unique,request_key,evidence_capability

class Frontier(LedgerFrontier):
    def _read_targets(self,claims):
        """Validate each read's identity, independently of route completeness.

        No route is repaired here, and archived/withdrawn/duplicate targets do
        not become live. An omitted unrelated route member cannot block reads
        linked to a unique, explicitly current condition or pending challenge.
        """
        targets={};counts=Counter(c.get('id')for c in claims if isinstance(c,dict)and isinstance(c.get('id'),str))
        for c in claims:
            if not isinstance(c,dict):continue
            cid=c.get('id')
            if not isinstance(cid,str)or not cid.strip()or counts[cid]!=1 or c.get('action')=='withdraw':continue
            route=c.get('route_assignment')or{}
            if not isinstance(route,dict):route={}
            raw_current=route.get('current_condition_ids',[]);raw_history=route.get('historical',[])
            current=Counter(x for x in(raw_current if isinstance(raw_current,list)else[])if isinstance(x,str))
            archived={x.get('condition_id')for x in(raw_history if isinstance(raw_history,list)else[])if isinstance(x,dict)and isinstance(x.get('condition_id'),str)}
            for field,target_kind in(('conditions','condition'),('challenges','challenge')):
                values=c.get(field,[])
                if not isinstance(values,list):continue
                ids=Counter(x.get('id')for x in values if isinstance(x,dict)and isinstance(x.get('id'),str))
                schema=CLAIM['properties'][field]['items']
                for value in values:
                    if not isinstance(value,dict):continue
                    tid=value.get('id')
                    if not isinstance(tid,str)or ids[tid]!=1 or not tid.strip()or not jsonschema.Draft202012Validator(schema).is_valid(value):continue
                    if target_kind=='condition':
                        if current[tid]!=1 or tid in archived:continue
                        targets[cid,target_kind,tid]={'snapshot':value['snapshot']}
                    elif value['state']=='pending':targets[cid,target_kind,tid]={'snapshot':None}
        return targets

    def _read_admission(self,plan):
        targets=self._read_targets(plan['claims']);qid_counts=Counter(q['id']for q in plan['questions'])
        declarations={x['id']:x for x in plan['explorations']};rows=[]
        for q in plan['questions']:
            problems=[]
            if not q['id'].strip()or qid_counts[q['id']]!=1:problems.append('missing_or_duplicate_question_id')
            if not q['reason'].strip()or q['line']<1 or q['column']<0:problems.append('invalid_question_request')
            if q['exploration_id']:
                canonical=self.changed_operations.get(q['exploration_id']);declaration=declarations.get(q['exploration_id'])
                if q['claim_id']!='exploration'or q['condition_ids']or q['challenge_ids']or canonical is None or declaration is None or q['id']not in declaration['question_ids']:
                    problems.append('unbound_exploration_question')
                if canonical is not None and not self._body_binding(canonical):problems.append('exploration_source_not_visible')
            else:
                links=[(q['claim_id'],'condition',k)for k in q['condition_ids']]+[(q['claim_id'],'challenge',k)for k in q['challenge_ids']]
                if not links:problems.append('question_without_live_target')
                for key in links:
                    target=targets.get(key)
                    if target is None:problems.append('question_target_not_live_in_proposal')
                    elif target['snapshot'] is not None and target['snapshot']!=q['snapshot']:problems.append('question_snapshot_mismatch')
            rows.append(dict(question_id=q['id'],accepted=not problems,errors=problems,semantic_relevance_guaranteed=False,
                source_scope='Actual file/symbol/range/environment permissions are additionally checked by the native backend.'))
        return rows

    def execute_plan(self,plan,backend=None,enable_queries=True):
        try:validation=self.validate_plan(plan)
        except Exception as error:validation=dict(accepted=False,complete=False,errors=[{'code':'plan_audit_exception','type':type(error).__name__}],diff_fact_bindings=[])
        if validation['accepted']:
            self.uncommitted_prior_raw_claims=[]
            return super().execute_plan(plan,backend,enable_queries)
        admissions=self._read_admission(plan);byid={q['id']:q for q in plan['questions']}
        prior=copy.deepcopy(self.raw_claims)
        # Preserve pending raw history if a proposal omits/invalidates IDs.
        history_errors={'raw_claim_omitted','raw_conditions_omitted','raw_challenges_omitted','invalid_raw_claim_requires_explicit_repair','invalid_raw_claim_id_requires_explicit_repair'}
        history_preserved=not _structure(plan['claims'])and not any(e.get('code')in history_errors for e in validation['errors'])
        if history_preserved:self.raw_claims=copy.deepcopy(plan['claims']);self.structural_errors=[]
        try:update=self.controller.update({'claims':plan['claims'],'questions':plan['questions']},len(self.versions))
        except Exception as error:update=dict(accepted=False,errors=[{'code':'auxiliary_ledger_exception','type':type(error).__name__}])
        self.versions.append(dict(stage='independent_question_admission',prior_raw_claims=prior,proposed_raw_claims=copy.deepcopy(plan['claims']),raw_history_preserved=history_preserved,plan_audit=copy.deepcopy(validation),controller_update=copy.deepcopy(update)))
        self.diff_fact_bindings=copy.deepcopy(validation.get('diff_fact_bindings',[]))
        self.uncommitted_prior_raw_claims=prior if not history_preserved else []
        answers=[];newids=[];selected=[];attempts=0
        for row in admissions:
            q=byid[row['question_id']];key=request_key(q)
            base=dict(question=copy.deepcopy(q),evidence=[],selected_evidence_ids=[],already_available_evidence_ids=[],cost={},cache_hit=False)
            if not row['accepted']:
                answer={**base,'execution':'question_rejected','unresolved':[{'reason':'question_admission_rejected','errors':row['errors']}]}
            elif not enable_queries:answer={**base,'execution':'withheld','unresolved':[{'reason':'query_withheld_same_source_control'}]}
            elif key in self.cache:
                cached=self.cache[key];ids=cached['all_evidence_ids'];original=cached['answer']
                answer={**base,'execution':'cached_answer_reused','cache_hit':True,'selected_evidence_ids':list(ids),'already_available_evidence_ids':list(ids),'unresolved':copy.deepcopy(original.get('unresolved',[])),'original_query_receipt':original.get('query_receipt')}
            elif backend is None:answer={**base,'execution':'unresolved','unresolved':[{'reason':'query_backend_unavailable'}]}
            else:
                attempts+=1
                try:answer=copy.deepcopy(backend.query(q));answer.update(execution='backend_query',cache_hit=False)
                except Exception as error:answer={**base,'execution':'unresolved','unresolved':[{'reason':'backend_query_exception','type':type(error).__name__}]}
                for e in answer.get('evidence',[]):
                    if e['id']in self.evidence:assert self.evidence[e['id']]==e
                    else:self.evidence[e['id']]=copy.deepcopy(e);newids.append(e['id'])
                self.controller.add_evidence(answer.get('evidence',[]));self.register_cached_answer(q,answer)
            ids=_unique(answer.get('selected_evidence_ids',[])+answer.get('already_available_evidence_ids',[])+[e['id']for e in answer.get('evidence',[])])
            answer['all_answer_evidence_ids']=ids
            answer['mechanically_compatible_evidence_ids']=[eid for eid in ids if eid in self.evidence and evidence_capability(self.evidence[eid],q['kind'],q['snapshot'])]
            answer['question_binding']=dict(claim_id=q['claim_id'],condition_ids=q['condition_ids'],challenge_ids=q['challenge_ids'],exploration_id=q['exploration_id'],live_structural_binding_valid=row['accepted'],semantic_relevance_guaranteed=False)
            if q['kind']=='implementation_body'and not answer['mechanically_compatible_evidence_ids']:answer.setdefault('unresolved',[]).append({'reason':'actual_complete_implementation_not_available'})
            selected.extend(ids);answers.append(answer)
        result=dict(accepted=True,complete=False,validation=validation,plan_structurally_accepted=False,
            query_admission=admissions,query_admission_accepted=True,raw_history_preserved=history_preserved,
            controller_update=update,answers=answers,coverage=[],new_evidence_ids=_unique(newids),selected_evidence_ids=_unique(selected),
            backend_query_attempts=attempts,cache_hits=sum(a.get('cache_hit',False)for a in answers),
            frontier=self.export(),semantic_truth_guaranteed=False,
            scope='Only question-level safe acquisition admitted; original plan errors, unresolved obligations and model semantic judgments remain unverified.')
        self.events.append(copy.deepcopy(result));return result

    def export(self):
        result=super().export()
        result['uncommitted_prior_raw_claims']=copy.deepcopy(getattr(self,'uncommitted_prior_raw_claims',[]))
        return result
