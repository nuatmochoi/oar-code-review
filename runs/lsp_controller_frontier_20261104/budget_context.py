"""Navigable, exact hunk views; original diff and all source remain immutable."""
import re,json,hashlib
from functools import lru_cache
from common import digest,render_evidence,compact_binding
from diff48 import diff_evidence
from oar_core import count_reference_tokens as tokens

DIFF_LIMIT=6000
INDEX_LIMIT=1800

@lru_cache(maxsize=128)
def diff_view(original,public_paths=()):
    """Select WHOLE hunks, never truncate a hunk, line, function or source ID."""
    sections=re.split(r'(?=^diff --git )',original,flags=re.M)
    groups=[];inventory=[]
    for section in sections:
        if not section.strip():continue
        handles=diff_evidence(section)
        if not handles:continue
        path=next((e['path']for e in handles if e['snapshot']=='HEAD'),handles[0]['path'])
        starts=list(re.finditer(r'^@@ .*$',section,flags=re.M))
        header=section[:starts[0].start()]if starts else section
        hunks=[section[m.start():starts[i+1].start()if i+1<len(starts)else len(section)]for i,m in enumerate(starts)]
        inventory.append(dict(path=path,hunks=len(hunks),sha256=hashlib.sha256(section.encode()).hexdigest(),section_reference_tokens=tokens(section)))
        groups.append(dict(path=path,header=header,hunks=hunks,selected=[]))
    preferred=set(public_paths)
    groups.sort(key=lambda g:(g['path']not in preferred,not g['path'].endswith(('.py','.go')),g['path']))
    text='';selected=[]
    # Round-robin file coverage, then more hunks; skip any indivisible unit
    # that does not fit and record its absence rather than silently clipping.
    for i in range(max((len(g['hunks'])for g in groups),default=0)):
        for g in groups:
            if i>=len(g['hunks']):continue
            unit=(g['header']if not g['selected']else'')+g['hunks'][i]
            if tokens(text+unit)>DIFF_LIMIT-64:continue
            g['selected'].append(i);text+=unit;selected.append((g['path'],i))
    # Reconstruct per-file ordering so separated hunks always have a header.
    text=''.join(g['header']+''.join(g['hunks'][i]for i in g['selected'])for g in groups if g['selected'])
    assert tokens(text)<=DIFF_LIMIT
    index=[]
    for row in inventory:
        if tokens(json.dumps(index+[row],ensure_ascii=False,separators=(',',':')))>INDEX_LIMIT:break
        index.append(row)
    scope=dict(mode='whole_hunk_budget_view',full_original_sha256=hashlib.sha256(original.encode()).hexdigest(),full_original_reference_tokens=tokens(original),display_reference_tokens=tokens(text),selected_hunks=len(selected),total_hunks=sum(len(g['hunks'])for g in groups),source_files=len(inventory),displayed_index_files=len(index),index_complete=len(index)==len(inventory),selected_paths=sorted({p for p,_ in selected}),no_selected_hunk_or_source_line_truncated=True,source_coverage_is_not_complete=len(selected)==sum(len(g['hunks'])for g in groups))
    scope['source_coverage_is_not_complete']=not scope['source_coverage_is_not_complete']
    return text,diff_evidence(text),index,scope

def public_view(case,source_evidence):
    sources=[e for e in source_evidence if e.get('kind')!='diff_hunk']
    preferred=()
    text,handles,index,scope=diff_view(case['diff'],preferred)
    return {**case,'diff':text,'budget_view':scope,'diff_inventory':index},sources+handles

def render_public(case,evidence):
    text='Original public PR input:\n'+case['public_contract']
    text+='\n\nDiff scope (unselected original content remains in the controller source store, not read by this model):\n'+json.dumps(case['budget_view'],ensure_ascii=False,separators=(',',':'))
    text+='\n\nBounded file inventory (hashes identify source, not proof of behavior):\n'+json.dumps(case['diff_inventory'],ensure_ascii=False,separators=(',',':'))
    text+='\n\nExact selected COMPLETE diff hunks:\n'+case['diff']
    handles=[e for e in evidence if e.get('kind')=='diff_hunk']
    text+='\n\nDiff handle rows [ID,snapshot,path,start,end]; only physical rows present in the selected hunks are visible, not all rows in enclosing ranges:\n'+json.dumps([[e[k]for k in('id','snapshot','path','start_line','end_line')]for e in handles],ensure_ascii=False,separators=(',',':'))
    source=[e for e in evidence if e.get('kind')!='diff_hunk']
    bindings={};table=[];bodies=[]
    for e in source:
        binding=compact_binding(e.get('binding',{}));bid='B-'+digest(binding)[:8]
        bindings[bid]=binding
        table.append([e['id'],e['snapshot'],e['path'],e['start_line'],e['end_line'],e.get('kind'),e.get('mechanical_checks',{}).get('complete_unit',False),bid])
        bodies.append('<body id='+json.dumps(e['id'])+'>\n'+e.get('content','')+'\n</body>')
    text+='\n\nSource catalog rows [ID,snapshot,path,start_line,end_line,kind,complete_unit,binding_ID]. Each body below is exact original text; its first source line is start_line and subsequent lines advance by one. Source ranges and raw row maps are controller-bound; no runtime execution is implied.\n'+json.dumps(table,ensure_ascii=False,separators=(',',':'))
    text+='\n\nShared source binding records:\n'+json.dumps(bindings,ensure_ascii=False,separators=(',',':'))
    text+='\n\nExact complete source units and metadata (each body once):\n'+'\n\n'.join(bodies)
    return text

def prompt_state(frontier):
    """All current obligations, without reserializing full claims/old ledgers."""
    x=frontier.export()if hasattr(frontier,'export')else frontier
    result={k:x.get(k)for k in('structural_errors','implementation_shape_warnings','unselected','selection_incomplete','scope')if k in x}
    result['uncommitted_prior_raw_claims']=x.get('uncommitted_prior_raw_claims',[])
    result['pending_targets']=[{k:t.get(k)for k in('claim_id','target_kind','target_id','kind','snapshot','model_status')}for t in x.get('pending_targets',[])]
    return result

def execution_view(execution):
    answers=[]
    for a in execution.get('answers',[]):
        answers.append({k:a.get(k)for k in('question','execution','cache_hit','all_answer_evidence_ids','mechanically_compatible_evidence_ids','callee_binding','unresolved','cost')})
    return dict(accepted=execution.get('accepted'),complete=execution.get('complete'),whole_plan_audit_accepted=(execution.get('validation')or{}).get('accepted'),question_admissions=execution.get('query_admission',[]),errors=(execution.get('validation')or{}).get('errors',[]),answers=answers,new_evidence_ids=execution.get('new_evidence_ids',[]),semantic_truth_guaranteed=False)
