"""Explicit scoped cause versus stronger consequence, with mechanical auditing.

The model supplies all semantic judgments. The controller checks provenance,
IDs and internal consistency; it never creates, retains or rewrites findings.
"""
from copy import deepcopy
from collections import Counter
from schemas import obj,arr,S,enum

BOOL={'type':'boolean'}
STATUS=enum('supported','refuted','unresolved')
STEP=obj(dict(proposition=S,status=STATUS,basis=enum('supplied_source','language_or_format_semantics','public_requirement','unresolved_assumption'),evidence_ids=arr(S)))
EXTENSION=obj(dict(statement=S,condition_ids=arr(S),status=STATUS,essential_to_core=BOOL,counterfactual_reason=S))
SCOPE=obj(dict(claim_id=S,original_claim=S,scoped_claim=S,concrete_trigger=S,
    confirmed_operation=S,supported_consequence=S,defect_basis=S,
    status=STATUS,scope_change=enum('retained','narrowed','refuted','deferred'),
    evidence_ids=arr(S),essential_condition_ids=arr(S),proof_steps=arr(STEP),
    impact_extensions=arr(EXTENSION),necessity_check=S))

def with_claim_scopes(base):
    out=deepcopy(base);out['properties']['claim_scopes']=arr(deepcopy(SCOPE));out['required'].append('claim_scopes');return out

POLICY='''
SCOPED CAUSE VERIFICATION: perform this decomposition for every claim and record it in claim_scopes. This is an actual reasoning step, not a reason to retain every initial warning.
1. Separate the smallest concrete source-supported defect from stronger impact, exploitability, magnitude, frequency, and deployment statements. Record original_claim exactly as in the initial candidate, scoped_claim, an admissible concrete_trigger, confirmed_operation, supported_consequence, defect_basis and proof_steps with actual evidence. An operation is not automatically a defect: establish a wrong result, failed requirement/contract, or concrete newly introduced avoidable resource work. Preserve correct ordinary language/data-format reasoning. Do not invent a benign operation's harm to fill this record.
2. For each impact_extension ask the counterfactual: if this extension were false, would the scoped claim still describe a real defect? Record essential_to_core and why. If a genuinely indispensable API behavior, admissible trigger, path, invariant, or required contract is unknown, the core remains unresolved. A conditional 'if my unsupported premise holds' is not proof. Do not disguise a necessary input/validation premise as impact magnitude.
3. When only a stronger impact remains unverified, actually REVISE the claim and final wording to the supported core instead of deferring the whole old stronger claim. Keep original condition IDs, original statuses, refutations and history. Explicitly explain why a condition is no longer necessary for the revised core. Use the existing route history basis not_necessary_for_claim where appropriate; never mark an unresolved condition supported to permit reporting. Current condition_roles and report_assessments concern the revised core. Unknown extensions remain visibly unknown and must not be asserted in a finding's title/explanation/trigger/impact.
4. Qualitative newly introduced avoidable work can be established from the caller and guard/evaluation order without proving a large attack, absent global input limits, observed outage or measured throughput. Report only that demonstrated work and its actual trigger; do not claim denial of service or material amplification without evidence. Conversely, if the only alleged defect requires an unproved threshold, security policy or external API behavior, defer it. Narrowing is not permission to report every inefficiency.
5. Build a finite positive witness from supplied source and actual public/type/format contracts. Do not introduce an endless sequence of hypothetical unseen guards solely because any hidden code could exist. If a particular producer, constructor or validator is truly necessary to admit the witness, identify that specific premise and query it within the remaining budget. Treat a supplied positive input contract differently from absence of proof that no guard exists. Do not assume a hidden guard is absent, ignore a visible restriction, or import facts not supplied.
6. Emission is a semantic judgment about the scoped defect, not about the strongest initial consequence. If the core is supported and a stronger extension is nonessential/unresolved, use report/report_conditionally for the narrow explanation and disclose uncertainty; actual occurrence is not required. If no substantive supported core remains, defer or withdraw with reasons. Withdrawal requires actual refutation; failed lookup alone is not refutation. No finding may be synthesized by the controller, and no report is forced.
'''

def audit(payload,evidence,initial_claims=()):
    errors=[];scopes=payload.get('claim_scopes',[]);claims={c['id']:c for c in payload.get('claims',[])};original={c['id']:c['claim']for c in initial_claims};available={e['id']for e in evidence}
    counts=Counter(s['claim_id']for s in scopes)
    def bad(code,**fields):errors.append(dict(code=code,**fields))
    if set(counts)!=set(claims)or any(n!=1 for n in counts.values()):bad('scope_claim_partition_not_exact')
    for s in scopes:
        cid=s['claim_id'];claim=claims.get(cid)
        if claim is None:continue
        if cid in original and s['original_claim']!=original[cid]:bad('scope_original_claim_not_exact',claim_id=cid)
        if s['scoped_claim']!=claim['claim']:bad('scope_claim_text_disagrees',claim_id=cid)
        conditions={p['id']:p for p in claim['conditions']};essential=s['essential_condition_ids']
        current=set(claim['route_assignment']['current_condition_ids'])
        if len(set(essential))!=len(essential)or not set(essential)<=set(conditions)or not set(essential)<=current:bad('scope_unknown_or_duplicate_essential_condition',claim_id=cid)
        ids=list(s['evidence_ids'])
        for step in s['proof_steps']:ids.extend(step['evidence_ids'])
        if not set(ids)<=available:bad('scope_evidence_not_delivered',claim_id=cid)
        if s['status']=='supported':
            if not s['evidence_ids']or not s['proof_steps']or not s['defect_basis'].strip()or not s['necessity_check'].strip():bad('supported_scope_without_explicit_basis',claim_id=cid)
            if any(conditions.get(k,{}).get('status')!='supported'for k in essential):bad('supported_scope_with_unknown_essential',claim_id=cid)
            if any(p['status']!='supported'for p in s['proof_steps']):bad('supported_scope_with_unresolved_proof_step',claim_id=cid)
        for extension in s['impact_extensions']:
            if not set(extension['condition_ids'])<=set(conditions):bad('extension_condition_missing',claim_id=cid)
            if not extension['counterfactual_reason'].strip():bad('extension_without_necessity_reason',claim_id=cid)
            if s['status']=='supported'and extension['essential_to_core']and extension['status']!='supported':bad('supported_scope_hides_essential_extension',claim_id=cid)
            if not extension['essential_to_core']and set(extension['condition_ids'])&set(essential):bad('extension_and_core_necessity_conflict',claim_id=cid)
    return dict(accepted=not errors,errors=errors,scopes_recorded=len(scopes),narrowed=sum(s['scope_change']=='narrowed'for s in scopes),automatic_retention_or_repair=False,semantic_truth_guaranteed=False,scope='Provenance/IDs/internal declarations only; whether the scoped defect and necessity judgment are true is model assessed.')
