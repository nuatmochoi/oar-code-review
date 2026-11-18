"""Execute oar_core updates using explicit model judgments, never infer causes.

The adapter supplies change-record bookkeeping from explicit model reasons.
It does not choose support/refutation or silently repair a rejected judgment.
"""
import copy,dataclasses,json,re
import oar_core as oc
from common import digest,normalize_block,source_lines

def public_diff_evidence(diff):
    paths={};oldpath=newpath=None;base=head=0
    def put(path,snapshot,n,text):
        if path is not None:
            paths.setdefault(path,{'BASE':{},'HEAD':{}})[snapshot][n]=text
    for t in source_lines(diff):
        if t.startswith('--- '):
            oldpath=t[4:].split('\t')[0].removeprefix('a/');newpath=None
            if oldpath=='/dev/null':oldpath=None
        elif t.startswith('+++ '):
            newpath=t[4:].split('\t')[0].removeprefix('b/')
            if newpath=='/dev/null':newpath=None
        elif t.startswith('@@'):
            m=re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)',t)
            if m:base,head=map(int,m.groups())
        elif t.startswith(' ') and base and head:
            put(oldpath,'BASE',base,t[1:]);put(newpath,'HEAD',head,t[1:]);base+=1;head+=1
        elif t.startswith('-') and not t.startswith('---'):
            put(oldpath,'BASE',base,t[1:]);base+=1
        elif t.startswith('+') and not t.startswith('+++'):
            put(newpath,'HEAD',head,t[1:]);head+=1
    out=[]
    for path,sides in paths.items():
        for snapshot,rows in sides.items():
            if not rows:continue
            v=dict(snapshot=snapshot,path=path,start_line=min(rows),end_line=max(rows),
                kind='diff_hunk',provenance='public_diff',content=diff,
                physical_rows={str(k):v for k,v in rows.items()},mechanical_checks=dict(public_diff_rows=True,semantic_truth_guaranteed=False),binding={})
            v['id']='D-'+digest([snapshot,path,diff])[:16];out.append(v)
    return out

def core_evidence(b):
    kind=b.get('kind','source_lines')
    is_source=kind in ('source_lines','implementation_body','exception_branch','body')
    raw_provenance=b.get('provenance','read_lines')
    provenance=raw_provenance.get('provider','read_lines') if isinstance(raw_provenance,dict) else str(raw_provenance)
    if provenance not in oc.PROVENANCES:
        provenance='native_lsp' if 'lsp' in provenance.lower() else 'ast_fallback' if 'ast' in provenance.lower() else 'read_lines'
    ck='source_lines' if is_source else 'diff_hunk' if kind=='diff_hunk' else 'definition_location' if kind=='definition_location' else 'reference_edge' if kind=='reference_edge' else 'ast_outline'
    return oc.Evidence(id=b['id'],snapshot=b['snapshot'].lower(),file=b['path'],
        lines=tuple(enumerate(source_lines(b['content']),b['start_line'])) if is_source else (),
        text='' if is_source else b['content'],provenance=provenance,content_kind=ck,
        backend=json.dumps(raw_provenance,sort_keys=True) if isinstance(raw_provenance,dict) else raw_provenance,anchor_line=b['start_line'],runtime_validity='static_source')

class ClaimController:
    def __init__(self,evidence):
        self.evidence={};self.store=oc.EvidenceStore();self.ledger=oc.Ledger(self.store)
        self.updates=[];self.rounds=[]
        self.add_evidence(evidence)
    def add_evidence(self,items):
        store=copy.deepcopy(self.store);evidence=copy.deepcopy(self.evidence)
        for b in items:
            if b['id'] in evidence:assert evidence[b['id']]==b;continue
            store.add(core_evidence(b));evidence[b['id']]=copy.deepcopy(b)
        self.store=store;self.evidence=evidence;self.ledger.store=store
    def update(self,payload,round_index):
        mapped=[];errors=[]
        for c in payload['claims']:
            if not c['reason'].strip():errors.append(dict(code='missing_model_reason',claim=c['id']))
            old=self.ledger.candidates.get(c['id'])
            conditions=[]
            for p in c['conditions']:
                if not all(p.get(k,'').strip() for k in ('reason','effect_if_true','effect_if_false')):
                    errors.append(dict(code='missing_model_condition_reason_or_effect',claim=c['id'],condition=p['id']))
                if p['status'] in ('supported','refuted') and p['kind'] in ('implementation_body','exception_branch','dependency_version'):
                    supplied=[self.evidence[eid] for eid in p['evidence_ids'] if eid in self.evidence]
                    def can_carry(e):
                        source=e.get('kind') in ('source_lines','implementation_body','exception_branch','body')
                        if p['kind']=='dependency_version':return source or e.get('kind') in ('environment_record','dependency_version')
                        return source and e.get('mechanical_checks',{}).get('complete_unit') is True
                    if not any(can_carry(e) for e in supplied):
                        errors.append(dict(code='typed_evidence_cannot_carry_question',claim=c['id'],condition=p['id'],required=p['kind'],
                            scope='Body completeness and evidence kind only; semantic callee binding remains model-assessed.'))
                required='runtime' if p['kind']=='runtime_observation' else 'name' if p['kind'] in ('definition_location','dependency_version') else 'body' if p['kind'] in ('implementation_body','exception_branch') else 'value'
                conditions.append(dict(id=p['id'],predicate=p['predicate'],required=True,requires=required,
                    status={'supported':'supported','refuted':'contradicted','unresolved':'unknown'}[p['status']],
                    snapshot=p['snapshot'].lower(),evidence_ids=p['evidence_ids'],assessed_by='model'))
            challenges=[dict(id=x['id'],text=x['text'],state=x['state'],material=True,target=None,evidence_ids=x['evidence_ids']) for x in c['challenges']]
            item=dict(id=c['id'],claim=c['claim'],scope=c['trigger'],origin=c['reason'],
                origin_evidence_ids=list(dict.fromkeys(e for p in c['conditions'] for e in p['evidence_ids'])),
                obligations=conditions,challenges=challenges,routes=[dict(id='necessary',obligation_ids=[p['id'] for p in conditions])],changes=[])
            if c['action']=='withdraw':item['withdraw']=True
            if old is not None:
                # Use core's own structural diff to produce exact bookkeeping.
                # Every reason/basis/evidence still comes from the model output.
                merge_errors=[];new,_=self.ledger._merge(old,item,'adapter',merge_errors)
                if merge_errors:errors.extend(merge_errors)
                if new is not None:
                    for kind,target,a,b,accepted in self.ledger._diffs(old,new):
                        if c['change_basis']=='none':
                            errors.append(dict(code='state_change_without_model_basis',claim=c['id'],target=target,kind=kind))
                            continue
                        kind0=accepted[0];reason=c['reason'];ids=[]
                        pred=next((p for p in c['conditions'] if p['id']==target),None)
                        ch=next((p for p in c['challenges'] if p['id']==target),None)
                        if pred:reason=pred['reason'];ids=pred['evidence_ids']
                        elif ch:reason=ch['reason'];ids=ch['evidence_ids']
                        else:ids=item['origin_evidence_ids']
                        basis=c['change_basis']
                        if basis=='new_evidence':basis='new_premise'
                        rec=dict(kind=kind0,affected_ids=[target],reason=reason,evidence_ids=ids,basis=basis)
                        if kind0 in oc.EFFECT_KINDS:
                            rec.update(claim_effect_if_true=(pred or {}).get('effect_if_true',''),claim_effect_if_false=(pred or {}).get('effect_if_false',''))
                        item['changes'].append(rec)
            mapped.append(item)
        if errors:
            record=dict(accepted=False,errors=errors,state_sha256_before=self.ledger.state_hash(),state_sha256_after=self.ledger.state_hash(),transitions=[])
        else:
            r=self.ledger.apply_update(dict(candidates=mapped,evidence_refs=[]),round_index=round_index,provided_evidence_ids=self.evidence)
            record=dataclasses.asdict(r)
        record['semantic_assessor']='reviewer_model'
        record['mechanical_truth_scope']='IDs, snapshots, source-bearing kinds, reason-bearing structural changes; not causal or path truth'
        self.updates.append(record)
        self.rounds.append(dict(round=round_index,raw_claims=copy.deepcopy(payload['claims']),questions=copy.deepcopy(payload['questions']),update=record,state=self.ledger.export()))
        return record
    def export(self):
        return dict(state=self.ledger.export(),transitions=[t.to_dict() for t in self.ledger.transitions],rejections=self.ledger.rejections,updates=self.updates,rounds=self.rounds)
    def bind_locations(self,payload):
        errors=[];bindings=[];claim_diagnostics=[];unresolved=[]
        for i,f in enumerate(payload['findings']):
            cid=f.get('claim_id');candidate=self.ledger.candidates.get(cid)
            if candidate is None:claim_diagnostics.append(dict(finding=i,claim_id=cid,issue='unknown_claim_id'))
            else:
                if candidate.withdrawn:claim_diagnostics.append(dict(finding=i,claim_id=cid,issue='withdrawn_claim_reported'))
                if candidate.verdict()!='supported':claim_diagnostics.append(dict(finding=i,claim_id=cid,issue='ledger_not_supported',verdict=candidate.verdict()))
            if self.updates and not self.updates[-1]['accepted']:claim_diagnostics.append(dict(finding=i,claim_id=cid,issue='final_update_rejected'))
            roles=[('regression_anchor',f['regression_anchor']),('failure_operation',f['failure_operation'])]+[('supporting_evidence',x) for x in f['supporting_evidence']]
            for role,l in roles:
                if l.get('status')=='unresolved':
                    bad=bool(l.get('evidence_id') or l.get('path') or l.get('start_line')!=0 or l.get('end_line')!=0 or not l.get('reason','').strip())
                    v=dict(finding=i,role=role,requested=l,valid=False,errors=['invalid_unresolved_slot'] if bad else [],unresolved=True)
                    bindings.append(v);unresolved.append(v)
                    if bad:errors.append(v)
                    continue
                b=self.evidence.get(l['evidence_id']);issues=[]
                if b is None:issues.append('unknown_id')
                else:
                    if l['snapshot']!=b['snapshot']:issues.append('wrong_snapshot')
                    if l['path']!=b['path']:issues.append('wrong_path')
                    if l['start_line']<b['start_line'] or l['end_line']>b['end_line'] or l['end_line']<l['start_line']:issues.append('range_not_supplied')
                    elif b.get('kind')=='diff_hunk' and not all(str(n) in b['physical_rows'] for n in range(l['start_line'],l['end_line']+1)):issues.append('unsupplied_diff_gap')
                    if role=='regression_anchor' and l['snapshot']!='HEAD':issues.append('regression_anchor_not_HEAD')
                    if b.get('kind') not in ('source_lines','implementation_body','exception_branch','body','diff_hunk'):issues.append('location_is_not_operation_source')
                v=dict(finding=i,role=role,requested=l,valid=not issues,errors=issues)
                bindings.append(v)
                if issues:errors.append(v)
        return dict(bindings=bindings,errors=errors,unresolved=unresolved,claim_diagnostics=claim_diagnostics,semantic_relevance_assessed=False,findings_filtered=0,locations_repaired=0)
