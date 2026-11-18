"""Native-only development check; no reviewer, gold lookup or model call."""
from concurrent.futures import ProcessPoolExecutor,as_completed
from common import ROOT,BUDGETS,load,write_new
from symbol_context import ContextCollector
from controller_frontier import acquire

def one(u):
    key=f'{u["case_id"]}_r{u["repeat"]}';case=next(c for c in load(ROOT/'prepared/cases.json')if c['id']==u['case_id'])
    ref=load(ROOT/'prepared/first_acquisition_manifest.json')['units'][key];old=load(ref['path']);context=old['context'];oldids={e['id']for e in context['evidence']};reports=[]
    for phase in('before_replan','before_final'):
        tool=ContextCollector(case,ROOT/'audit/full_frontier_v7'/key/phase,BUDGETS,initial_state=context)
        try:report=acquire(tool,old['initial']['questions']if phase=='before_replan'else[],phase=phase,include_changed=phase=='before_replan',completed_answers=old['first_execution']['answers']if phase=='before_replan'else[],claims=old['initial']['claims'])
        finally:tool.close();context=tool.export()
        reports.append(report)
    row=dict(unit=key,models=0,phases=[dict(phase=r['phase'],nodes=len(r['records']),source_queries=sum(not v['question']['query'].startswith('_')for v in r['records']),termination=r['termination_reason'],errors=r['errors'],incremental_cost=r['incremental_cost'])for r in reports],new_sources=[dict(id=e['id'],snapshot=e['snapshot'],path=e['path'],start=e['start_line'],end=e['end_line'],kind=e.get('mechanical_checks',{}).get('unit_kind'))for e in context['evidence']if e['id']not in oldids and e['kind']in('source_lines','implementation_body')],cost=context['cost'],source_tokens=context['source_tokens'])
    write_new(ROOT/'audit/full_frontier_v7'/key/'context.json',context);print(__import__('json').dumps(row),flush=True);return row

if __name__=='__main__':
    with ProcessPoolExecutor(max_workers=4)as p:rows=[f.result()for f in as_completed([p.submit(one,u)for u in load(ROOT/'prepared/units.json')['units']])]
    write_new(ROOT/'audit/full_frontier_preflight_v7.json',dict(models=0,rows=rows))
