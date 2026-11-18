"""Supplement only budget-unissued stages; preserve exact completed work."""
import copy,json,time
from pathlib import Path
from common import ROOT,PROJECT,load,sha,digest,write_new
from loop_policy import plan_only,attach
from reexplore_run import RecordedAnswers
OLD=PROJECT/'runs/lsp_followup3_replanning_20261104'

def reuse_additional(case,n,arm,shared,latest,f,folder):
    if arm!='FOUR_QUERY'or not(ROOT/'prepared/exact_reuse.json').exists():return None
    unit=f'{case["id"]}_r{n}';old_shared=load(OLD/'results/shared'/unit/'shared.json');old_extra=load(OLD/'results/shared'/unit/'extra_replan.json')
    path=OLD/'results/reviews'/f'{case["id"]}_{arm}_r{n}'/'query_round_trace.json'
    if digest(shared)!=digest(old_shared)or old_extra['payload'] is None or digest(latest)!=digest(old_extra['payload'])or not path.exists():return None
    record=load(path)['additional_round']
    if record is None:return None
    original=record['core'];rebuilt=f.execute_plan(plan_only(latest),RecordedAnswers(original),enable_queries=True)
    assert digest(rebuilt)==digest(original),'Reused third acquisition must reproduce actual old controller transition'
    attach(f,record['context']['evidence'])
    write_new(folder/'additional_queries.reused.actual.json',dict(source=str(path),sha256=sha(path),same_actual_question_and_prefix=True,new_run_native_calls=0,logical_cost_preserved=True))
    return copy.deepcopy(record),copy.deepcopy(record['context'])

def prepare():
    from causal_loop import cases,units
    assert(OLD/'completion.json').exists();assert not(ROOT/'freeze.json').exists();refs={};calls={}
    # Seal the actually completed old response/query source, including failures.
    for base in('results/shared','results/reviews'):
        for p in(OLD/base).glob('**/*.json'):refs[str(p)]=sha(p)
    for p in[OLD/'freeze.json',OLD/'completion.json',OLD/'post/aggregate.json',OLD/'post/rows.json']:refs[str(p)]=sha(p)
    for c,n in units():
        unit=f'{c["id"]}_r{n}';p=OLD/'results/shared'/unit/'shared.json';shared=load(p);assert shared['complete'],'Supplement must not conceal a failed prefix'
        write_new(ROOT/'results/shared'/unit/'shared.json',shared)
        p=OLD/'results/reviews'/f'{c["id"]}_INITIAL_r{n}'/'review.result.json';r=load(p);write_new(ROOT/'results/reviews'/r['review_id']/'review.result.json',r)
    for p in(OLD/'results').glob('**/request.json'):
        receipt=p.parent/'initial.result.json'
        if not receipt.exists()or load(receipt)['status']!='ok':continue
        key=str(p.parent.relative_to(OLD));d=dict(request=str(p),request_sha256=sha(p),provider_result=str(receipt),provider_sha256=sha(receipt))
        if p.parent.name=='final':
            delivery=p.parents[1]/'final.delivered_evidence.json'
            if not delivery.exists():continue
            d.update(delivery=str(delivery),delivery_sha256=sha(delivery))
        calls[key]=d
    write_new(ROOT/'prepared/exact_reuse.json',dict(calls=calls,old_complete=True,selection='Same logical unit and stage with exact request; no correctness-based selection',references=refs,recorded_at=time.time()))
    prior=load(ROOT/'prepared/previous_seals.json');prior['files'].update(refs)
    # This preparation file is not yet frozen and no new model has run.
    (ROOT/'prepared/previous_seals.json').write_text(json.dumps(prior,ensure_ascii=False,indent=2)+'\n')
    return dict(reused_prefixes=36,eligible_exact_model_calls=len(calls),sealed_old_files=len(refs))

def replay_preflight():
    from causal_loop import cases,units
    from loop_policy import replay
    checks=[]
    for c,n in units():
        unit=f'{c["id"]}_r{n}';s=load(OLD/'results/shared'/unit/'shared.json');extra=load(OLD/'results/shared'/unit/'extra_replan.json')
        f=replay(c,s);row=dict(case_id=c['id'],repeat=n,prefix_reproduced=True,third_reproduced=False)
        p=OLD/'results/reviews'/f'{c["id"]}_FOUR_QUERY_r{n}'/'query_round_trace.json'
        if extra['payload'] is not None and p.exists()and load(p).get('additional_round')is not None:
            record=load(p)['additional_round'];core=record['core'];rebuilt=f.execute_plan(plan_only(extra['payload']),RecordedAnswers(core),enable_queries=True)
            assert digest(rebuilt)==digest(core);attach(f,record['context']['evidence']);row['third_reproduced']=True
        checks.append(row)
    write_new(ROOT/'audit/actual_receipt_replay.json',dict(passed=True,checks=checks,model_calls=0,native_calls=0,scope='Exact recorded controller/query replay; no causal-accuracy guarantee.'))
    return dict(passed=True,prefixes=len(checks),thirds=sum(x['third_reproduced']for x in checks))
if __name__=='__main__':print(json.dumps(prepare()))
