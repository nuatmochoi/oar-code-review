"""Public diff seeds and explicit candidate birth records; no evaluator imports."""
import copy,re
from collections import Counter
from schemas import obj,arr,S,enum
from reexplore_schema import INITIAL as BASE_INITIAL,REPLAN as BASE_REPLAN
from prompts_lsp48 import changed_operations

BOOL={'type':'boolean'}
INTENT=obj(dict(question_id=S,needed_fact=enum('callee_behavior','producer','consumer','input_guard','exception_path','type_binding','api_contract','value_transfer','other'),focus_symbol=S,blocking=BOOL,discriminator=S,source_evidence_ids=arr(S)))
DISCOVERY=obj(dict(seed_id=S,decision=enum('new_candidate','covered_by_existing','no_substantive_suspicion','unresolved'),claim_ids=arr(S),distinct_from_claim_ids=arr(S),reason=S))
INITIAL=copy.deepcopy(BASE_INITIAL);INITIAL['properties']['retrieval_intents']=arr(INTENT);INITIAL['required'].append('retrieval_intents')
REPLAN=copy.deepcopy(BASE_REPLAN);REPLAN['properties'].update(retrieval_intents=arr(INTENT),discovery_assessments=arr(DISCOVERY));REPLAN['required']+=['retrieval_intents','discovery_assessments']

INTENT_POLICY='''For every requested question record exactly one retrieval_intents entry. State the missing fact, actual focus symbol if known, whether it blocks the causal judgment, the evidence that could distinguish support from refutation, and supplied evidence IDs. These are semantic planning judgments, not verified facts. Keep the existing question/condition/challenge schema and history. An empty question list requires an empty intent list.'''
DISCOVERY_POLICY='''Perform a separate NEW-CANDIDATE SEARCH after updating the initial hypotheses. Inspect the supplied public-diff operation seeds and new source for an independently plausible regression that is not already represented by an initial claim. Return discovery_assessments for the investigated seeds, explicitly linking each proposed new candidate to a new claim ID in claims. Explain why it is distinct from existing candidates, identify a concrete source-visible risk/contract and the indispensable unanswered premise, then request an admissible next query. No new candidate is a valid outcome. Do not invent a bug, manufacture an API contract or label ordinary behavior defective to fill a slot. Preserve all initial claims/history even when refuted. For a new claim, claim_scopes.original_claim is its exact birth claim text. Record a scoped current claim independently of uncertain stronger impact. Seeds are lexical candidates, never AST/runtime/causal proof. Do not rebrand the same root cause under a new ID.'''

def seed_catalog(case,evidence):
    checks={'call':['selected_callee_contract','argument_to_parameter','return_to_consumer'], 'return':['returned_value_consumer','return_contract'], 'assignment':['value_producer','next_value_consumer'], 'condition':['guard_prerequisite','branch_effect'], 'attribute':['field_producer','attribute_contract']}
    return [{**q,'checks':checks[q['operation']],'mechanical_scope':'Exact changed public-diff token candidate; not AST binding, dataflow, defect or execution proof.'}for q in changed_operations(case,evidence,limit=96)]

def compact_seeds(seeds):
    return [[s.get(k)for k in('id','snapshot','file','line','column','operation','evidence_id','symbol_hint','checks')]for s in seeds]

def audit(payload,evidence,initial,seeds=()):
    claims={c['id']:c for c in payload.get('claims',[])};original={c['id']:c for c in initial.get('claims',[])};available={e['id']for e in evidence};questions={q['id']:q for q in payload.get('questions',[])};counts=Counter(x['question_id']for x in payload.get('retrieval_intents',[]));errors=[]
    if set(counts)!=set(questions)or any(v!=1 for v in counts.values()):errors.append({'code':'intent_question_partition_not_exact'})
    for x in payload.get('retrieval_intents',[]):
        if not set(x['source_evidence_ids'])<=available:errors.append({'code':'intent_source_not_delivered','question_id':x['question_id']})
    known={s['id']for s in seeds};births={cid:c for cid,c in claims.items()if cid not in original};linked=set()
    for d in payload.get('discovery_assessments',[]):
        if d['seed_id']not in known:errors.append({'code':'unknown_public_seed','seed_id':d['seed_id']})
        if not set(d['claim_ids'])<=set(claims):errors.append({'code':'discovery_claim_not_present'})
        if not set(d['distinct_from_claim_ids'])<=set(original):errors.append({'code':'unknown_prior_claim'})
        if d['decision']=='new_candidate':
            for cid in d['claim_ids']:
                if cid in original:errors.append({'code':'old_claim_not_new_candidate','claim_id':cid})
                else:linked.add(cid)
    for cid in births:
        if cid not in linked:errors.append({'code':'new_claim_without_seed_assessment','claim_id':cid})
    return dict(accepted=not errors,errors=errors,new_claim_ids=sorted(births),birth_claims=copy.deepcopy(births),initial_claim_ids=sorted(original),semantic_distinctness_guaranteed=False)

def intent_for(q,intents):
    found=[i for i in intents if i['question_id']==q.get('id')]
    if len(found)==1:return found[0]
    kind=q.get('kind');return dict(question_id=q.get('id'),needed_fact='value_transfer'if kind=='value_path'else'exception_path'if kind=='exception_branch'else'callee_behavior',focus_symbol=q.get('symbol',''),blocking=True,discriminator=q.get('reason',''),source_evidence_ids=[])
