"""One reviewer/report schema shared by all four static conditions."""
def obj(properties):return dict(type='object',additionalProperties=False,properties=properties,required=list(properties))
def arr(items):return dict(type='array',items=items)
S={'type':'string'}
I={'type':'integer'}
def enum(*values):return dict(type='string',enum=list(values))
LOCATION=obj(dict(status=enum('bound','unresolved'),evidence_id=S,snapshot=enum('HEAD','BASE'),path=S,start_line=I,end_line=I,reason=S))
QUESTION=obj(dict(id=S,claim_id=S,kind=enum('definition_location','implementation_body','value_path','exception_branch','dependency_version','runtime_observation'),
    query=enum('definition','references','implementation','body','flow','environment'),snapshot=enum('BASE','HEAD'),file=S,line=I,column=I,symbol=S,reason=S))
CONDITION=obj(dict(id=S,predicate=S,kind=enum('definition_location','implementation_body','value_path','exception_branch','dependency_version','runtime_observation'),
    snapshot=enum('HEAD','BASE'),status=enum('supported','refuted','unresolved'),evidence_ids=arr(S),reason=S,effect_if_true=S,effect_if_false=S))
CHALLENGE=obj(dict(id=S,text=S,state=enum('pending','resolved','rejected'),evidence_ids=arr(S),reason=S))
HISTORICAL_ROLE=obj(dict(condition_id=S,reason=S,
    basis=enum('not_necessary_for_claim','interpretation_error','scope_revised'),evidence_ids=arr(S)))
ROUTE_ASSIGNMENT=obj(dict(current_condition_ids=arr(S),historical=arr(HISTORICAL_ROLE)))
CLAIM=obj(dict(id=S,claim=S,trigger=S,action=enum('new','retain','revise','withdraw'),
    change_basis=enum('new_evidence','interpretation_error','scope_revised','refuted_in_scope','duplicate','none'),
    reason=S,conditions=arr(CONDITION),challenges=arr(CHALLENGE),route_assignment=ROUTE_ASSIGNMENT,
    termination=enum('sufficient_evidence','refuted','unresolved','budget_exhausted','continue')))
FINDING=obj(dict(claim_id=S,title=S,explanation=S,trigger=S,impact=S,
    regression_anchor=LOCATION,failure_operation=LOCATION,supporting_evidence=arr(LOCATION),
    suggested_fix_target=S,uncertainties=arr(S)))
REVIEW=obj(dict(assessment=enum('issue','no_issue','uncertain'),findings=arr(FINDING),claims=arr(CLAIM),questions=arr(QUESTION),reasoning=S,termination_reason=S))
