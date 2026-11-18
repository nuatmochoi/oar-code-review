"""Native-only public-source fixtures; no new model or private truth access."""
import copy,json,time
from concurrent.futures import ProcessPoolExecutor,as_completed
from common import ROOT,PROJECT,load,write_new,BUDGETS
import reexplore_run as r
from staged_run import acquisition
from persistent_context import ContextCollector
from discovery_policy import intent_for

OLD=PROJECT/'runs/lsp_controller_frontier_20261104'
def one(unit,arm):
    cid=unit['case_id'];n=unit['repeat'];key=f'{cid}_{arm}_r{n}';case=next(c for c in load(ROOT/'prepared/cases.json')if c['id']==cid)
    ref=load(OLD/'prepared/first_acquisition_manifest.json')['units'][f'{cid}_r{n}'];prior=load(ref['path']);context=r.context_for(case);tool=ContextCollector(case,ROOT/'audit/native_preflight'/key,BUDGETS,initial_state=context);reports=[];questions=[]
    try:
        for phase,payload in(('before_replan',prior['initial']),('before_final',prior['replan'])):
            tool.set_phase(phase);p=copy.deepcopy(payload);p['retrieval_intents']=[intent_for(q,[])for q in p['questions']];answers=[]
            for q in p['questions']:
                answer=tool.query(q);answer['execution']='backend_query';answers.append(answer);questions.append({'query':q['query'],'unresolved':answer['unresolved']})
            reports.append(acquisition(tool,arm,p,{'answers':answers},phase))
    finally:tool.budgets['max_native_calls']=48;tool.budgets['max_file_reads']=256;tool.close();context=tool.export()
    row=dict(unit=key,arm=arm,model_calls=0,cost=context['cost'],source_tokens=context['source_tokens'],phases=[dict(phase=p['phase'],queries=sum(not x['question']['query'].startswith('_')for x in p['records']),operations=dict(__import__('collections').Counter(x['question']['query']for x in p['records']if not x['question']['query'].startswith('_'))),reason=p['termination_reason'],native_cap=p['native_cap'],errors=p['errors'])for p in reports],question_outcomes=questions)
    write_new(ROOT/'audit/native_preflight'/key/'context.json',context);print(json.dumps({k:row[k]for k in('unit','phases')}),flush=True);return row
if __name__=='__main__':
    jobs=[(u,a)for u in load(ROOT/'prepared/units.json')['units']for a in('R0','R2')]
    with ProcessPoolExecutor(max_workers=4)as p:rows=[f.result()for f in as_completed([p.submit(one,*j)for j in jobs])]
    assert all(v['phases'][0]['native_cap']==34 and v['phases'][1]['native_cap']==46 for v in rows)
    assert all(v['cost']['native_calls']<=48 and v['cost']['file_reads']<=256 and v['source_tokens']<=12000 for v in rows)
    write_new(ROOT/'audit/native_preflight.json',dict(passed=True,models=0,rows=rows,scope='Old public reviewer plans are labelless native fixtures, not reused fresh-review inputs. R0 and R1 use identical native policy. No F1 tested.'))
