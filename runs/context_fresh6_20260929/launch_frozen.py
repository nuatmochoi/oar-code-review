"""Freeze assessment materials and launch the already frozen experiment once."""
import json,sys,runpy
from datetime import datetime,timezone
from pathlib import Path
R=Path(__file__).resolve().parent;sys.path.insert(0,str(R.parents[1]))
from ace_review.context_adaptive import digest
OUT=R/'experiment_v1'
assert not (OUT/'raw').exists()
assert (R/'run_experiment.py').read_text()==(OUT/'implementation/run_experiment.py').read_text()
for name in ['provenance_audit.json','index_validation_equivalence.json','index_cache_equivalence.json','peewee_boundary_validation.json','witness_results.json']:
 assert json.loads((R/name).read_text())['valid'],name
files=['PLAN_KO.md','manifest.json','evaluation_targets.json','adjudication_protocol.json','provenance_audit.json','overlap_audit.json','witness_results.json','peewee_boundary_validation.json','index_validation_equivalence.json','audit_experiment.py','audit_sources.py','run_experiment.py','launch_frozen.py']
record={'frozen_before_model_calls':True,'created_utc':datetime.now(timezone.utc).isoformat(),'files':{p:digest((R/p).read_text()) for p in files},'main_freeze_sha256':digest((OUT/'freeze.json').read_text())}
with (OUT/'assessment_freeze.json').open('x') as f:json.dump(record,f,indent=2)
print(json.dumps({'assessment_frozen':True,'planned_calls':78}),flush=True)
runpy.run_path(str(R/'run_experiment.py'),run_name='__main__')
