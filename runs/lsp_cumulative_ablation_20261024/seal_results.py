"""Seal completed primary/recovery artifacts without removing failed attempts."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

R = Path(__file__).resolve().parent
ROOT = R.parents[1]
read = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
target = R / 'result_seal.json'
if '--verify' in sys.argv:
    seal = read(target)
    errors = [k for k,v in seal['files'].items() if not (R/k).is_file() or sha(R/k)!=v['sha256']]
    errors += ['reference:'+k for k,v in seal['references'].items() if not (ROOT/k).is_file() or sha(ROOT/k)!=v]
    print(json.dumps({'verified_files':len(seal['files']),'errors':errors}))
    raise SystemExit(bool(errors))
assert not target.exists() and not (R/'runner.lock').exists()
run, scores = read(R/'run_summary.json'), read(R/'scores.json')
assert run['completed_trajectories']==run['logical_trajectories']
for name,digest in {**read(R/'algorithm_seal.json')['files'],**read(R/'freeze.json')['files']}.items():
    assert sha(ROOT/name)==digest, name
references = {}
if R.name == 'lsp_cumulative_ablation_20261024':
    audit = read(R/'audit_all.json')
    assert all(audit['checks'][k] for k in ['algorithm_hashes','freeze_hashes','workspace_hashes','packet_and_native_integrity',
        'shared_common_prompts','complete_trajectories','reuse_integrity','legacy_gate_regression','exact_model_record_inventory'])
    assert len(read(R/'author_annotations.json'))==216 and len(scores['rows'])==360
    assert (R/'REPORT_KO.md').exists() and read(R/'parent_crosscheck.json')
    assert read(R/'execution_analysis.json')
    for path,digest in read(R/'reference_seals.json').items():
        assert sha(Path(path))==digest
        references[str(Path(path).relative_to(ROOT))]=digest
    p=R.parent/'lsp_cumulative_recovery_20261024/result_seal.json'
    references[str(p.relative_to(ROOT))]=sha(p)
else:
    assert R.name=='lsp_cumulative_recovery_20261024' and len(scores['recovery_audit'])==6
    assert sum(len(read(R/f'author_annotations_{a}.json')) for a in ['B0','B1','B2'])==6
    for path,digest in read(R/'analysis_seal.json')['files'].items():
        assert sha(Path(path))==digest
    for name in ['algorithm_seal.json','freeze.json','scores.json']:
        p=R.parent/'lsp_cumulative_ablation_20261024'/name
        references[str(p.relative_to(ROOT))]=sha(p)
for arm,s in scores['summary'].items():
    assert s['unsafe_reviews']==s['safe_reviews']==36
    assert s['grounded_F1']['TP']+s['grounded_F1']['FN']==36
    assert s['grounded_F1']['TP']+s['grounded_F1']['FP']==s['reported_issues']
excluded={'__pycache__','states','checkouts','workspaces'}
files={str(p.relative_to(R)):{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(R.rglob('*'))
       if p.is_file() and p!=target and not any(x in excluded for x in p.relative_to(R).parts)}
seal={'created_utc':datetime.now(timezone.utc).isoformat(),'logical_trajectories_in_this_run':run['logical_trajectories'],
      'fresh_transport_entries':run['fresh_CLI_invocations'],'retained_status_counts':run['trajectory_statuses'],
      'scope':'All saved run artifacts except caches/workspaces and this seal. Operational failures retained; recovery composite explicitly secondary.',
      'references':references,'files':files}
with target.open('x') as f:
    json.dump(seal,f,ensure_ascii=False,indent=2)
print(json.dumps({'sealed_files':len(files),'path':str(target)}))
