"""Scope metadata is owned by its claim/condition, with no duplicate prose IDs.

The controller binds prior versions and checks declarations, not causal truth.
Original model output is never rewritten or filtered by this audit.
"""
from copy import deepcopy
from collections import Counter
from schemas import obj,arr,S,enum,CLAIM,CONDITION,REVIEW
from common import digest

STATUS=enum('supported','refuted','unresolved')
ROLES=('core_requirement','trigger_requirement','reachability_requirement','impact_only','context_only','historical')
ESSENTIAL=set(ROLES[:3])
STEP=obj(dict(proposition=S,status=STATUS,basis=enum('supplied_source','language_or_format_semantics','public_requirement','unresolved_assumption'),evidence_ids=arr(S)))
EXTENSION=obj(dict(statement=S,additional_requirement_condition_ids=arr(S),context_condition_ids=arr(S),status=STATUS,counterfactual_reason=S))
SCOPE=obj(dict(status=STATUS,scope_change=enum('retained','narrowed','refuted','deferred'),confirmed_operation=S,supported_consequence=S,defect_basis=S,evidence_ids=arr(S),proof_steps=arr(STEP),impact_extensions=arr(EXTENSION),necessity_check=S))

def with_claim_scopes(base):
 out=deepcopy(base);claim=out['properties']['claims']['items'];claim['properties']['causal_scope']=deepcopy(SCOPE);claim['required'].append('causal_scope')
 condition=claim['properties']['conditions']['items'];condition['properties'].update(scope_role=enum(*ROLES),necessity_reason=S);condition['required']+=['scope_role','necessity_reason']
 return out

POLICY='''SCOPED CAUSE CONTRACT v2:
Each final claim owns exactly one causal_scope object. Each condition owns its scope_role and necessity_reason. The ONLY canonical revised cause text is claims[].claim and its trigger is claims[].trigger. Actually revise those fields when narrowing; do not leave an overbroad cause there and describe the narrow cause only in a different field. Do not emit a separate claim_scopes array, duplicate original_claim/scoped_claim strings, or invented role IDs. The controller binds original supplied history through the existing candidate_decisions mapping and preserves the actual model words.
Classify each condition's necessity relative to this revised narrow cause: core_requirement (operation/contract/API behavior), trigger_requirement (admissible input/state), reachability_requirement (path needed for that cause), impact_only, context_only, or historical. Explain why falsifying the premise would or would not eliminate that narrow defect. A historical condition is not a current obligation; use the route history to preserve why it ceased to be necessary. Do not relabel a genuinely essential unknown as impact/context to permit reporting.
Build finite proof_steps and an admissible positive witness in claim.trigger/necessity_check from supplied source and actual public/type/format contracts. Production occurrence and impact magnitude are not automatically required. A hypothetical unsupported API premise is not a witness. Avoid an endless sequence of imagined unseen guards, but respect real missing validators/producers when necessary. An observed operation alone is not a defect: establish a wrong result, violated contract, or concrete new avoidable work. Broad deployment/security/performance effects remain unverified unless actually supported.
For an impact_extension, context_condition_ids may reference the SAME core conditions: sharing context is not a logical conflict. additional_requirement_condition_ids identify extra noncore premises needed only for that wider effect. If a premise is necessary for the narrow cause, its condition must have an essential role instead. Unknown nonessential impact does not refute an independently established core. The extension's uncertain consequences must not be asserted as facts in findings.
Declare causal_scope.status=supported only when every essential condition and every proof step is supported within the shown evidence/ordinary language or format semantics, with actual code evidence. If an essential fact is unresolved, keep the core unresolved. Withdraw only with affirmative refutation of an essential premise; failed reads are not refutation. Preserve current/historical condition IDs and reasons; never promote an unknown just to pass a gate. For every historical candidate, independently report/narrow/withhold/withdraw/merge using candidate_decisions. Do not silently replace one supported defect with another. No initial warning is automatically restored and no finding is synthesized by the controller. These are model semantic judgments; mechanical acceptance certifies only provenance and declared consistency.'''

def ledger_projection(payload):
 """Remove only new scope metadata; preserve all legacy values byte-for-byte."""
 out={k:deepcopy(v)for k,v in payload.items()if k in REVIEW['properties']}
 for claim in out.get('claims',[]):
  for key in list(claim):
   if key not in CLAIM['properties']:del claim[key]
  for condition in claim['conditions']:
   for key in list(condition):
    if key not in CONDITION['properties']:del condition[key]
 return out

def bindings(payload,inventory):
 out=[]
 for c in payload['claims']:
  origins=[]
  for oldid,d in payload['candidate_decisions'].items():
   if d['current_claim_id']==c['id']and oldid in inventory:
    origins.append(dict(candidate_id=oldid,versions=deepcopy(inventory[oldid]['versions'])))
  out.append(dict(claim_id=c['id'],current_claim=c['claim'],current_trigger=c['trigger'],scope=deepcopy(c.get('causal_scope')),prior_versions=origins,conditions=[dict(condition_id=p['id'],predicate=p['predicate'],status=p['status'],scope_role=p.get('scope_role'),necessity_reason=p.get('necessity_reason'))for p in c['conditions']],semantic_truth_guaranteed=False))
 return out

def audit(payload,evidence,initial_claims=()):
 errors=[];available={e['id']for e in evidence};narrowed=0
 def bad(code,**more):errors.append(dict(code=code,**more))
 ids=[c['id']for c in payload['claims']]
 if len(ids)!=len(set(ids)):bad('duplicate_claim_id')
 for c in payload['claims']:
  cid=c['id'];scope=c.get('causal_scope')
  if not isinstance(scope,dict):bad('missing_nested_causal_scope',claim_id=cid);continue
  narrowed+=scope['scope_change']=='narrowed';conditions={p['id']:p for p in c['conditions']};current=set(c['route_assignment']['current_condition_ids'])
  if len(conditions)!=len(c['conditions']):bad('duplicate_condition_id',claim_id=cid)
  if not current<=set(conditions):bad('unknown_current_condition',claim_id=cid)
  essential=[]
  for p in c['conditions']:
   role=p.get('scope_role')
   if role not in ROLES:bad('missing_or_invalid_scope_role',claim_id=cid,condition_id=p['id']);continue
   if not p.get('necessity_reason','').strip():bad('missing_necessity_reason',claim_id=cid,condition_id=p['id'])
   if (p['id']not in current)!=(role=='historical'):bad('historical_current_role_conflict',claim_id=cid,condition_id=p['id'])
   if role in ESSENTIAL:essential.append(p)
  eid=list(scope['evidence_ids'])
  for step in scope['proof_steps']:eid.extend(step['evidence_ids'])
  if not set(eid)<=available:bad('scope_evidence_not_delivered',claim_id=cid)
  if scope['status']=='supported':
   if not scope['evidence_ids']or not scope['proof_steps']or not scope['defect_basis'].strip()or not scope['necessity_check'].strip():bad('supported_scope_without_explicit_basis',claim_id=cid)
   if any(p['status']!='supported'for p in essential):bad('supported_scope_with_unknown_essential',claim_id=cid)
   if any(p['status']!='supported'for p in scope['proof_steps']):bad('supported_scope_with_unresolved_proof_step',claim_id=cid)
  if scope['status']=='refuted':
   if not scope['evidence_ids']or not(any(p['status']=='refuted'for p in essential)or any(s['status']=='refuted'for s in scope['proof_steps'])):bad('refuted_scope_without_declared_refutation',claim_id=cid)
  for ext in scope['impact_extensions']:
   required=ext['additional_requirement_condition_ids'];context=ext['context_condition_ids']
   if not set(required+context)<=set(conditions):bad('extension_condition_missing',claim_id=cid)
   if any(conditions.get(k,{}).get('scope_role')in ESSENTIAL for k in required):bad('core_condition_must_be_extension_context_not_additional_requirement',claim_id=cid)
   if not ext['counterfactual_reason'].strip():bad('extension_without_necessity_reason',claim_id=cid)
 return dict(accepted=not errors,errors=errors,scopes_recorded=len(payload['claims']),narrowed=narrowed,automatic_retention_or_repair=False,semantic_truth_guaranteed=False,scope='Object ownership, provenance and declared necessity consistency. No semantic equivalence inferred from text; no status/claim/finding repair.')
