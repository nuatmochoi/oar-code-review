from pathlib import Path
import json,re
ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'reviewbench_official25_guarded12k_20261105'
# Reuse the previously exercised streaming transport and durable admission ledger.
src=(OLD/'budget_proxy.py').read_text().replace('CAP=50.;REVIEW_CAP=45.','CAP=70.;REVIEW_CAP=55.').replace('8791','8793')
a=src.index('def estimated_cost(');b=src.index('class Budget:',a)
src=src[:a]+'''def estimated_cost(usage):
 i=usage.get('input_tokens',usage.get('prompt_tokens'));o=usage.get('output_tokens',usage.get('completion_tokens'))
 if not isinstance(i,int)or not isinstance(o,int):return None
 details=usage.get('input_tokens_details')or usage.get('prompt_tokens_details')or{}
 cached=details.get('cached_tokens',0);writes=details.get('cache_write_tokens',0);plain=i-cached-writes
 if plain<0:raise ValueError('Usage breakdown is inconsistent')
 rate=8 if i>272000 else 4;out=30 if i>272000 else 20
 usd=(plain*rate+cached*rate*.1+writes*rate*1.25+o*out)/1e6
 return dict(input_tokens=i,output_tokens=o,cached_tokens=cached,cache_write_tokens=writes,usd_list_rate_estimate=usd,usd_conservative_upper=usd)
'''+src[b:]
src=src.replace("input_bound=len(wire)+8192;reserve=(input_bound*20+limit*30)/1e6", "input_bound=len(wire)+8192;reserve=(input_bound*(10 if input_bound>272000 else 5)+limit*30)/1e6")
src=src.replace("status='upstream_rejected';error='HTTP '+str(exc.code)","status='upstream_rejected'if exc.code in (400,401,403,404,422,429)else'usage_unknown';error='HTTP '+str(exc.code)")
(ROOT/'budget_proxy.py').write_text(src)
# Include metadata-only changed files in coverage rather than silently dropping them.
manifest=json.loads((ROOT/'public/manifest.json').read_text());diag=json.loads((ROOT/'audit/change_unit_diagnostic.json').read_text())
for ref,row in zip(manifest,diag['rows']):
 p=Path(ref['path']);d=json.loads(p.read_text());present={u['path']for u in d['units']}
 for file in d['case']['changed_files']:
  if file in present:continue
  u=dict(id=f'U{len(d["units"])+1:04d}',path=file,base_start=0,head_start=0,base_end=0,head_end=0,header='metadata-only/binary/rename change; no textual hunk',changed_head=[],changed_base=[],sample=[],kind='metadata_only')
  d['units'].append(u);d['coverage'].append(dict(unit_id=u['id'],path=file,full_diff_delivered=True,head_complete_unit_evidence_ids=[],native_question_ids=[]))
 p.write_text(json.dumps(d,ensure_ascii=False,indent=2));ref['sha256']=__import__('hashlib').sha256(p.read_bytes()).hexdigest();row['metadata_only_units']=sum(u.get('kind')=='metadata_only'for u in d['units']);row['units']=len(d['units']);row['full_diff_delivered_units']=len(d['units'])
(ROOT/'public/manifest.json').write_text(json.dumps(manifest,indent=2));diag['units']=sum(x['units']for x in diag['rows']);(ROOT/'audit/change_unit_diagnostic.json').write_text(json.dumps(diag,ensure_ascii=False,indent=2))
print(json.dumps(dict(units=diag['units'],body_units=diag['complete_head_body_units'],native_question_units=diag['native_question_units'],metadata_only_units=sum(x['metadata_only_units']for x in diag['rows']))))
