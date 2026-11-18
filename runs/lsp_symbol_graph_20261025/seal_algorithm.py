"""Freeze the reviewed policy and analysis before any evaluation native calls."""
import hashlib
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

R=Path(__file__).resolve().parent
ROOT=R.parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(R))
read=lambda p:json.loads(p.read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert not (R/'algorithm_seal.json').exists()
assert not list((R/'native_claims').glob('*.json'))
assert not list((R/'actual_cli_starts').glob('*.json'))
for name in ['runner','graph_policy','audit_score']:
    importlib.import_module(name)
parent=ROOT/'runs/lsp_cumulative_ablation_20261024/algorithm_seal.json'
files=dict(read(parent)['files'])
for name,digest in files.items():
    assert sha(ROOT/name)==digest,name
for module in list(sys.modules.values()):
    filename=getattr(module,'__file__',None)
    if not filename:continue
    path=Path(filename).resolve()
    if path.suffix=='.py' and path.is_relative_to(ROOT) and 'site-packages' not in path.parts:
        files[str(path.relative_to(ROOT))]=sha(path)
required=['graph_policy.py','graph_policy_tests.py','runner.py','prepare.py','harness_selfcheck.py',
          'preparation_selfcheck.py','preparation_selfcheck_results.json',
          'audit_score.py','annotate.py','inspect_batch.py','seal_algorithm.py','PROTOCOL_KO.md',
          'grading_rules.json','runtime_manifest.json','reference_integrity_before.json',
          'preparation_plan.json','control_provenance.json','reference_seals.json',
          'harness_selfcheck_results.json','graph_policy_test_results.json','audit_selfcheck_results.json']
for name in required:
    path=R/name
    assert path.is_file(),name
    files[str(path.relative_to(ROOT))]=sha(path)
assert read(R/'harness_selfcheck_results.json')['all_passed']
assert read(R/'preparation_selfcheck_results.json')['all_passed']
assert read(R/'graph_policy_test_results.json')['status']=='passed'
assert read(R/'audit_selfcheck_results.json').get('passed',read(R/'audit_selfcheck_results.json').get('all_passed',read(R/'audit_selfcheck_results.json').get('status')=='passed'))
for path in (ROOT/'tools/pyright-lsp-20261023/node_modules/pyright/dist').rglob('*'):
    if path.is_file() and path.suffix in {'.js','.json','.node'}:
        files[str(path.relative_to(ROOT))]=sha(path)
for path in [parent,ROOT/'runs/lsp_agentic_preserved_recovery_20261023/author_annotations.json',
             ROOT/'runs/lsp_agentic_preserved_recovery_20261023/scores.json']:
    files[str(path.relative_to(ROOT))]=sha(path)
for path,entry in read(R/'reference_integrity_before.json').items():
    path=Path(path)
    assert sha(path)==entry['sha256']
    files[str(path.relative_to(ROOT))]=sha(path)
value={'created_utc':datetime.now(timezone.utc).isoformat(),'scope':'Policy, transport, preparation, rubric and analysis frozen before evaluation-native or model calls. Synthetic tests excluded from experimental query counts.',
       'inherited_algorithm':str(parent.relative_to(ROOT)),'files':dict(sorted(files.items())),
       'evaluation_native_calls_before_freeze':0,'new_model_calls_before_freeze':0}
with (R/'algorithm_seal.json').open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
print(json.dumps({'sealed_files':len(files),'path':str(R/'algorithm_seal.json')}))
