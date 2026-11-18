"""No ranking/deletion: fixed upstream source and complete candidate history."""
import copy
from schemas import obj,arr,S,enum

TASK='Give the terminal review. No further tool round remains. Report only concrete supported narrow defects, retaining explicitly bounded triggers and marking uncertain broader impact separately. Reflect actual support/refutation/unresolved evidence. Preserve reasons for candidate withdrawal or withholding; never call deletion/no-warning a successful correction. Do not replace one supported distinct defect just because another is ranked higher. Return up to6 independent findings; zero is valid.'
POLICY='''Before assembling findings, make an independent decision for EVERY supplied historical candidate ID. The inventory is a history checklist, NOT a list of known bugs and not a filter on new candidates. Do not union initial warnings blindly. A disproven necessary premise permits withdrawal; a failed lookup or uncertain wider impact does not refute a separately proven narrow defect. Separate proven_core, trigger, and unverified_expansion. Use supported/refuted/unresolved only within actual evidence scope. Reassess narrowed scope and report it if the narrow defect is independently established. If two candidates are the same cause, explicitly merge them into a retained candidate; never silently replace one independent correct cause with another. Unresolved essentials may justify withholding, with the exact missing fact. Select final finding indices only for actual findings belonging to the chosen current claim; controller checks identity/availability, not truth. Keep initial IDs in decision entries even if the final current claim has a revised ID. New independent causes remain allowed and the complete original source is retained.''' 

def inventory(shared,extra,history):
    values={}
    for stage,payload in [('initial',shared.get('initial')),('replan2',shared.get('replan')),('replan3',extra.get('payload')),('latest_plan',history.get('latest_plan'))]:
        for c in(payload or{}).get('claims',[]):
            values.setdefault(c['id'],dict(id=c['id'],versions=[]))['versions'].append(dict(stage=stage,claim=copy.deepcopy(c)))
    return values

def augmented(schema,candidates):
    out=copy.deepcopy(schema)
    decision=obj(dict(disposition=enum('report','revise_and_report','withdraw','merge','withhold'),current_claim_id=S,proven_core=S,trigger=S,unverified_expansion=S,core_status=enum('supported','refuted','unresolved'),evidence_ids=arr(S),final_finding_indices=arr({'type':'integer','minimum':0,'maximum':5}),merged_into=S,reason=S))
    out['properties']['candidate_decisions']=obj({k:copy.deepcopy(decision)for k in candidates});out['required'].append('candidate_decisions');return out

def audit(payload,candidates,evidence):
    decisions=payload['candidate_decisions'];assert set(decisions)==set(candidates)
    es={e['id']for e in evidence};claims={c['id']:c for c in payload['claims']};errors=[]
    for cid,d in decisions.items():
        for e in d['evidence_ids']:
            if e not in es:errors.append(dict(candidate=cid,code='evidence_not_delivered',evidence_id=e))
        for i in d['final_finding_indices']:
            if i>=len(payload['findings'])or payload['findings'][i]['claim_id']!=d['current_claim_id']:errors.append(dict(candidate=cid,code='finding_identity_mismatch',index=i))
        if d['disposition']in('report','revise_and_report')and not d['final_finding_indices']:errors.append(dict(candidate=cid,code='report_without_finding'))
        if d['disposition']not in('report','revise_and_report')and d['final_finding_indices']:errors.append(dict(candidate=cid,code='unreported_disposition_with_finding'))
        if d['disposition']in('report','revise_and_report')and d['current_claim_id']not in claims:errors.append(dict(candidate=cid,code='current_claim_missing'))
        if d['disposition']=='merge'and(not d['merged_into']or d['merged_into']==cid or d['merged_into']not in claims):errors.append(dict(candidate=cid,code='invalid_merge_target'))
    return dict(accepted=not errors,errors=errors,all_historical_candidates_have_decision=True,findings_preserved_regardless_of_auxiliary_acceptance=True,semantic_truth_guaranteed=False)
