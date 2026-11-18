"""Finding dispositions are independent from the candidate/claim ledger.

All judgments remain model-assessed. This schema supplies stable controller IDs
and source-bound references; it does not certify a defect or preserve an old
finding automatically.
"""
import copy

def obj(properties):
 return {'type':'object','additionalProperties':False,'properties':properties,'required':list(properties)}
def arr(items):return {'type':'array','items':items}
def enum(*values):return {'type':'string','enum':list(values)}
S={'type':'string'}
I={'type':'integer'}

EVIDENCE_REF=obj(dict(evidence_id=S,snapshot=enum('HEAD','BASE'),path=S,
 start_line=I,end_line=I,judgment=enum('supports','refutes','unresolved'),reason=S))
DISPOSITION=obj(dict(warning_id=S,
 disposition=enum('retained','revised','withdrawn','deferred','merged'),
 final_finding_indices=arr(I),evidence_state=enum('supported','refuted','unresolved'),
 basis=enum('initial_evidence','new_support','new_refutation','interpretation_correction','scope_revised','unresolved_check','duplicate'),
 evidence_refs=arr(EVIDENCE_REF),question_ids=arr(S),reason=S))

def dispositions_schema(warning_ids=None,evidence_ids=None,question_ids=None):
 item=copy.deepcopy(DISPOSITION)
 if warning_ids:item['properties']['warning_id']=enum(*warning_ids)
 if evidence_ids:item['properties']['evidence_refs']['items']['properties']['evidence_id']=enum(*evidence_ids)
 if question_ids:item['properties']['question_ids']['items']=enum(*question_ids)
 return arr(item)

def with_warning_dispositions(review_schema,warning_ids=None,evidence_ids=None,question_ids=None):
 """Return a copy; the old REVIEW object and frozen old runs are untouched."""
 value=copy.deepcopy(review_schema)
 value['properties']['warning_dispositions']=dispositions_schema(warning_ids,evidence_ids,question_ids)
 if 'warning_dispositions'not in value['required']:value['required'].append('warning_dispositions')
 return value
