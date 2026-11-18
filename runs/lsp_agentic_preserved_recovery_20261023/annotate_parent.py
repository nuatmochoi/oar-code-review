"""Store explicitly read per-response judgments, separately per arm."""
import hashlib,json,sys
from pathlib import Path
R=Path(__file__).resolve().parent
arm=sys.argv[1];assert arm in ['F','G']
p=R/'author_annotations_G_last6.json';assert arm=='G';out=json.loads(p.read_text()) if p.exists() else {}
for group in json.load(sys.stdin):
    ids=group.pop('run_ids',None) or [group.pop('run_id')]
    for rid in ids:
        assert f'_{arm}_' in rid and rid not in out and int(rid[1:3])>=7,rid
        row=dict(group)
        required=['first_target_state','final_target_state','supported_issue_indices','unsupported_issue_indices','local_supported_issue_indices','safe_verdict_supported','reason']
        assert all(k in row for k in required),rid
        assert set(row['supported_issue_indices']).issubset(row['local_supported_issue_indices']),rid
        row.setdefault('reviewer','AI assistant; nonblind, not independent human adjudication')
        row['raw_sha256']={str(k):hashlib.sha256((R/'raw'/f'{rid}_s{k}.json').read_bytes()).hexdigest() for k in [1,2] if (R/'raw'/f'{rid}_s{k}.json').exists()}
        out[rid]=row
p.write_text(json.dumps(out,ensure_ascii=False,indent=2));print('annotated',arm,len(out))
