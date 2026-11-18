import json,sys
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
p=R/'author_annotations_G_last6.json';ann=read(p) if p.exists() else {};labels=read(R/'labels.json');limit=int(sys.argv[1]) if len(sys.argv)>1 else 6;count=0
for p in sorted((R/'trajectories').glob('*_G_*.json')):
 t=read(p);rid=p.stem
 if rid in ann or int(t['case_id'][1:])<7:continue
 print('\n###',rid,labels[t['case_id']]['worlds'][t['world']],t['status'])
 for k in [1,2]:
  rp=R/'raw'/f'{rid}_s{k}.json'
  if not rp.exists():print(k,'NO_CALL');continue
  x=read(rp);d=x.get('response')
  if not d:print(k,'ERROR',x.get('error'));continue
  out={'review':d['review']}
  if d['review']['status']!='insufficient_evidence':out['checks']=d['checks']
  print(k,json.dumps(out,ensure_ascii=False))
 count+=1
 if count>=limit:break
print('COUNT',count)
