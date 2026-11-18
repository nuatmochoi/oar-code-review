"""Context additions and displacement, with native work counted only once."""
import collections,json,statistics
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
rows=[]
for p in sorted((R/'packets/C1').glob('*_fixed.json')):
    stem=p.stem.removesuffix('_fixed');old=read(R/'packets/C0'/p.name);new=read(p)
    def lines(d):return {(version,f,l) for version,key in [('head','source_lines'),('base','base_lines')] for f,ls in d['audit'][key].items() for l in ls}
    a,b=lines(old),lines(new);x=new['audit']
    rows.append({'world_id':stem,'old_source_tokens':old['audit']['source_tokens'],'new_source_tokens':x['source_tokens'],'added_numbered_lines':len(b-a),'removed_numbered_lines':len(a-b),'same_source':old['source']==new['source'],'old_queries':len(old['audit']['queries']),'new_queries':len(x['queries']),'class_inventories':len(x.get('class_inventories',[])),'module_sites':len(x.get('module_sites',[])),'constructor_components_supplied':sum(c['supplied'] for c in x.get('constructor_components',[])),'constructor_components_blocked':sum(not c['supplied'] for c in x.get('constructor_components',[])),'blocked_components':len(x['blocked']),'native_empty_results':sum(not q['raw'] for q in x['queries']),'query_origins':dict(collections.Counter(q['origin'] for q in x['queries']))})
out={'worlds':rows,'summary':{'worlds':len(rows),'source_changed':sum(not x['same_source'] for x in rows),'worlds_with_displacement':sum(x['removed_numbered_lines']>0 for x in rows),'mean_old_source_tokens':statistics.mean(x['old_source_tokens'] for x in rows),'mean_new_source_tokens':statistics.mean(x['new_source_tokens'] for x in rows),**{k:sum(x[k] for x in rows) for k in ['added_numbered_lines','removed_numbered_lines','new_queries','class_inventories','module_sites','constructor_components_supplied','constructor_components_blocked','native_empty_results']}},'note':'Prepared once perworld. C0 reuses frozen native packets from oldrun; C1 native queries are new. Code additions and displacement are counted independently of correctness; class metadata tokens are included in source cap.'}
(R/'retrieval_analysis.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out['summary'],indent=2))
