"""Public-only policies. No PR-specific path, gold, or post-hoc contract."""
import json
from prompts48 import SYSTEM
from prompts_lsp48 import base_text, changed_operations, frontier_view, summarize_answer, final_prompt as old_final
from role_prompts import ROLE_POLICY
from budget_context import render_public,prompt_state,execution_view
from prompts_lsp48 import REPORT_POLICY
from claim_scope import POLICY as SCOPE_POLICY

BASE_SYSTEM = SYSTEM.replace(
    'API behavior is a hypothesis until actual implementation or a scoped observation supports it.',
    'Material third-party API behavior needs supplied implementation/contract evidence or a scoped observation; distinguish this from ordinary language/data-format reasoning and public normative requirements.')
QUERY_POLICY = '''
You can request actual static tools, and the controller will execute valid requests.
definition/references/implementation use the PR snapshot LSP and its actual capabilities. body reads a complete enclosing implementation at a known physical line. flow collects local def-use and limited argument/parameter/return mappings, not runtime paths. search performs a bounded literal symbol/field search within the SAME frozen repository source pool and returns candidate bodies/lines. For search use the exact literal symbol in symbol and an existing source path in file as a ranking hint; a search match is not a call edge. environment supplies recorded environment facts, not API behavior. No runtime probe or new internet source is available.
Select questions that can change a material causal judgment: actual callee behind a wrapper; producer of a consumed field; consumer of a changed return value; previous guard/exception branch; admissible input or event order. A reference edge is only a candidate. Trace both upstream prerequisites and downstream failure operations as needed. Do not assume every PR has a defect.
Separate failure mechanism, trigger admissibility, path reachability and API contract from deployment frequency/observation. Do not demand an observed incident to establish a source-supported conditional defect, and do not assume an unknown API contract merely by writing 'if'. State a concrete possible input/event sequence and identify which steps remain unsupported.
Preserve all claim/condition/challenge IDs and historical records, with explicit revisions. Every query binds to live condition_ids or challenge_ids; use unique new question IDs at the second round. For a new candidate-free exploration use a supplied changed-operation anchor, claim_id='exploration', its exploration_id and matching explorations entry. If candidates exist, extend them or create explicit new candidates, not detached seeds.
coverage may be empty: the controller derives query selection from explicit question links, and missing selections remain unresolved. Use reuse only when the required evidence kind is physically available. For a literal diff fact use literal_diff_fact with exact diff_facts rows; do not pretend a diff fragment is a complete function. Complete source bodies, references, model semantic judgments and actual execution observations are different things.
Use only actual delivered evidence IDs and exact source positions; no invented location. column=0 lets the backend locate one unambiguous symbol token. When a target position is not yet known, use bounded search, not a guessed path/line. Keep reasoning and bookkeeping concise; reserve budget for final review. Never treat missing/failed/withheld lookup as refutation.
'''

def initial_prompt(case,evidence):
    prompt=render_public(case,evidence)
    prompt+='\n\nChanged-operation candidates [id,snapshot,file,line,column,operation,evidence_id,symbol_hint]; lexical only, not execution proof:\n'+json.dumps([[o.get(k)for k in('id','snapshot','file','line','column','operation','evidence_id','symbol_hint')]for o in changed_operations(case,evidence)],ensure_ascii=False,separators=(',',':'))
    prompt+='\n\nCall 1 of 3. Produce an INITIAL REVIEW with provisional findings, claims and the first actual query plan together. Findings are hypotheses and may be empty. Unknown material premises should produce targeted queries. The next model call will receive the real query responses and can issue new follow-up queries. Return the combined INITIAL schema, including coverage/explorations/diff_facts; do not omit necessary condition history.'
    return dict(system=BASE_SYSTEM+QUERY_POLICY,prompt=prompt,shared='',suffix='')

def replan_prompt(case,evidence,initial,execution,frontier,remaining):
    prompt=render_public(case,evidence)
    prompt+='\n\nActual initial review and first questions:\n'+json.dumps({k:v for k,v in initial.items()if k not in('coverage','diff_facts','explorations')},ensure_ascii=False,separators=(',',':'))
    prompt+='\n\nACTUAL FIRST QUERY EXECUTION:\n'+json.dumps(execution_view(execution),ensure_ascii=False,separators=(',',':'))
    prompt+='\n\nController frontier and remaining budgets:\n'+json.dumps({'frontier':prompt_state(frontier),'remaining':remaining},ensure_ascii=False,separators=(',',':'))
    prompt+='''
Call 2 of 3: VERIFICATION AND RE-EXPLORATION, not final review. Whole-plan audit errors do not suppress independently admissible questions. Read the actual tool answers even if the plan audit failed; safe acquisition is not causal verification. Keep the audit errors and repair your bookkeeping explicitly. A diff_facts record is only for a literal_diff_fact condition, never a substitute for implementation/dataflow. Prefer empty coverage over inconsistent duplicate selections; explicit live question links are enough. For EVERY executed first question emit exactly one answer_assessments record: did the returned source answer the semantic question, contradict the premise, or leave it unresolved? Cite delivered IDs and explain what remains. Mechanical tool success is not semantic success.
Revise/refute initial API assumptions when the actual implementation contradicts them. If a wrapper, producer, guard, consumer, or contract is still missing, issue a NEW concrete question targeting it and link next_question_ids. Follow actual source names/locations, not speculation. Same unchanged cached request cannot provide new information; select body, a discovered callee, another consumer, references/flow, or search as appropriate. A previously collected body may be reread without new-query credit.
An empty first plan or failed first lookup does not forbid new questions. Fix structural errors explicitly while preserving the raw claim history. Never force a finding or manufacture supported conditions. If no productive query exists, state unresolved or sufficient evidence with reasons. Your follow-up requests will either be executed or explicitly withheld in matched experimental branches; you do not choose the branch. Return REPLAN JSON with answer_assessments and no findings.
'''
    prompt+='\n\nRecord claim_scopes now. For every initial candidate, explicitly decide whether a supported narrower defect survives when an uncertain stronger consequence is removed. If so, actually revise the current claim and its route before selecting follow-up questions. Preserve the exact original claim text in original_claim; no unsupported essential premise may be removed solely to emit a finding.'
    return dict(system=BASE_SYSTEM+QUERY_POLICY+SCOPE_POLICY,prompt=prompt,shared='',suffix='')

def final_prompt(case,evidence,initial,plan,execution,frontier,remaining,warning_state,first_execution,assessments,scopes=()):
    prompt=render_public(case,evidence)
    initial_summary={k:initial.get(k)for k in('assessment','findings','reasoning','termination_reason')}
    initial_summary['claim_statements']=[{k:c.get(k)for k in('id','claim','trigger')}for c in initial.get('claims',[])]
    prompt+='\n\nInitial actual report and claim statements (complete current/historical conditions follow in latest plan):\n'+json.dumps(initial_summary,ensure_ascii=False,separators=(',',':'))
    prompt+='\n\nComplete latest causal plan, conditions and history:\n'+json.dumps(plan,ensure_ascii=False,separators=(',',':'))
    prompt+='\n\nActual replan claim scopes and unconfirmed extensions (reassess using final supplied source):\n'+json.dumps(scopes,ensure_ascii=False,separators=(',',':'))
    prompt+='\n\nActual first tool outcomes:\n'+json.dumps(execution_view(first_execution),ensure_ascii=False,separators=(',',':'))
    prompt+='\n\nSemantic first-answer assessment and next-query reasons:\n'+json.dumps(assessments,ensure_ascii=False,separators=(',',':'))
    prompt+='\n\nActual follow-up tool outcomes:\n'+json.dumps(execution_view(execution),ensure_ascii=False,separators=(',',':'))
    prompt+='\n\nController outstanding obligations, remaining budget and warning tracking:\n'+json.dumps(dict(frontier=prompt_state(frontier),remaining=remaining,warning_state=warning_state),ensure_ascii=False,separators=(',',':'))
    prompt+='\n\nCall 3 of 3. Whole-plan audit and per-question safe acquisition are distinct. Preserve unresolved obligations and historical refutations, and use actual supplied bodies even when an unrelated metadata audit failed. Reconcile both rounds with ACTUALLY supplied source. Distinguish regression anchor, failure operation and supporting evidence. The budget diff view is partial: never claim complete PR coverage. All earlier visible diff handles stay available. Missing/withheld results remain unresolved, not refuted. No later tool round remains. Return the complete role-aware REVIEW; auxiliary audit never rewrites your raw report.'
    prompt+='\n\nReturn updated claim_scopes for EVERY claim. Where a stronger impact remains unknown, test the narrower supported defect and actually revise its wording, necessary conditions, report assessment and warning disposition consistently. Do not defer a demonstrated narrower defect solely because an excluded stronger consequence is unknown. Do not emit a core with an unresolved indispensable premise. Keep excluded consequences as explicit uncertainties, never as asserted impact. These are your semantic judgments, not controller-certified facts.'
    return dict(system=BASE_SYSTEM+REPORT_POLICY+ROLE_POLICY+SCOPE_POLICY,prompt=prompt,shared='',suffix='')
