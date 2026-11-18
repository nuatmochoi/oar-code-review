"""Persist author annotations before revealing arm/world mapping."""
import json
from pathlib import Path
R=Path(__file__).resolve().parent
rows=json.loads((R/'blind_responses.json').read_text())
conditional={'Bc40a6bba7d','B975150e706','B942231c49a'}
outscope={'B206c54e27f','Bcf4d94d4d4','Beb1b3b8f04'}
annotations=[]
for r in rows:
 status=r['response']['status'];bid=r['blind_id'];alarm=status=='reported_issues'
 note='No issue asserted.'
 if alarm:note='Correct target mechanism and stated concrete triggering configuration.'
 if bid in conditional:note='Correct None-to-join mechanism, but only conditional optional-group scenario; actual deployment arguments not established in response.'
 if bid in outscope:note='Explicitly acknowledges deployed -1 is preserved but reports a hypothetical different budget. Target-path alarm outside the shown fixture.'
 if bid=='Bec304bfc88':note='Correct empty-name helper None-to-membership mechanism. Says omitted doc_names rather than explicit [], which has identical helper input; inspect supplied helper for grounding.'
 if bid=='Ba06c9e4c05':note+=' Prose contains mistyped evidence ID E-2dd359c97422d; structured citation correctly uses E-2dd359b62473. Report citation defect separately.'
 annotations.append({'blind_id':bid,'case_id':r['case_id'],'status':status,'target_alarm':alarm,'mechanism_correct':alarm,'concrete_trigger_claim':alarm and bid not in conditional|outscope,'conditional_trigger_only':bid in conditional,'explicitly_outside_fixture':bid in outscope,'rationale':note})
(R/'blind_annotations.json').write_text(json.dumps(annotations,ensure_ascii=False,indent=2))
print('Persisted',len(annotations),'author arm-hidden annotations')
