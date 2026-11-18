"""Record manually read final judgments against immutable original stage hashes."""
import json
import sys
from pathlib import Path
import runner as e

R = Path(__file__).resolve().parent
arm = sys.argv[1]
assert arm in e.ARMS
path = R / f'author_annotations_{arm}.json'
out = e.read(path) if path.exists() else {}
jobs = {j['run_id']: j for j in e.jobs()}
old = e.read(e.PRIOR / 'author_annotations.json')
for group in json.load(sys.stdin):
    ids = group.pop('run_ids', None) or [group.pop('run_id')]
    for rid in ids:
        assert rid in jobs and jobs[rid]['arm'] == arm and rid not in out, rid
        job = jobs[rid]
        assert (R / 'trajectories' / f'{rid}.json').is_file(), (rid, 'not completed')
        row = dict(group)
        required = ['first_target_state', 'final_target_state', 'supported_issue_indices', 'unsupported_issue_indices',
                    'local_supported_issue_indices', 'safe_verdict_supported', 'reason']
        assert all(k in row for k in required), rid
        original = f"{job['case_id']}_{job['world']}_G_r{job['repeat']}"
        assert row['first_target_state'] == old[original]['first_target_state'], (rid, 'shared first annotation mismatch')
        firstpath = e.resolve_stage(job, 1)['raw_path']
        assert e.sha(firstpath) == old[original]['raw_sha256']['1'], (rid, 'shared first raw hash mismatch')
        final = e.stage_record(job, 2)
        n = len(final['response']['review']['issues']) if final and final.get('response') else 0
        assert sorted(row['supported_issue_indices'] + row['unsupported_issue_indices']) == list(range(n)), rid
        assert set(row['supported_issue_indices']) <= set(row['local_supported_issue_indices']) <= set(range(n)), rid
        row['reviewer'] = 'AI assistant; nonblind, not independent human adjudication'
        row['original_first_run_id'] = original
        row['raw_sha256'] = {str(s): e.sha(e.resolve_stage(job, s)['raw_path']) if e.resolve_stage(job, s)['raw_path'].exists() else None for s in (1, 2)}
        out[rid] = row
path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
print('annotated', arm, len(out))
