"""Grade-free prospective input admission and source/state-growth stress test."""
import copy,json
from common import ROOT,PREVIOUS,load,write_new,source_tokens
import reexplore_run as r
from reexplore_prompts import replan_prompt,final_prompt
from schemas import REVIEW
from oar_core import count_reference_tokens as tokens

def grow_sources(es):
    result=copy.deepcopy(es);units=[e for e in es if e.get('kind')in('source_lines','implementation_body','exception_branch','body')]
    if not units:return result
    for i in range(80):
        e=copy.deepcopy(units[i%len(units)]);e['id']=f'STRESS-NOT-REAL-{i}';e['path']='STRESS-NOT-REAL/'+str(i)+'/'+e['path']
        if source_tokens(result+[e])>12000:continue
        result.append(e)
    return result

def build():
    rows=[];stress=[]
    for c in load(ROOT/'prepared/cases.json'):
        raw,reserve,a=r.initial_admission(c);view=r.view_case(c)
        es=grow_sources(r.evidence_for(c,r.context_for(c)))
        for n in(1,2):
            rows.append(dict(case_id=c['id'],repeat=n,initial_status='ok'if a['admitted']else'blocked',admission=a,diff_view=view['budget_view']))
            # Old REVIEW shapes size a diagnostic only; no gold/grade or this
            # synthetic input is ever passed into an actual new reviewer.
            old=load(PREVIOUS/'results/reviews'/f'{c["id"]}_B2_r{n}'/'review.result.json')
            initial=old.get('payload')or dict(assessment='uncertain',findings=[],claims=[],questions=[],reasoning='',termination_reason='')
            initial=copy.deepcopy(initial)
            while tokens(json.dumps(initial))<4000:initial['reasoning']+=' Source-bound causal question remains unresolved.'
            f=r.Frontier(initial,es)
            execution=r.blank_execution('synthetic_stress_no_model_or_tool')
            second=replan_prompt(view,es,initial,execution,f,{})
            p=dict(claims=initial['claims'],questions=initial['questions'],coverage=[],explorations=[],diff_facts=[],reasoning='Synthetic worst-state display, not a real candidate.',termination_reason='')
            final=final_prompt(view,es,initial,p,execution,f.export(),{}, {},execution,[])
            req1=a['required_reserve'];req2=r.admission(second,r.REPLAN)['required_reserve'];req3=r.admission(final,r.FINAL)['required_reserve']
            used1=max(0,req1-2000);used2=max(0,req2-2000)
            reserve2=r.reserve_final(c,es,initial,{},f.export(),execution)
            s2=r.admission(second,r.REPLAN,used1,1,final_reserve=reserve2)
            s3=r.admission(final,r.FINAL,used1+used2,2)
            stress.append(dict(case_id=c['id'],repeat=n,source_reference_tokens=source_tokens(es),synthetic_initial_JSON_reference_tokens=tokens(json.dumps(initial)),first_required=req1,second_required=req2,final_required=req3,projected_total=req1+req2+req3-4000,second_admitted=s2['admitted'],final_admitted=s3['admitted'],scope='Synthetic source/state-growth diagnostic, not actual provider cost or a guarantee about future model output.'))
    return dict(models=0,all=96,rows=rows,stress=stress,complete=True)

if __name__=='__main__':
    v=build();write_new(ROOT/'audit/budget_growth_preflight.json',v)
    print(json.dumps(dict(initial_admitted=sum(x['admission']['admitted']for x in v['rows']),second_stress_admitted=sum(x['second_admitted']for x in v['stress']),final_stress_admitted=sum(x['final_admitted']for x in v['stress']),max_projected=max(x['projected_total']for x in v['stress']))))
