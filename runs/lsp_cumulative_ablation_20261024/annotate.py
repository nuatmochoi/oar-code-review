"""Record explicitly read causal judgments and resolved original raw hashes."""
import json
import sys
from pathlib import Path
import experiment as e

R = Path(__file__).resolve().parent
arm = sys.argv[1]
assert arm in e.ARMS
path = R / f'author_annotations_{arm}.json'
out = e.read(path) if path.exists() else {}
jobs = {j['run_id']: j for j in e.jobs()}
for group in json.load(sys.stdin):
    ids = group.pop('run_ids', None) or [group.pop('run_id')]
    for rid in ids:
        assert rid in jobs and jobs[rid]['arm'] == arm and rid not in out, rid
        row = dict(group)
        required = ['first_target_state', 'final_target_state', 'supported_issue_indices', 'unsupported_issue_indices',
                    'local_supported_issue_indices', 'safe_verdict_supported', 'reason']
        assert all(k in row for k in required), rid
        final = e.stage_record(jobs[rid], 2)
        n = len(final['response']['review']['issues']) if final and final.get('response') else 0
        assert sorted(row['supported_issue_indices'] + row['unsupported_issue_indices']) == list(range(n)), rid
        assert set(row['supported_issue_indices']) <= set(row['local_supported_issue_indices']) <= set(range(n)), rid
        row.setdefault('reviewer', 'AI assistant; nonblind, not independent human adjudication')
        row['raw_sha256'] = {str(s): e.sha(e.resolve_stage(jobs[rid], s)['raw_path']) if e.resolve_stage(jobs[rid], s)['raw_path'].exists() else None for s in [1, 2]}
        out[rid] = row
path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
print('annotated', arm, len(out))
