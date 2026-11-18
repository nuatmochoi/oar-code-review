"""Describe retrieval and source preservation without inferring model understanding."""
import collections,json,statistics
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
freeze=read(R/'freeze.json');rows=[]
for job in freeze['jobs']:
 rid=job['run_id'];stem=f"{job['case_id']}_{job['world']}";p=R/'retrieval'/f'{rid}.json'
 if not p.exists():continue
 ret=read(p);seed=read(R/'packets'/'seed'/f'{stem}_initial.json');fixed=read(R/'packets'/'F'/f'{stem}_fixed.json');a=ret['audit']
 newlines=sum(len(set(v)-set(seed['audit']['source_lines'].get(f,[]))) for f,v in a['source_lines'].items())
 removed=sum(len(set(v)-set(a['source_lines'].get(f,[]))) for f,v in seed['audit']['source_lines'].items())
 requests=a.get('requests',[]);outcomes=[o for q in a.get('additional_queries',[]) for o in q.get('outcomes',[])]+a.get('query_outcomes',[])
 rows.append({**job,'protected_source_prefix':ret['source'].startswith(seed['source']),'removed_head_lines':removed,'added_head_lines':newlines,'source_changed':ret['source']!=seed['source'],'same_source_as_fixed':ret['source']==fixed['source'],'new_native_queries':len(a.get('additional_queries',[])),'source_tokens':a['source_tokens'],'requested':len(requests),'accepted':sum(x.get('outcome')=='queued' for x in requests),'requests':requests,'declared_gaps':a.get('declared_gaps',[]),'outcomes':dict(collections.Counter(x['outcome'] for x in outcomes)),'blocked_components':len(a.get('additional_blocked',[])),'added_components':a.get('additional_admitted',[])})
summary={}
for arm in ['F','G']:
 rr=[x for x in rows if x['arm']==arm];c=collections.Counter()
 for x in rr:c.update(x['outcomes'])
 summary[arm]={k:sum(x[k] for x in rr) for k in ['protected_source_prefix','removed_head_lines','added_head_lines','source_changed','same_source_as_fixed','new_native_queries','requested','accepted','blocked_components']}
 summary[arm].update(trajectories=len(rr),mean_source_tokens=statistics.mean(x['source_tokens'] for x in rr),outcomes=dict(c))
summary['native_count_note']='F rows reuse predecessor fixed packets: zero new F preparation calls in recovery. Their trace query counts describe prior work. G rows execute independently. Source line additions are per review, not unique code lines.'
(R/'retrieval_analysis.json').write_text(json.dumps({'summary':summary,'rows':rows},ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False,indent=2))
