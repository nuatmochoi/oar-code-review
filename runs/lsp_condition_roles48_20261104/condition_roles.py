"""Role-aware reporting declarations and read-only mechanical audits.

This controller checks IDs, partitioning, explicit decisions and literal public
requirement quotes. It does not certify a language rule, an admissible trigger,
API semantics, causal sufficiency or a correct defect. It never edits findings.
"""
import copy
from collections import Counter
from schemas import obj,arr,enum,S,I
from warning_schema import with_warning_dispositions

ROLES=('failure_mechanism','trigger_admissibility','path_reachability',
       'behavior_contract','requirement','deployment_observation','impact_magnitude')
NECESSITIES=('essential','scope_only','frequency_only')
ROLE=obj(dict(claim_id=S,condition_id=S,role=enum(*ROLES),
 necessity=enum(*NECESSITIES),basis=enum('supplied_source','public_requirement','language_or_format_semantics','unresolved_assumption'),
 evidence_ids=arr(S),public_requirement_quotes=arr(S),reason=S))
STATE=enum('supported','refuted','unresolved','not_required')
ASSESSMENT=obj(dict(claim_id=S,decision=enum('report','report_conditionally','defer','withdraw'),
 mechanism_status=enum('supported','refuted','unresolved'),trigger_admissibility=STATE,
 path_reachability=STATE,behavior_contract=STATE,
 deployment_observation=enum('observed','not_observed','unresolved','not_required'),
 finding_indices=arr(I),essential_unresolved_condition_ids=arr(S),
 nonblocking_unresolved_condition_ids=arr(S),scoped_trigger=S,reason=S))

def with_condition_roles(base,warning_ids=None,evidence_ids=None,question_ids=None):
 schema=with_warning_dispositions(base,warning_ids,evidence_ids,question_ids)
 schema['properties'].update(condition_roles=arr(copy.deepcopy(ROLE)),report_assessments=arr(copy.deepcopy(ASSESSMENT)))
 schema['required']+=['condition_roles','report_assessments']
 return schema

def audit(payload,evidence,public_contract,prior_claims=()):
 """Read-only diagnostics. A failure never selects a more favorable report."""
 errors=[];roles=payload.get('condition_roles',[]);assessments=payload.get('report_assessments',[])
 claims={c['id']:c for c in payload.get('claims',[])};available={e['id']for e in evidence}
 def bad(code,**fields):errors.append(dict(code=code,**fields))
 for c in prior_claims:
  if c['id']not in claims:bad('prior_claim_missing',claim_id=c['id'])
 expected={(cid,pid)for cid,c in claims.items()for pid in c['route_assignment']['current_condition_ids']}
 bycondition={(cid,p['id']):p for cid,c in claims.items()for p in c['conditions']}
 rolemap={};rows=[]
 for role in roles:
  key=role['claim_id'],role['condition_id']
  if key in rolemap:bad('duplicate_condition_role',claim_id=key[0],condition_id=key[1])
  rolemap[key]=role
  if key not in expected:bad('role_not_current_condition',claim_id=key[0],condition_id=key[1])
  if not role['reason'].strip():bad('role_without_reason',claim_id=key[0],condition_id=key[1])
  if role['role']in('failure_mechanism','trigger_admissibility','path_reachability','behavior_contract','requirement')and role['necessity']=='frequency_only':
   bad('material_role_mislabeled_frequency_only',claim_id=key[0],condition_id=key[1])
  for eid in role['evidence_ids']:
   if eid not in available:bad('role_evidence_not_delivered',claim_id=key[0],condition_id=key[1],evidence_id=eid)
  quotes=role['public_requirement_quotes']
  if role['basis']=='public_requirement'and not quotes:bad('public_requirement_without_quote',claim_id=key[0],condition_id=key[1])
  for quote in quotes:
   if not quote.strip()or len(quote)>512 or quote not in public_contract:bad('public_requirement_quote_not_literal',claim_id=key[0],condition_id=key[1])
  rows.append({'claim_id':key[0],'condition_id':key[1],'role':role['role'],'necessity':role['necessity'],
   'model_condition_status':bycondition.get(key,{}).get('status'),'basis':role['basis'],
   'public_quotes_mechanically_matched':all(bool(q.strip())and len(q)<=512 and q in public_contract for q in quotes),
   'semantic_truth_guaranteed':False})
 for cid,pid in sorted(expected-set(rolemap)):bad('current_condition_without_role',claim_id=cid,condition_id=pid)
 assessmentmap={};findings=payload.get('findings',[]);decisions=[]
 for assessment in assessments:
  cid=assessment['claim_id']
  if cid in assessmentmap:bad('duplicate_report_assessment',claim_id=cid)
  assessmentmap[cid]=assessment
  if cid not in claims:bad('assessment_claim_missing',claim_id=cid);continue
  unknown={pid for c,pid in expected if c==cid and bycondition.get((cid,pid),{}).get('status')=='unresolved'}
  blocking=assessment['essential_unresolved_condition_ids'];nonblocking=assessment['nonblocking_unresolved_condition_ids']
  if len(set(blocking))!=len(blocking)or len(set(nonblocking))!=len(nonblocking)or set(blocking)&set(nonblocking)or set(blocking)|set(nonblocking)!=unknown:
   bad('unresolved_partition_not_exact',claim_id=cid,actual_unresolved=sorted(unknown))
  for pid in nonblocking:
   if rolemap.get((cid,pid),{}).get('necessity')=='essential':bad('essential_unresolved_declared_nonblocking',claim_id=cid,condition_id=pid)
  for pid in blocking:
   if rolemap.get((cid,pid),{}).get('necessity')not in('essential',None):bad('nonessential_unresolved_declared_blocking',claim_id=cid,condition_id=pid)
  indices=assessment['finding_indices'];actual=[i for i,f in enumerate(findings)if f['claim_id']==cid]
  if len(set(indices))!=len(indices)or sorted(indices)!=actual:bad('assessment_finding_mapping_not_exact',claim_id=cid)
  emitted=bool(actual);decision=assessment['decision']
  if emitted!=(decision in('report','report_conditionally')):bad('assessment_emission_disagrees',claim_id=cid)
  if not assessment['reason'].strip():bad('assessment_without_reason',claim_id=cid)
  if emitted:
   if blocking:bad('reported_with_declared_essential_blocker',claim_id=cid)
   if assessment['mechanism_status']!='supported':bad('reported_without_supported_mechanism',claim_id=cid)
   for field in('trigger_admissibility','path_reachability','behavior_contract'):
    if assessment[field]not in('supported','not_required'):bad('reported_with_unresolved_material_assessment',claim_id=cid,field=field)
   if not assessment['scoped_trigger'].strip():bad('reported_without_scoped_trigger',claim_id=cid)
   if decision=='report_conditionally'and any(not findings[i]['trigger'].strip()for i in actual):bad('conditional_report_without_trigger',claim_id=cid)
  if decision=='withdraw'and claims[cid]['action']!='withdraw':bad('withdrawal_claim_disagrees',claim_id=cid)
  decisions.append({'claim_id':cid,'decision':decision,'emitted':emitted,'deployment_observation':assessment['deployment_observation'],
   'essential_unresolved_count':len(blocking),'nonblocking_unresolved_count':len(nonblocking),
   'semantic_truth_guaranteed':False})
 for cid in sorted(set(claims)-set(assessmentmap)):bad('claim_without_report_assessment',claim_id=cid)
 for i,finding in enumerate(findings):
  if finding['claim_id']not in claims:bad('finding_claim_missing',finding_index=i)
 return {'accepted':not errors,'errors':errors,'role_rows':rows,'decision_rows':decisions,
  'role_counts':dict(Counter(r['role']for r in rows)),'decision_counts':dict(Counter(r['decision']for r in decisions)),
  'unresolved_but_emitted_claims':sum(r['emitted']and r['nonblocking_unresolved_count']>0 for r in decisions),
  'deployment_unobserved_but_emitted_claims':sum(r['emitted']and r['deployment_observation']in('unresolved','not_observed')for r in decisions),
  'automatic_retention_or_repair':False,'semantic_truth_guaranteed':False,
  'scope':'Checks explicit model-declared roles, partitions, IDs, actual output mappings and literal public-input quotes only; no semantic certification or report rewriting.'}
