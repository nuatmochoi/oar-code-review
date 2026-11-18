"""Preserve actual parent shared stages/plan without reading parent final answers."""
import hashlib
from pathlib import Path
from common import ROOT,load,write_new,sha,digest
PARENT=ROOT.parent/'causal_claim_runtime_replay_20261103'

def usage(r):
    t=r['actual_usage']['total'];assert r['actual_usage']['per_turn']
    return t['input_tokens']+t['output_tokens']

def main():
    complete=PARENT/'results/completed.json';assert complete.exists(),'Parent three finals must finish before supplement preparation completes.'
    refs={};payloads=[];tokens=[]
    for stage in ('verify_1','verify_2'):
        path=PARENT/'results/shared'/stage/'initial.result.json';r=load(path);assert r['status']=='ok'
        raw=PARENT/'results/shared'/stage/'initial.input.json'
        summary=PARENT/'results/shared'/(stage+'.summary.json');s=load(summary)
        refs[str(path)]={'sha256':sha(path)};refs[str(raw)]={'sha256':sha(raw)};refs[str(summary)]={'sha256':sha(summary)}
        payloads.append(s['accepted_payload']);tokens.append(usage(r))
    plan_summary=load(ROOT/'prepared/parent_plan_summary.json');plan=plan_summary['accepted_plan'];assert plan['assessment']=='propose'and len(plan['probes'])==1
    parent_plan=PARENT/'results/shared/planner/initial.result.json';r=load(parent_plan);assert r['status']=='ok'
    tokens.append(usage(r));refs[str(parent_plan)]={'sha256':sha(parent_plan)}
    parent_plan_input=parent_plan.with_name('initial.input.json');refs[str(parent_plan_input)]={'sha256':sha(parent_plan_input)}
    probe=plan['probes'][0];write_new(ROOT/'prepared/unchanged_probe.py',probe['code'])
    assert sha(ROOT/'prepared/unchanged_probe.py')==hashlib.sha256(probe['code'].encode()).hexdigest()
    query_records=[]
    for n in (1,2,3):
        p=PARENT/'results/static_queries'/f'query_round_{n}.json';q=load(p);query_records.append(q);refs[str(p)]={'sha256':sha(p)}
        for item in q['queries']:refs[item['raw_reference']['path']]={'sha256':item['raw_reference']['sha256']}
    # Only parent elapsed/cost fields are used from its final record, never its
    # final answer as a new reviewer input. Shared actual outputs above are the
    # identical common starting point of the parent three conditions.
    parent_cost=PARENT/'results/plan_no_observation/review.result.json';cost=load(parent_cost)['active_wall_seconds'];refs[str(parent_cost)]={'sha256':sha(parent_cost)}
    refs[str(complete)]={'sha256':sha(complete)}
    refs[str(PARENT/'freeze.json')]={'sha256':sha(PARENT/'freeze.json')}
    write_new(ROOT/'prepared/shared_context.json',dict(previous_stages=payloads,query_records=query_records,questions=plan_summary['questions'],plan=plan,shared_model_calls=3,shared_actual_model_tokens=sum(tokens),shared_stage_tokens=tokens,shared_active_wall_seconds=cost['common']+cost['planner'],common_active_wall_seconds=cost['common'],planner_active_wall_seconds=cost['planner'],parent_static_final_used_as_input=False,parent_final_payloads_used_as_input=False,probe_code_sha256=sha(ROOT/'prepared/unchanged_probe.py'),plan_sha256=digest(plan),external_references=refs))
    write_new(ROOT/'audit/shared_identity.json',dict(passed=True,parent_complete_reference={'path':str(complete),'sha256':sha(complete)},probe_code_sha256=sha(ROOT/'prepared/unchanged_probe.py'),actual_parent_plan_sha256=digest(plan),no_code_characters_changed=True,additional_native_calls=0,model_calls=0,actual_Django_probes=0))
    return dict(shared_physical_calls=3,shared_tokens=sum(tokens),probe_code_sha256=sha(ROOT/'prepared/unchanged_probe.py'))

if __name__=='__main__':print(main())
