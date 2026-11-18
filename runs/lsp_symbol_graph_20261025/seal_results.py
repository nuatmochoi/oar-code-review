"""Seal completed comparison and recheck all previously preserved cohorts."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

R = Path(__file__).resolve().parent
ROOT = R.parents[1]
read = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def verify(path, relative_root=ROOT):
    doc = read(path)
    for name, expected in doc['files'].items():
        target = Path(name)
        if not target.is_absolute():
            target = relative_root / target
        expected_hash = expected['sha256'] if isinstance(expected, dict) else expected
        assert target.is_file() and sha(target) == expected_hash, name
        if isinstance(expected, dict) and 'bytes' in expected:
            assert target.stat().st_size == expected['bytes'], name
    return len(doc['files'])


def main():
    assert not (R / 'result_seal.json').exists(), 'Existing result seal must not be replaced.'
    assert not (R / 'runner.lock').exists(), 'Model runner is still active.'
    required = ['scores.json', 'author_annotations.json', 'audit_all.json', 'run_summary.json',
                'context_comparison.json', 'operational_execution_analysis.json', 'REPORT_KO.md']
    for name in required:
        assert (R / name).is_file(), name
    audit = read(R / 'audit_all.json')
    gates = ['algorithm_hashes', 'freeze_hashes', 'workspace_hashes', 'packet_and_native_integrity',
             'shared_common_prompts', 'complete_trajectories', 'reuse_integrity', 'legacy_gate_regression',
             'exact_model_record_inventory', 'shared_first_annotations']
    assert all(audit['checks'][key] for key in gates), audit['checks']
    assert len(read(R / 'scores.json')['rows']) == len(read(R / 'author_annotations.json')) == 144
    assert read(R / 'run_summary.json')['completed_trajectories'] == 144
    verified = {}
    for name, before in read(R / 'reference_integrity_before.json').items():
        seal = Path(name)
        assert sha(seal) == before['sha256'], name
        # Historical result manifests use paths relative to their run folder;
        # algorithm/freeze and this new result manifest use workspace paths.
        count = verify(seal, relative_root=seal.parent)
        assert count == before['verified_files']
        verified[name] = {'sha256': sha(seal), 'verified_files': count, 'unchanged': True}
    for name in ['algorithm_seal.json', 'freeze.json']:
        verify(R / name)
    with (R / 'reference_integrity_after.json').open('x') as stream:
        json.dump(verified, stream, ensure_ascii=False, indent=2)
    files = {str(p.relative_to(ROOT)): sha(p) for p in sorted(R.rglob('*'))
             if p.is_file() and '__pycache__' not in p.parts and not p.name.startswith('.')}
    result = {'created_utc': datetime.now(timezone.utc).isoformat(), 'files': files,
              'scope': 'Completed G0/H paired follow-up comparison; original cohorts verified unchanged.',
              'fresh_CLI_invocations': read(R / 'run_summary.json')['fresh_CLI_invocations'],
              'logical_trajectories': 144, 'original_unique_shared_firsts': 72}
    with (R / 'result_seal.json').open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'sealed_files': verify(R / 'result_seal.json'), 'original_cohorts_unchanged': True}))


if __name__ == '__main__':
    main()
