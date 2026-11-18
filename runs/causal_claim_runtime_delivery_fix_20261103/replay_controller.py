"""Existing ledger; only an explicit runtime carrier extension is new.

Observation is scoped to the child analysis environment, never deployed proof.
It is metadata, cannot carry an implementation body or an operation location.
"""
import copy,json
import oar_core as oc
from claim_controller import ClaimController

class ReplayController(ClaimController):
    def add_evidence(self,items):
        static=[b for b in items if b.get('kind')!='runtime_observation']
        super().add_evidence(static)
        for b in items:
            if b.get('kind')!='runtime_observation':continue
            assert b['binding']['scope']=='analysis_profile'
            assert b['binding']['deployed_runtime_confirmed']is False
            assert b['mechanical_checks']['successful_scoped_execution']is True
            if b['id']in self.evidence:assert self.evidence[b['id']]==b;continue
            ev=oc.Evidence(id=b['id'],snapshot=b['snapshot'].lower(),file=b['path'],text=b['content'],provenance='runtime_probe',content_kind='ast_outline',runtime_validity='runtime_observed',backend='scoped_analysis_subprocess',anchor_line=None)
            self.store.add(ev);self.evidence[b['id']]=copy.deepcopy(b)
        self.ledger.store=self.store
