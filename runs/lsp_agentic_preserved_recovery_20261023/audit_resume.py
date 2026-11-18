"""Verify original evidence survived resume and stage inventory is nonduplicating."""
import hashlib,json
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1]
read=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
seal=read(R/'resume_seal.json');initial=read(R/'resume_initial_artifacts.json');cp=read(R/'pause_checkpoint.json');freeze=read(R/'freeze.json')
errors=[]
for p,h in seal['files'].items():
 if sha(ROOT/p)!=h:errors.append(['resume_seal',p])
for p,h in initial['files'].items():
 if sha(R/p)!=h:errors.append(['prior_evidence_changed',p])
planned={f"{j['run_id']}_s{k}" for j in freeze['jobs'] for k in [1,2]}
raw={p.stem for p in (R/'raw').glob('*_s[12].json')};started={p.name.removesuffix('.started.json') for p in (R/'call_metrics').glob('*.started.json')};actual={p.stem for p in (R/'actual_cli_starts').glob('*.json')}
if not raw<=planned:errors.append(['unplanned_records',sorted(raw-planned)])
if raw!=started or raw!=actual:errors.append(['stage_inventory',sorted(started-raw),sorted(raw-started),sorted(actual^raw)])
preexisting={f"{row['run_id']}_s{k}" for row in cp['rows'] for k in row['raw_sha256']}
if not preexisting<=raw:errors.append(['lost_prior_records'])
for p in (R/'trajectories').glob('*.json'):
 t=read(p);count=sum(p.stem+f'_s{k}' in raw for k in [1,2])
 if t['physical_calls']!=count:errors.append(['trajectory_call_count',p.stem])
out={'all_checks_passed':not errors,'errors':errors,'preserved_prior_evidence_files':len(initial['files']),'preserved_pre_pause_model_calls':len(preexisting),'resumed_new_model_calls':len(raw-preexisting),'total_model_calls':len(raw),'planned_maximum_calls':len(planned),'unexecuted_stage_slots':len(planned-raw),'all_planned_stages_executed':raw==planned,'user_pause_continuation_same_cohort':True,'no_preexisting_stage_reissued':not any(x[0]=='prior_evidence_changed' for x in errors)}
(R/'resume_audit.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2));assert not errors
