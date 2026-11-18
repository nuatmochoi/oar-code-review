"""Compact metadata, unchanged full source; no fixture-answer policy branches."""
import json
from common import digest,sha,render_evidence
from prompts import SYSTEM as STATIC_SYSTEM
SYSTEM=STATIC_SYSTEM.replace('Runtime is unavailable in this static study; leave observation questions unresolved.','A supplied runtime_observation is only an analysis-profile subprocess observation. It cannot establish the original PR deployed runtime. Request a scoped observation only for unresolved predicates that supplied static evidence cannot decide. Never treat an observation record as implementation source or an operation location.')
SYSTEM+='\nAn executed predicate about a scoped analysis observation must use kind=runtime_observation and cite the actual observation. If changing an existing condition kind, meaning, or scope, explicitly revise it with the appropriate change_basis and reason, or add a separate scoped condition while leaving an unproved deployment predicate unresolved. A runtime-only citation cannot support/refute an implementation_body or exception_branch condition requiring complete source. Keep that source kind only when actual applicable implementation source also supports the judgment. Do not upgrade runtime metadata to source or a PR failure-operation location.'

def compact_binding(b):
    return {k:b[k]for k in ('status','scope','version','origin','binding_evidence','assumptions','runtime_dispatch_confirmed','deployed_runtime_confirmed','fixture_origin','plan_sha256','receipt_sha256')if k in b}

def visible_evidence(items):
    out=[]
    for old in items:
        b=dict(old);b['binding']=compact_binding(old.get('binding',{}))
        if b.get('kind')not in ('source_lines','implementation_body','exception_branch','body','diff_hunk','runtime_observation'):
            try:value=json.loads(b['content'])
            except (ValueError,TypeError):value={}
            # Whole metadata artifacts remain on disk. These typed facts do not
            # become source bodies; no line of actual source content is cut.
            if b.get('kind')=='environment_record':
                b['content']=json.dumps(dict(evidence_kind='environment_record',binding=b['binding'],full_metadata_sha256=digest(old['content']),deployed_runtime_proven=False),ensure_ascii=False)
            elif isinstance(value,dict):
                fields={k:value[k]for k in ('operation','location','relation','scope','caller','callee','arguments','parameter','expression','source','target','snapshot','resolution_mode')if k in value}
                b['content']=json.dumps(dict(facts=fields,full_metadata_sha256=digest(old['content']),not_execution_proof=True),ensure_ascii=False)
        out.append(b)
    return render_evidence(out)

def compact_tools(records):
    return [dict(round=r['round'],questions=[dict(question=q['question'],evidence=q['evidence'],unresolved=q['unresolved'],raw_reference=q['raw_reference'])for q in r['queries']],source_tokens=r['source_tokens'])for r in records]

def review_prompt(case,evidence,previous,controller,query_records,label,plan=None,observation_status=None):
    text='Public input:\n'+case['public_contract']+'\n\nProposed diff:\n'+case['diff']+'\n\nSupplied evidence:\n'+visible_evidence(evidence)
    text+='\n\nPrevious actual reviewer response (historical seed on first round; no semantic repair):\n'+json.dumps(previous,ensure_ascii=False)
    text+='\n\nMechanical controller feedback (not semantic proof):\n'+json.dumps(dict(state=controller.ledger.export(),last_update=controller.updates[-1]if controller.updates else None,rejections=controller.ledger.rejections),ensure_ascii=False)
    text+='\n\nCompact actual query outcomes; full raw artifacts remain hash referenced:\n'+json.dumps(compact_tools(query_records),ensure_ascii=False)
    if plan is not None:text+='\n\nShared proposed analysis probe plan (code is a generated fixture, not repository source):\n'+json.dumps(plan,ensure_ascii=False)
    if observation_status is not None:text+='\n\nObservation delivery status:\n'+json.dumps(observation_status,ensure_ascii=False)
    text+='\n\n'+('Check the previous API mechanism against supplied evidence, address its challenges, and request the next relevant consumer/path if that mechanism is refuted. Do not assume the alternative cause.'if label=='verify'else'Give the actual final findings and explicit candidate/condition judgments. A correct intermediate conclusion, withdrawn warning, or unresolved hypothesis is not an emitted corrected finding. Keep the actual failure source unresolved when it exists only in public prose.')
    return dict(system=SYSTEM,shared='',prompt=text,suffix='')

def planner_prompt(case,evidence,previous,query_records,unresolved):
    system='''Propose at most one tiny analysis-environment Python probe only for the supplied unresolved questions. Use only the public contract, code, and allowed import bindings. Do not assume an expected result or the correct defect cause. If supplied static evidence already answers the question, decline a probe. The probe is a generated analysis fixture, never original repository implementation or caller. No project import, network, files, eval, exec, open, or process creation. Standard-library imports must match allowed public imports; bind only the public APIs needed for the unresolved predicate. Set RESULT to a JSON-compatible value describing actual observations, not a prefilled conclusion. Use actual values and exceptions; no assertions that manufacture the desired observation. Runtime observations will be scoped only to the fixed analysis interpreter, not original PR deployment. Do not supply physical repository locations for generated fixture operations. Return the PLAN JSON only.'''
    public_imports=__import__('common').load(__import__('common').ROOT/'prepared/public_imports.json')
    text=json.dumps(dict(public_contract=case['public_contract'],diff=case['diff'],unresolved_questions=unresolved,allowed_public_imports=public_imports,previous=previous,query_outcomes=compact_tools(query_records)),ensure_ascii=False)+'\n\nSupplied code and evidence:\n'+visible_evidence(evidence)
    return dict(system=system,shared='',prompt=text,suffix='')
