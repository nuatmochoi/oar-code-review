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
    def __init__(self,evidence,mapping_mode='legacy'):
        if mapping_mode=='separated':mapping_mode='active'
        if mapping_mode not in ('legacy','active'):raise ValueError('mapping_mode must be legacy or active')
        self.mapping_mode=mapping_mode
        self.evidence={};self.store=oc.EvidenceStore();self.ledger=oc.Ledger(self.store)
        self.updates=[];self.rounds=[]
        self.declared_route_assignments={};self.condition_history={};self.role_history=[]
        self.add_evidence(evidence)
    def add_evidence(self,items):
        store=copy.deepcopy(self.store);evidence=copy.deepcopy(self.evidence)
        for b in items:
            if b['id'] in evidence:assert evidence[b['id']]==b;continue
            store.add(core_evidence(b));evidence[b['id']]=copy.deepcopy(b)
        self.store=store;self.evidence=evidence;self.ledger.store=store
    def _assignment(self,c,old,errors):
        """Common declarative guards. Necessity is never inferred from status."""
        cid=c['id'];conditions=c['conditions'];ids=[p['id']for p in conditions]
        a=c.get('route_assignment')
        def bad(code,**more):errors.append(dict(code=code,claim=cid,**more))
        if len(set(ids))!=len(ids):bad('duplicate_condition_id')
        if old is not None:
            missing=sorted(set(old.obligations)-set(ids))
            if missing:bad('preserved_condition_omitted',condition_ids=missing)
        if not isinstance(a,dict)or set(a)!= {'current_condition_ids','historical'}:
            bad('missing_or_invalid_route_assignment');return [],{}
        current=a['current_condition_ids'];history=a['historical']
        if not isinstance(current,list)or not all(isinstance(v,str)and v for v in current)or not isinstance(history,list):
            bad('invalid_route_assignment_types');return [],{}
        historical={};historical_ids=[]
        byid={p['id']:p for p in conditions}
        for entry in history:
            if not isinstance(entry,dict)or set(entry)!= {'condition_id','reason','basis','evidence_ids'}:
                bad('invalid_historical_role');continue
            pid=entry['condition_id']
            if not isinstance(pid,str)or not pid:bad('invalid_historical_condition_id');continue
            historical_ids.append(pid)
            historical[pid]=entry
            if not isinstance(entry['reason'],str)or not entry['reason'].strip():bad('missing_historical_role_reason',condition=pid)
            if entry['basis']not in oc.REMOVAL_BASES:bad('invalid_historical_role_basis',condition=pid)
            eids=entry['evidence_ids']
            if not isinstance(eids,list)or not all(isinstance(e,str)and e for e in eids):bad('invalid_historical_role_evidence',condition=pid);continue
            if len(set(eids))!=len(eids):bad('duplicate_historical_role_evidence',condition=pid)
            for eid in eids:
                if eid not in self.evidence:bad('historical_role_unknown_evidence',condition=pid,evidence_id=eid)
                elif pid in byid and not self.store.get(eid).represents(byid[pid]['snapshot'].lower()):bad('historical_role_wrong_snapshot',condition=pid,evidence_id=eid)
        if len(set(current))!=len(current)or len(set(historical_ids))!=len(historical_ids):bad('duplicate_route_role_id')
        if set(current)&set(historical_ids):bad('overlapping_route_roles')
        if set(current)|set(historical_ids)!=set(ids):bad('route_assignment_not_exact_partition',missing=sorted(set(ids)-set(current)-set(historical_ids)),unknown=sorted((set(current)|set(historical_ids))-set(ids)))
        previous=self.declared_route_assignments.get(cid)
        if previous is not None:
            prior_roles={pid:'current'for pid in previous['current_condition_ids']};prior_roles.update({x['condition_id']:'historical'for x in previous['historical']})
            next_roles={pid:'current'for pid in current};next_roles.update({pid:'historical'for pid in historical_ids})
            if any(prior_roles.get(pid)!=role for pid,role in next_roles.items()if pid in prior_roles)and c['change_basis']=='none':bad('role_change_without_model_basis')
        return current,historical

    def _commit_role_history(self,claims,round_index):
        for c in claims:
            cid=c['id'];assignment=copy.deepcopy(c['route_assignment']);previous=self.declared_route_assignments.get(cid)
            declared_current=set(assignment['current_condition_ids']);hist={v['condition_id']:v for v in assignment['historical']}
            versions=self.condition_history.setdefault(cid,{})
            for p in c['conditions']:
                pid=p['id'];role='current'if pid in declared_current else'historical';v=dict(round=round_index,raw_condition=copy.deepcopy(p),declared_role=role)
                old=versions.setdefault(pid,[])
                if not old or old[-1]['raw_condition']!=p or old[-1]['declared_role']!=role:old.append(v)
            retirement=[]
            for pid,v in hist.items():
                retirement.append(dict(condition_id=pid,reason=v['reason'],basis=v['basis'],evidence_ids=list(v['evidence_ids']),source_references=self.ledger._refs(v['evidence_ids']),raw_condition_sha256=digest(next(p for p in c['conditions']if p['id']==pid)),semantic_necessity_guaranteed=False))
            cand=self.ledger.candidates[cid]
            self.role_history.append(dict(round=round_index,claim_id=cid,declared_assignment=assignment,previous_assignment_sha256=digest(previous)if previous is not None else None,effective_route_ids=list(cand.routes['necessary'].obligation_ids),effective_required_ids=[p.id for p in cand.obligations.values()if p.required],retired_by_model_role=retirement,semantic_necessity_guaranteed=False))
            self.declared_route_assignments[cid]=assignment

    def update(self,payload,round_index):
        mapped=[];errors=[]
        claim_ids=[c['id']for c in payload['claims']]
        if len(set(claim_ids))!=len(claim_ids):errors.append(dict(code='duplicate_claim_id'))
        for c in payload['claims']:
            if not c['reason'].strip():errors.append(dict(code='missing_model_reason',claim=c['id']))
            old=self.ledger.candidates.get(c['id'])
            current,historical=self._assignment(c,old,errors)
            current_set=set(current)
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
                conditions.append(dict(id=p['id'],predicate=p['predicate'],required=self.mapping_mode=='legacy'or p['id']in current_set,requires=required,
                    status={'supported':'supported','refuted':'contradicted','unresolved':'unknown'}[p['status']],
                    snapshot=p['snapshot'].lower(),evidence_ids=p['evidence_ids'],assessed_by='model'))
            challenges=[dict(id=x['id'],text=x['text'],state=x['state'],material=True,target=None,evidence_ids=x['evidence_ids']) for x in c['challenges']]
            item=dict(id=c['id'],claim=c['claim'],scope=c['trigger'],origin=c['reason'],
                origin_evidence_ids=list(dict.fromkeys(e for p in c['conditions'] for e in p['evidence_ids'])),
                obligations=conditions,challenges=challenges,routes=[dict(id='necessary',obligation_ids=[p['id']for p in conditions if self.mapping_mode=='legacy'or p['id']in current_set])],changes=[])
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
                        if kind=='required'and a is True and b is False:
                            declaration=historical.get(target)
                            if declaration:
                                basis=declaration['basis'];reason=declaration['reason'];ids=declaration['evidence_ids']
                        if kind=='route_modified'and target=='necessary':
                            previous=self.declared_route_assignments.get(c['id'],{})
                            prior=set(previous.get('current_condition_ids',[]))
                            retired=[v for pid,v in historical.items()if pid in prior and pid not in current_set]
                            if retired:
                                reason=c['reason']+'\nExplicit historical-role declarations: '+json.dumps(retired,ensure_ascii=False)
                                ids=list(dict.fromkeys(e for v in retired for e in v['evidence_ids']))
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
            if record['accepted']:self._commit_role_history(payload['claims'],round_index)
        record['semantic_assessor']='reviewer_model'
        record['mechanical_truth_scope']='IDs, snapshots, source-bearing kinds, reason-bearing structural changes; not causal or path truth'
        self.updates.append(record)
        self.rounds.append(dict(round=round_index,raw_claims=copy.deepcopy(payload['claims']),questions=copy.deepcopy(payload['questions']),update=record,state=self.ledger.export()))
        return record
    def export(self):
        return dict(state=self.ledger.export(),transitions=[t.to_dict() for t in self.ledger.transitions],rejections=self.ledger.rejections,updates=self.updates,rounds=self.rounds,mapping_mode=self.mapping_mode,declared_route_assignments=copy.deepcopy(self.declared_route_assignments),condition_history=copy.deepcopy(self.condition_history),role_history=copy.deepcopy(self.role_history),semantic_necessity_guaranteed=False)
    def public_state(self):
        return dict(ledger=self.ledger.export(),declared_route_assignments=copy.deepcopy(self.declared_route_assignments),condition_history={cid:{pid:dict(versions=len(values),sha256=digest(values))for pid,values in conditions.items()}for cid,conditions in self.condition_history.items()},role_history=dict(records=len(self.role_history),sha256=digest(self.role_history)),semantic_necessity_guaranteed=False)
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
