"""Real live-question execution and separately recorded safe exploratory reads."""
import copy,json
from pathlib import Path
from common import digest
from warning_tracker import physical_rows
from planner_schema import PLAN
from reexplore_schema import ANSWER
from schemas import obj,arr,S,enum
from location_ids import wire_schema
from acquisition_frontier import Frontier
from prompts_lsp48 import changed_operations
from reexplore_run import RecordedAnswers

def question_routes(operations=()):
    base=copy.deepcopy(PLAN['properties']['questions']['items'])
    base['properties']['line']={'type':'integer','minimum':1};base['properties']['column']={'type':'integer','minimum':0}
    conditions=copy.deepcopy(base);conditions['properties']['exploration_id']=enum('');conditions['properties']['condition_ids'].update(minItems=1)
    challenges=copy.deepcopy(base);challenges['properties']['exploration_id']=enum('');challenges['properties']['condition_ids'].update(maxItems=0);challenges['properties']['challenge_ids'].update(minItems=1)
    alternatives=[conditions,challenges]
    if operations:
        exploration=copy.deepcopy(base);exploration['properties'].update(claim_id=enum('exploration'),condition_ids={'type':'array','items':S,'maxItems':0},challenge_ids={'type':'array','items':S,'maxItems':0},exploration_id=enum(*(op['id']for op in operations)));alternatives.append(exploration)
    return arr({'anyOf':alternatives})

def initial_schema(evidence,operations=()):
    schema,cat=wire_schema(evidence)
    schema['properties'].update(copy.deepcopy(PLAN['properties']))
    schema['required']=list(schema['properties'])
    schema['properties']['questions']=question_routes(operations)
    return schema,cat

def replan_schema(operations=()):
    schema=copy.deepcopy(PLAN);schema['properties']['questions']=question_routes(operations);schema['properties']['answer_assessments']=arr(copy.deepcopy(ANSWER));schema['required'].append('answer_assessments');return schema

def ordered_questions(questions):
    priority=[];broad=[]
    for q in questions:
        (priority if q['kind']in('implementation_body','value_path','exception_branch','dependency_version')else broad).append(copy.deepcopy(q))
    out=[]
    while priority or broad:
        out.extend(priority[:2]);priority=priority[2:]
        if broad:out.append(broad.pop(0))
    assert sorted(q['id']for q in out)==sorted(q['id']for q in questions)
    return out

def plan_only(payload):
    p={k:copy.deepcopy(payload[k])for k in PLAN['properties']};p['questions']=ordered_questions(p['questions']);return p

def attach(f,es):
    f.controller.add_evidence(es)
    for e in es:
        if e['id']in f.evidence:assert f.evidence[e['id']]==e
        else:f.evidence[e['id']]=copy.deepcopy(e)

def safe_anchor(q,evidence):
    if q['query']not in ['definition','references','implementation','body','flow','environment','search']:return False
    if not q.get('reason','').strip()or q['line']<1 or q['column']<0:return False
    return any(e['snapshot']==q['snapshot']and e['path']==q['file']and q['line']in physical_rows(e)[0]for e in evidence)

def read_authority(q,evidence,tool):
    if q['query']not in('definition','references','implementation','body','flow','environment','search')or not q.get('reason','').strip()or q['line']<1 or q['column']<0:return None
    if safe_anchor(q,evidence):return dict(basis='actually_visible_source',position_must_still_be_checked_by_native=True,semantic_truth_guaranteed=False)
    try:
        path,expected,binding=tool._target(q['snapshot'],q['file'])
        if not isinstance(expected,str)or len(expected)!=64 or any(c not in'0123456789abcdef'for c in expected):return None
        return dict(basis='frozen_repository_or_environment_source_manifest',expected_sha256=expected,binding=binding,source_not_yet_delivered=True,position_not_verified_until_native_query=True,semantic_truth_guaranteed=False)
    except(AttributeError,ValueError,KeyError,OSError):return None

def execute(f,plan,tool,*,enabled):
    # Search is a resumable discovery operation. A cached first page must not
    # prevent a later query from scanning the remaining candidate pool.
    if enabled:
        from frontier import request_key
        for q in plan['questions']:
            if q['query']=='search':f.cache.pop(request_key(q),None)
    before=list(f.evidence.values());core=f.execute_plan(plan,tool,enable_queries=enabled);extra=[]
    # A stale route never becomes a valid route. A visible read may still collect
    # neutral evidence, so a schema/history mistake cannot deadlock exploration.
    if enabled:
        for answer in core.get('answers',[]):
            q=answer['question']
            if answer.get('execution')!='question_rejected':continue
            problems={p for u in answer.get('unresolved',[])for p in u.get('errors',[])}
            if not problems or not problems<={'question_target_not_live_in_proposal','question_without_live_target','unbound_exploration_question','question_snapshot_mismatch'}:continue
            authority=read_authority(q,before,tool)
            if authority is None:continue
            recorded=tool.query(q)
            extra.append(dict(original_question=copy.deepcopy(q),original_rejection=copy.deepcopy(answer),native_answer=recorded,status='independent_manifest_authorized_source_read',read_authority=authority,original_target_remains_rejected=True,semantic_status_promoted=False))
    context=tool.export()if tool is not None else None
    if context is not None:attach(f,context['evidence'])
    return dict(core=core,independent_reads=extra,controller_after=f.export(),context=context)

def replay(case,shared):
    f=Frontier(shared['initial'],shared['initial_evidence'],changed_operations=changed_operations(case,shared['initial_evidence']))
    for index,(payload,record) in enumerate([(shared['initial'],shared['first']), (shared['replan'],shared['second'])]):
        if index:
            from persistent_context import release_budget_cached_answers
            release_budget_cached_answers(f)
        original=record['core']
        from frontier import request_key
        for q in plan_only(payload)['questions']:
            if q['query']=='search':f.cache.pop(request_key(q),None)
        rebuilt=f.execute_plan(plan_only(payload),RecordedAnswers(original),enable_queries=True)
        assert digest(rebuilt)==digest(original),'Controller replay differs from its actual recorded execution'
        attach(f,record['context']['evidence'])
    return f

POLICY='''This is a provisional causal investigation, not proof that the PR is buggy. Keep prior candidate, condition and challenge identities/history explicit; a refuted API assumption belongs to history and is not a current necessary condition. Separate the changed operation, concrete failure mechanism, trigger and unverified broader impact. When a source refutes an assumed API behavior, revise or withdraw that explanation and, if still material, ask about the next actual producer/consumer/guard. Definition positions and reference edges alone do not answer implementation/runtime questions. Unknown evidence or a failed read is not refutation. Queries must target a current condition/pending challenge via condition_ids/challenge_ids; new required facts need explicit new current condition IDs. When no candidate exists, use a supplied changed-operation anchor with an exploration record; do not force a defect. New independent candidates are allowed, with zero also valid. Existing source is not filtered or evicted. Reuse source that actually answers a fact; otherwise query the smallest discriminating missing fact. Keep statements of fact separate from causal relevance. Preserve the reason for each correction/withdrawal; do not drop prior candidates merely because another candidate is ranked higher. Controller acceptance checks identity/shape/physical availability and does not certify semantic truth. Ask only static supported tools; runtime observations remain unresolved. Keep initial/updated candidate scope and necessary reasons intact; no generic self-reflection counts as executed verification.'''
