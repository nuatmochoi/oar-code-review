"""Record explicit author judgments; never infers support from model status."""
import hashlib,json,sys
from pathlib import Path
R=Path(__file__).resolve().parent
p=R/'author_annotations.json';out=json.loads(p.read_text()) if p.exists() else {}
entries=[]
for row in json.load(sys.stdin):
 ids=row.pop('run_ids',None)
 entries.extend([{**row,'run_id':rid} for rid in ids] if ids else [row])
for row in entries:
 rid=row.pop('run_id');assert rid not in out,rid
 assert all(k in row for k in ['first_target_state','final_target_state','supported_issue_indices','unsupported_issue_indices','safe_verdict_supported','reason'])
 row['reviewer']='assistant author; nonblind, not independent human adjudication'
 row['raw_sha256']={str(i):hashlib.sha256((R/'raw'/f'{rid}_s{i}.json').read_bytes()).hexdigest() for i in [1,2] if (R/'raw'/f'{rid}_s{i}.json').exists()}
 out[rid]=row
p.write_text(json.dumps(out,ensure_ascii=False,indent=2));print('annotated',len(out))
