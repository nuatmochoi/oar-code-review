"""One initial review+query call, one evidence-conditioned replan, one final."""
from copy import deepcopy
from schemas import REVIEW, obj, arr, S, enum
from planner_schema import PLAN
from claim_scope import with_claim_scopes

INITIAL = deepcopy(REVIEW)
INITIAL['properties'].update(deepcopy(PLAN['properties']))
INITIAL['required'] = list(INITIAL['properties'])
ANSWER = obj(dict(question_id=S, outcome=enum('supported','refuted','unresolved'),
    evidence_ids=arr(S), remaining_question=S, next_question_ids=arr(S), reason=S))
REPLAN = deepcopy(PLAN)
REPLAN['properties']['answer_assessments'] = arr(ANSWER)
REPLAN['required'].append('answer_assessments')
REPLAN=with_claim_scopes(REPLAN)

def plan_only(payload):
    return {k: deepcopy(payload[k]) for k in PLAN['properties']}

def review_only(payload):
    return {k: deepcopy(payload[k]) for k in REVIEW['properties']}

def audit_replan(payload, first_execution, evidence):
    """IDs/coverage only; semantic support/refutation is never certified."""
    expected = {a['question']['id'] for a in first_execution.get('answers',[])}
    available = {e['id'] for e in evidence}
    next_ids = {q['id'] for q in payload['questions']}
    seen = set(); errors=[]
    for a in payload['answer_assessments']:
        if a['question_id'] in seen: errors.append('duplicate_answer_assessment')
        seen.add(a['question_id'])
        if not set(a['evidence_ids']) <= available: errors.append('undelivered_answer_evidence')
        if not set(a['next_question_ids']) <= next_ids: errors.append('missing_followup_question')
        if a['outcome'] in ('supported','refuted') and not a['evidence_ids']:
            errors.append('semantic_verdict_without_source')
        if not a['reason'].strip(): errors.append('empty_answer_reason')
    if seen != expected: errors.append('answer_assessment_not_exact_partition')
    return dict(accepted=not errors, errors=errors, semantic_truth_guaranteed=False)
