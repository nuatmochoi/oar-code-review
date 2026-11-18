"""Print unannotated completed reviews for explicit assistant-author inspection."""
import json,sys
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
annotations=read(R/'author_annotations.json') if (R/'author_annotations.json').exists() else {}
labels=read(R/'labels.json');limit=int(sys.argv[1]) if len(sys.argv)>1 else 12
count=0
for p in sorted((R/'trajectories').glob('*.json')):
 if p.stem in annotations:continue
 t=read(p)
 print('\n###',p.stem,labels[t['case_id']]['worlds'][t['world']],t['status'])
 for stage in [1,2]:
  rp=R/'raw'/f'{p.stem}_s{stage}.json'
  if not rp.exists():continue
  x=read(rp)
  response=x.get('response')
  if response:
   compact={'review':response['review']}
   if response['review']['status']=='reported_issues' or (stage==2 and response['review']['status']=='no_supported_issue'):compact['checks']=response['checks']
   print('stage',stage,json.dumps(compact,ensure_ascii=False))
  else:print('stage',stage,'error',x.get('error'))
 count+=1
 if count>=limit:break
print('\nINSPECTED_OUTPUT_COUNT',count)
