"""Real live-question execution and separately recorded safe exploratory reads."""
import copy,json
from pathlib import Path
from common import digest
from warning_tracker import physical_rows
from planner_schema import PLAN
from reexplore_schema import ANSWER
from schemas import obj,arr,S
from location_ids import wire_schema
from acquisition_frontier import Frontier
from prompts_lsp48 import changed_operations
from reexplore_run import RecordedAnswers

def initial_schema(evidence):
    schema,cat=wire_schema(evidence)
    schema['properties'].update(copy.deepcopy(PLAN['properties']))
    schema['required']=list(schema['properties'])
    return schema,cat

def replan_schema():
    schema=copy.deepcopy(PLAN);schema['properties']['answer_assessments']=arr(copy.deepcopy(ANSWER));schema['required'].append('answer_assessments');return schema

def plan_only(payload):return {k:copy.deepcopy(payload[k])for k in PLAN['properties']}

def attach(f,es):
    f.controller.add_evidence(es)
    for e in es:
        if e['id']in f.evidence:assert f.evidence[e['id']]==e
        else:f.evidence[e['id']]=copy.deepcopy(e)

def safe_anchor(q,evidence):
    if q['query']not in ['definition','references','implementation','body','flow','environment','search']:return False
    if not q.get('reason','').strip()or q['line']<1 or q['column']<0:return False
    return any(e['snapshot']==q['snapshot']and e['path']==q['file']and q['line']in physical_rows(e)[0]for e in evidence)

def execute(f,plan,tool,*,enabled):
    before=list(f.evidence.values());core=f.execute_plan(plan,tool,enable_queries=enabled);extra=[]
    # A stale route never becomes a valid route. A visible read may still collect
    # neutral evidence, so a schema/history mistake cannot deadlock exploration.
    if enabled:
        for answer in core.get('answers',[]):
            q=answer['question']
            if answer.get('execution')!='question_rejected'or not safe_anchor(q,before):continue
            problems={p for u in answer.get('unresolved',[])for p in u.get('errors',[])}
            if not problems or not problems<={'question_target_not_live_in_proposal','question_without_live_target','unbound_exploration_question','question_snapshot_mismatch'}:continue
            recorded=tool.query(q)
            extra.append(dict(original_question=copy.deepcopy(q),original_rejection=copy.deepcopy(answer),native_answer=recorded,status='independent_visible_source_read',original_target_remains_rejected=True,semantic_status_promoted=False))
    context=tool.export()if tool is not None else None
    if context is not None:attach(f,context['evidence'])
    return dict(core=core,independent_reads=extra,controller_after=f.export(),context=context)

def replay(case,shared):
    f=Frontier(shared['initial'],shared['initial_evidence'],changed_operations=changed_operations(case,shared['initial_evidence']))
    for index,(payload,record) in enumerate([(shared['initial'],shared['first']), (shared['replan'],shared['second'])]):
        if index:
            from persistent_context import release_budget_cached_answers
            release_budget_cached_answers(f)
        original=record['core'];rebuilt=f.execute_plan(plan_only(payload),RecordedAnswers(original),enable_queries=True)
        assert digest(rebuilt)==digest(original),'Controller replay differs from its actual recorded execution'
        attach(f,record['context']['evidence'])
    return f

POLICY='''This is a provisional causal investigation, not proof that the PR is buggy. Keep prior candidate, condition and challenge identities/history explicit; a refuted API assumption belongs to history and is not a current necessary condition. Separate the changed operation, concrete failure mechanism, trigger and unverified broader impact. When a source refutes an assumed API behavior, revise or withdraw that explanation and, if still material, ask about the next actual producer/consumer/guard. Definition positions and reference edges alone do not answer implementation/runtime questions. Unknown evidence or a failed read is not refutation. Queries must target a current condition/pending challenge via condition_ids/challenge_ids; new required facts need explicit new current condition IDs. When no candidate exists, use a supplied changed-operation anchor with an exploration record; do not force a defect. New independent candidates are allowed, with zero also valid. Existing source is not filtered or evicted. Reuse source that actually answers a fact; otherwise query the smallest discriminating missing fact. Keep statements of fact separate from causal relevance. Preserve the reason for each correction/withdrawal; do not drop prior candidates merely because another candidate is ranked higher. Controller acceptance checks identity/shape/physical availability and does not certify semantic truth. Ask only static supported tools; runtime observations remain unresolved. Keep initial/updated candidate scope and necessary reasons intact; no generic self-reflection counts as executed verification.'''
