"""Print completed unannotated reviews for explicit reading, never auto-grade."""
import json,sys
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
arm=sys.argv[1];assert arm in ['C0','C1'];limit=int(sys.argv[2]) if len(sys.argv)>2 else 10
ap=R/f'author_annotations_{arm}.json';ann=read(ap) if ap.exists() else {}
labels=read(R/'labels.json');count=0
for p in sorted((R/'trajectories').glob(f'*_{arm}_r*.json')):
    if p.stem in ann:continue
    t=read(p);print('\n###',p.stem,labels[t['case_id']]['worlds'][t['world']],t['status'])
    for k in [1,2]:
        rp=R/'raw'/f'{p.stem}_s{k}.json'
        if not rp.exists():continue
        x=read(rp);d=x.get('response')
        if not d:print('stage',k,'ERROR',x.get('error'));continue
        compact={'review':d['review']}
        if d['review']['status']=='reported_issues' or (k==2 and d['review']['status']=='no_supported_issue'):compact['checks']=d['checks']
        print('stage',k,json.dumps(compact,ensure_ascii=False))
    count+=1
    if count>=limit:break
print('INSPECTED_OUTPUT_COUNT',count)
