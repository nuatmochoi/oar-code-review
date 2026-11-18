"""Seal finished artifacts, retaining operational failures in the experiment."""
import hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1]
read=lambda p:json.loads(p.read_text())
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
target=R/'result_seal.json'
if '--verify' in sys.argv:
 seal=read(target);errors=[k for k,v in seal['files'].items() if not (R/k).is_file() or sha(R/k)!=v['sha256']]
 errors.extend('predecessor:'+k for k,v in seal['predecessor_manifests'].items() if sha(ROOT/k)!=v)
 print(json.dumps({'verified_files':len(seal['files']),'errors':errors}));raise SystemExit(bool(errors))
assert not target.exists()
a=read(R/'audit_all.json');s=read(R/'scores.json');run=read(R/'run_summary.json')
resume=read(R/'resume_audit.json')
assert resume['all_checks_passed'] and resume['no_preexisting_stage_reissued']
assert resume['total_model_calls']==sum(x['physical_calls'] for x in s['rows'])
assert all(a['checks'][k] for k in ['algorithm_hashes','freeze_hashes','workspace_hashes','packet_and_native_integrity','shared_first_prompts','complete_trajectories'])
assert len(s['rows'])==len(read(R/'author_annotations.json'))==run['completed_trajectories']==144
assert read(R/'grading_crosscheck.json') and (R/'REPORT_KO.md').is_file()
for arm in ['F','G']:
 q=s['summary'][arm];assert q['unsafe_reviews']==q['safe_reviews']==36
 assert q['grounded_F1']['TP']+q['grounded_F1']['FN']==36
 assert q['grounded_F1']['TP']+q['grounded_F1']['FP']==q['reported_issues']
excluded={'__pycache__','states','checkouts','workspaces'}
files={str(p.relative_to(R)):{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(R.rglob('*')) if p.is_file() and not any(x in excluded for x in p.relative_to(R).parts) and p!=target}
predecessors={str((ROOT/'runs'/old/name).relative_to(ROOT)):sha(ROOT/'runs'/old/name) for old in ['lsp_context_closure_20261023','lsp_linked_adaptive_20261023','lsp_agentic_preserved_20261023'] for name in ['algorithm_seal.json','freeze.json','result_seal.json']}
seal={'created_utc':datetime.now(timezone.utc).isoformat(),'trajectories':144,'physical_model_calls':sum(x['physical_calls'] for x in s['rows']),'status_counts':run['status_counts'],'scope':'All run files except caches/workspaces and this manifest. Shared algorithm/workspace hashes are checked transitively by audit_all. Prior evidence remains sealed.','predecessor_manifests':predecessors,'files':files}
target.write_text(json.dumps(seal,ensure_ascii=False,indent=2));print(json.dumps({'sealed_files':len(files),'path':str(target)}))
