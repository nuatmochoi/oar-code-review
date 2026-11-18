"""Immutable per-stage methods and prepared inputs, before any model dispatch."""
import json,time,os
from pathlib import Path
from common import ROOT,PROJECT,load,sha,write_new,BUDGETS

def preserve():
    m=load(ROOT/'prepared/previous_seals.json')
    for p,h in m['files'].items():assert sha(p)==h,('Prior result changed',p)
    old=PROJECT/'runs/review_discovery_paths_20261104_v2';seal=load(old/'completion.json')
    for p,h in seal['files'].items():assert sha(old/p)==h,('Old completed file changed',p)
    return dict(prior_files=len(seal['files']),prior_seals=len(m['files']))

def freeze():
    from causal_loop import cases,ARMS
    from loop_policy import initial_schema
    from diff48 import diff_evidence
    from causal_loop import prompt
    reviewer_input=None
    assert not(ROOT/'freeze.json').exists()
    assert not list((ROOT/'results').glob('**/initial.result.json'))
    assert load(ROOT/'audit/tests.json')['passed']
    assert load(ROOT/'audit/workflow_smoke.json')['passed']
    assert load(ROOT/'audit/native_smoke.json')['passed']
    assert load(ROOT/'audit/actual_receipt_replay.json')['passed']
    preserve();admissions=[]
    from openai_transport import admission,credentials_ready
    credentials_ready()
    for c in cases():
        context=load(ROOT/'contexts'/c['id']/'context_LSP.json');es=diff_evidence(c['diff'])+context['evidence'];schema,cat=initial_schema(es)
        raw=prompt(c,es,{},'Prospective initial review+linked questions.',cat)
        v=admission(raw,schema,final_reserve=65000);assert v['admitted'],(c['id'],v)
        admissions.append(dict(case_id=c['id'],**v))
    write_new(ROOT/'audit/admissions.json',admissions)
    files={}
    for base,dirs,names in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in ['cache','logs','results','evaluation','grades','__pycache__','.pytest_cache','.pycache','post']]
        for name in names:
            p=Path(base)/name
            if p.is_file():files[str(p.relative_to(ROOT))]=sha(p)
    # Aggregate code is part of frozen evaluation, not a post-hoc analysis choice.
    for p in(ROOT/'post').glob('*.py'):files[str(p.relative_to(ROOT))]=sha(p)
    write_new(ROOT/'freeze.json',dict(frozen_at=time.time(),files=files,budgets=BUDGETS,stage=3,arms=ARMS,repeats=3,cases=12,planned_reviews=144,new_model_calls_before_freeze=0,source_only=True,no_gold_to_reviewer=True,no_runtime=True,no_scip=True,location_ID_decoding_lossless=True,unmatched_not_automatically_false=True))
    return verify()

def verify():
    f=load(ROOT/'freeze.json')
    for p,h in f['files'].items():assert sha(ROOT/p)==h,('Frozen input/code changed',p)
    return dict(files=len(f['files']),freeze_sha256=sha(ROOT/'freeze.json'))

if __name__=='__main__':print(json.dumps(freeze()))
