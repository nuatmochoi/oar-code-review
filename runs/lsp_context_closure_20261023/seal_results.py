"""Seal completed comparison artifacts; never rewrite experimental evidence."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

R = Path(__file__).resolve().parent
ROOT = R.parents[1]
OLD = ROOT / 'runs/lsp_linked_adaptive_20261023'
read = lambda p: json.loads(p.read_text())


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


target = R / 'result_seal.json'
if '--verify' in sys.argv:
    seal = read(target)
    errors = [name for name, record in seal['files'].items()
              if not (R / name).is_file() or sha(R / name) != record['sha256']]
    for name, record in seal['predecessor_manifests'].items():
        if sha(ROOT / name) != record:
            errors.append('predecessor:' + name)
    print(json.dumps({'verified_files': len(seal['files']), 'errors': errors}))
    raise SystemExit(bool(errors))

assert not target.exists(), 'Existing result seal is immutable; verify it instead.'
audit = read(R / 'audit_all.json')
scores = read(R / 'scores.json')
run = read(R / 'run_summary.json')
assert all(audit['checks'].values()), audit['checks']
assert len(scores['rows']) == 144
assert sum(x['physical_calls'] for x in scores['rows']) == 288
assert len(read(R / 'author_annotations.json')) == 144
assert run['completed_trajectories'] == 144 and run['calls_finished_this_process'] == 288
assert run['status_counts'] == {'ok': 144}
assert read(R / 'grading_crosscheck.json')
assert (R / 'REPORT_KO.md').is_file()
for arm in ['C0', 'C1']:
    s = scores['summary'][arm]
    assert s['unsafe_reviews'] == s['safe_reviews'] == 36
    assert s['grounded_F1']['TP'] + s['grounded_F1']['FN'] == 36
    assert s['grounded_F1']['TP'] + s['grounded_F1']['FP'] == s['reported_issues']

excluded = {'__pycache__', 'states', 'checkouts', 'workspaces'}
files = {}
for p in sorted(R.rglob('*')):
    rel = p.relative_to(R)
    if p.is_file() and not any(part in excluded for part in rel.parts) and p != target:
        files[str(rel)] = {'sha256': sha(p), 'bytes': p.stat().st_size}

predecessors = {str((OLD / name).relative_to(ROOT)): sha(OLD / name)
                for name in ['algorithm_seal.json', 'freeze.json', 'result_seal.json']}
seal = {
    'created_utc': datetime.now(timezone.utc).isoformat(),
    'trajectories': 144,
    'physical_model_calls': 288,
    'scope': 'All new run files except caches, derived workspace/checkouts, large pickled states and this manifest itself. Includes raw responses, traces, exact prompts, native traces, packets, independent AI crosscheck, annotations, F1/bootstrap analysis scripts, report and protocol.',
    'predecessor_manifests': predecessors,
    'transitive_coverage': {
        'shared_algorithms': 'algorithm_seal.json records shared and new code hashes; audit_all.json verifies actual files.',
        'frozen_inputs': 'freeze.json records cases, labels, context packets and configuration hashes; audit_all.json verifies them.',
        'reused_workspaces': 'workspace_manifests/*.json point to immutable predecessor workspaces with source hashes and Pyright configuration; audit_all.json checks actual source contents.',
        'baseline_packets_and_witness': 'C0 packet bytes are compared to the predecessor; prior archived source/patch/witness provenance is protected by predecessor result_seal.json. predecessor_verification.json records re-verification of all 2507 old artifacts.'
    },
    'files': files,
}
target.write_text(json.dumps(seal, ensure_ascii=False, indent=2))
print(json.dumps({'sealed_files': len(files), 'path': str(target)}))
