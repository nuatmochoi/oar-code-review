"""Hide arm/world identities for author response annotation after execution."""
import hashlib,json,random
from pathlib import Path
R=Path(__file__).resolve().parent;OUT=R/'experiment_v1'
records=json.loads((OUT/'predictions.json').read_text());rows=[];mapping={}
for row in records:
 key='B'+hashlib.sha256(('fresh6-annotation-'+row['run_id']).encode()).hexdigest()[:10]
 mapping[key]={'run_id':row['run_id'],'case_id':row['case_id'],'world':row['world'],'arm':row['arm'],'transport_status':row['status']}
 rows.append({'blind_id':key,'case_id':row['case_id'],'transport_status':row['status'],'response':row['response']})
random.Random(20260930).shuffle(rows)
(R/'blind_responses.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2));(OUT/'private/blind_mapping.json').write_text(json.dumps(mapping,indent=2))
print(json.dumps(rows,ensure_ascii=False,indent=2))
