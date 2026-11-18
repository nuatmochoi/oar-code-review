"""Describe actual retrieval interventions separately from final review accuracy."""
import collections,hashlib,json,statistics
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
rows=[];excluded=collections.Counter();examples={}
for p in sorted((R/'retrieval').glob('*.json')):
 rid=p.stem;cid,w,arm,rep=rid.split('_');x=read(p);a=x['audit']
 initial=read(R/'packets'/f'{cid}_{w}_initial.json');fixed=read(R/'packets'/f'{cid}_{w}_fixed.json')
 initial_lines={(v,f,ln) for v,key in [('head','source_lines'),('base','base_lines')] for f,ls in initial['audit'][key].items() for ln in ls}
 final_lines={(v,f,ln) for v,key in [('head','source_lines'),('base','base_lines')] for f,ls in a[key].items() for ln in ls}
 guided=[q for q in a['queries'] if q['origin']=='model_gap_request']
 first=read(R/'raw'/f'{rid}_s1.json')['response']
 row={'run_id':rid,'case_id':cid,'world':w,'arm':arm,'requested':len(first['context_requests']),'accepted':sum(q.get('accepted',False) for q in a['requests']),'executed_guided':len(guided),'guided_empty_native_results':sum(not q['raw'] for q in guided),'guided_without_declaration_target':sum(not q['targets'] for q in guided),'new_source_lines':len(final_lines-initial_lines),'source_changed_vs_initial':x['source']!=initial['source'],'source_changed_vs_fixed':x['source']!=fixed['source'],'source_tokens':a['source_tokens'],'queries':len(a['queries']),'blocked_packages':len(a['blocked']),'actual_additional_native_calls':sum(q.get('method') in ['textDocument/definition','textDocument/references'] for q in x['actual_additional_native_trace'])}
 rows.append(row)
 for q in guided:
  for z in q['excluded']:
   reason=z.get('reason') if isinstance(z,dict) else z;excluded[reason]+=1
   if isinstance(z,dict):examples.setdefault(cid,[]).append(z)
summary={}
for arm in ['P0Q0','P1Q0','P0Q1','P1Q1']:
 rr=[x for x in rows if x['arm']==arm]
 if not rr:continue
 summary[arm]={'trajectories':len(rr)}|{k:sum(x[k] for x in rr) for k in ['requested','accepted','executed_guided','guided_empty_native_results','guided_without_declaration_target','source_changed_vs_initial','source_changed_vs_fixed','actual_additional_native_calls']}
 summary[arm].update(mean_source_tokens=statistics.mean(x['source_tokens'] for x in rr),mean_queries=statistics.mean(x['queries'] for x in rr),no_guided_execution=sum(x['executed_guided']==0 for x in rr))
static_identity=[]
for cid in sorted({x['case_id'] for x in rows}):
 for w in ['a','b']:
  pp=list((R/'retrieval').glob(f'{cid}_{w}_P*Q0_r*.json'))
  if pp:static_identity.append({'case_id':cid,'world':w,'fixed_source_identical_across_P_and_repeats':len({hashlib.sha256(read(p)['source'].encode()).hexdigest() for p in pp})==1})
out={'summary':summary,'rows':rows,'fixed_source_identity':static_identity,'guided_exclusions':dict(excluded),'excluded_examples':{c:list({(z['file'],z['line'],z['reason']):z for z in zz}.values())[:20] for c,zz in examples.items()},'note':'Requested counts include questions produced but ignored by fixed Q0. Accepted means queued; executed counts actual unique native requests. Source additions alone do not establish causal detection gains.'}
(R/'retrieval_analysis.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out['summary'],indent=2))
