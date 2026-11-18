"""A finding-free planner with explicit live-condition query bindings."""
from copy import deepcopy
from schemas import CLAIM,QUESTION,obj,arr,S,enum

LINKED_QUESTION=deepcopy(QUESTION)
LINKED_QUESTION['properties'].update(condition_ids=arr(S),challenge_ids=arr(S),exploration_id=S)
LINKED_QUESTION['required']+=['condition_ids','challenge_ids','exploration_id']
COVERAGE=obj(dict(claim_id=S,target_kind=enum('condition','challenge'),target_id=S,
 mode=enum('query','reuse','defer'),question_ids=arr(S),evidence_ids=arr(S),reason=S))
EXPLORATION=obj(dict(id=S,snapshot=enum('BASE','HEAD'),file=S,line={'type':'integer'},column={'type':'integer'},
 operation=enum('call','return','assignment','condition','attribute'),evidence_id=S,question_ids=arr(S),reason=S))
PLAN=obj(dict(claims=arr(deepcopy(CLAIM)),questions=arr(LINKED_QUESTION),coverage=arr(COVERAGE),
 explorations=arr(EXPLORATION),reasoning=S,termination_reason=S))
