"""Full-visible-source planner/final inputs with condition-bound evidence answers."""
import copy,json,re
from common import render_evidence,digest
from prompts48 import SYSTEM

PLANNER_SYSTEM=SYSTEM+'''
This call produces a finding-free verification PLAN, not a final review. Keep every prior claim/condition/challenge ID, predicate and explicit reason unless revising it with a recorded basis. Partition current necessary conditions and historical conditions explicitly. The pending frontier preserves raw candidates even when an earlier mechanical ledger update was atomically rejected; that preservation is not causal confirmation.
The original report may contain unknown evidence IDs; those are errors, not supplied evidence. Keep its condition IDs while explicitly correcting invalid citations with a reason. Only the actual evidence catalog in this input establishes current visibility.
Cover each current necessary condition and pending material challenge exactly once in coverage, choosing query, reuse or defer. Every query must name its live claim and condition_ids/challenge_ids. Its kind must match the missing fact. Existing source IDs should be selected with reuse when they already carry the fact; do not repeat an identical native lookup just to obtain the same body again. A definition location or complete statement is not a complete function implementation. A cached answer will return its selected existing IDs without issuing another RPC. If you already know a definition's actual path/line, query=body can read its complete enclosing implementation.
If there were initial candidates, do not abandon them and fall back to generic changed-call seeds. You may add new explicit candidates, preserve all historical records, narrow/withdraw a disproven claim with reasons, or defer a question you cannot ground. When no candidates exist, you may explicitly select one of the supplied changed-operation anchors for exploration; these are lexical candidates, not executed paths or AST-proven operations. Do not assume every PR contains a bug.
Ask the smallest discriminating implementation/consumer/value-path question. When existing implementation evidence refutes an API assumption, revise the claim and ask about the next concrete consumer if still unresolved. An answer that requires runtime verification must remain unresolved here. Do not invent locations or claim execution evidence. Plan length must stay within 4,000 reference tokens; preserve necessary reasons rather than silently deleting obligations. Return only PLAN JSON. No findings field is allowed.'''

def changed_operations(case,evidence,limit=96):
 """Public changed physical rows only. A lexical inventory, never issued seeds."""
 handles={(e['snapshot'],e['path']):e for e in evidence if e.get('kind')=='diff_hunk'}
 old=new=None;a=b=0;out=[];seen=set()
 def add(snapshot,path,line,text):
  e=handles.get((snapshot,path))
  if not e or str(line)not in e.get('physical_rows',{}):return
  kinds=[]
  if re.search(r'\breturn\b',text):kinds.append(('return',re.search(r'\breturn\b',text).start(),''))
  guard=re.search(r'\b(if|elif|while|switch|case)\b',text)
  if guard:kinds.append(('condition',guard.start(),guard.group()))
  for m in re.finditer(r'\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(',text):
   if m.group(1)not in('if','while','for','switch','func')and not re.search(r'\b(def|class|func)\s*$',text[:m.start()]):kinds.append(('call',m.start(),m.group(1)))
  ass=re.search(r'(?<![=!<>])=(?!=)',text)
  if ass:kinds.append(('assignment',ass.start(),''))
  for kind,column,symbol in kinds:
   key=snapshot,path,line,column,kind
   if key in seen:continue
   seen.add(key);v=dict(snapshot=snapshot,file=path,line=line,column=column,operation=kind,evidence_id=e['id'])
   v['id']='OP-'+digest(v)[:12];v.update(symbol_hint=symbol,origin='public_diff_lexical_candidate_not_AST_or_runtime_confirmation');out.append(v)
 for row in case['diff'].splitlines():
  if row.startswith('--- '):old=row[4:].split('\t')[0].removeprefix('a/');old=None if old=='/dev/null'else old
  elif row.startswith('+++ '):new=row[4:].split('\t')[0].removeprefix('b/');new=None if new=='/dev/null'else new
  elif row.startswith('@@'):
   m=re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)',row)
   if m:a,b=map(int,m.groups())
  elif row.startswith('-')and not row.startswith('---'):add('BASE',old,a,row[1:]);a+=1
  elif row.startswith('+')and not row.startswith('+++'):add('HEAD',new,b,row[1:]);b+=1
  elif row.startswith(' '):a+=1;b+=1
 # Round-robin files/snapshots avoids flooding a finite metadata inventory.
 groups={}
 for op in sorted(out,key=lambda x:(x['snapshot']!='HEAD',x['file'],x['line'],x['column'])):groups.setdefault((op['snapshot'],op['file']),[]).append(op)
 chosen=[]
 while any(groups.values())and len(chosen)<limit:
  for values in groups.values():
   if values and len(chosen)<limit:chosen.append(values.pop(0))
 return chosen

def summarize_answer(answer):
 """Preserve existing selections as well as additions; never repeat raw bodies."""
 return {k:copy.deepcopy(answer[k])for k in ('question','question_binding','execution','cache_hit','selected_evidence_ids','already_available_evidence_ids','cached_original_new_evidence_ids','all_answer_evidence_ids','mechanically_compatible_evidence_ids','unresolved','cost','source_tokens_after','original_question','original_query_receipt')if k in answer}

def feedback(frontier):
 ctl=frontier.controller
 return dict(latest_update=copy.deepcopy(ctl.updates[-1])if ctl.updates else None,
  current_frontier=frontier.export(),semantic_truth_guaranteed=False)

def frontier_view(value):
 # Raw claims remain complete in initial_payload/PLAN immediately above. Their
 # duplicate serialization is omitted, never their fields or revision reasons.
 return {**{k:copy.deepcopy(v)for k,v in value.items()if k!='raw_claims'},
  'raw_claims_location':'Complete initial review / complete current PLAN in this same input.'}

def base_text(case,evidence):
 scope=case.get('planning_view')
 label='Full public BASE/HEAD diff (exact frozen text; once)'if not scope else'Planning-only complete file diff sections (other files explicitly omitted in this call)'
 text='Original public PR input:\n'+case['public_contract']+'\n\n'+label+':\n'+case['diff']
 if scope:text+='\n\nExplicit planning input scope (the final reviewer receives the full original diff):\n'+json.dumps(scope,ensure_ascii=False,separators=(',',':'))
 return text+'\n\nActual supplied source and physical DIFF citation handles:\n'+render_evidence(evidence)

def planning_prompt(case,evidence,initial_payload,frontier,cached_summaries):
 text=base_text(case,evidence)
 text+='\n\nActual initial final review (complete fields, including reasons):\n'+json.dumps(initial_payload,ensure_ascii=False,separators=(',',':'))
 text+='\n\nController pending frontier and structural/typed limitations:\n'+json.dumps(frontier_view(frontier.export()),ensure_ascii=False,separators=(',',':'))
 text+='\n\nAlready executed native lookup answers (existing selections are available):\n'+json.dumps(cached_summaries,ensure_ascii=False,separators=(',',':'))
 text+='\n\nReturn a finding-free PLAN with explicit coverage of every current condition/challenge. No automatic query is issued for an empty plan. Preserve uncertainty.'
 return dict(system=PLANNER_SYSTEM,prompt=text,shared='',suffix='')

def final_prompt(case,evidence,initial_payload,plan,execution,frontier_state,remaining):
 text=base_text(case,evidence)
 text+='\n\nActual initial final review (complete fields):\n'+json.dumps(initial_payload,ensure_ascii=False,separators=(',',':'))
 text+='\n\nActual verification plan (complete reasons, never a final finding):\n'+json.dumps(plan,ensure_ascii=False,separators=(',',':'))
 summary={k:copy.deepcopy(execution.get(k))for k in('accepted','validation','controller_update','coverage','new_evidence_ids','selected_evidence_ids','backend_query_attempts','cache_hits','semantic_truth_guaranteed')}
 summary['answers']=[summarize_answer(x)for x in execution.get('answers',[])]
 text+='\n\nActual tool/reuse/defer outcomes and question-to-evidence bindings:\n'+json.dumps(summary,ensure_ascii=False,separators=(',',':'))
 text+='\n\nCurrent controller state and measured remaining budgets:\n'+json.dumps(dict(frontier=frontier_view(frontier_state),remaining=remaining),ensure_ascii=False,separators=(',',':'))
 text+='\n\nProduce the final REVIEW JSON. Correct or withdraw a disproven cause with explicit reasons and preserve unresolved necessary conditions. Distinguish a collected body, mechanically compatible body, semantic support and actual runtime observation. Never turn a withheld/failed query or missing evidence into support. Every final citation must bind to an ID supplied in this input.'
 return dict(system=SYSTEM,prompt=text,shared='',suffix='')
