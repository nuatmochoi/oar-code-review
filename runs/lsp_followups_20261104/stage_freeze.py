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
    from stage1 import cases,reviewer_input,ARMS
    assert not(ROOT/'freeze.json').exists()
    assert not list((ROOT/'results').glob('**/initial.result.json'))
    assert load(ROOT/'audit/tests.json')['passed']
    preserve();admissions=[]
    from openai_transport import admission,credentials_ready
    credentials_ready()
    for c in cases():
        for a in ARMS:
            packet=load(ROOT/'contexts'/c['id']/f'{a}.json');raw,schema,es,cat=reviewer_input(c,packet)
            v=admission(raw,schema);assert v['admitted'],(c['id'],a,v)
            admissions.append(dict(case_id=c['id'],arm=a,**v))
    write_new(ROOT/'audit/admissions.json',admissions)
    files={}
    for base,dirs,names in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in ['cache','logs','results','evaluation','grades','__pycache__','.pytest_cache','.pycache','post']]
        for name in names:
            p=Path(base)/name
            if p.is_file():files[str(p.relative_to(ROOT))]=sha(p)
    # Aggregate code is part of frozen evaluation, not a post-hoc analysis choice.
    for p in(ROOT/'post').glob('*.py'):files[str(p.relative_to(ROOT))]=sha(p)
    write_new(ROOT/'freeze.json',dict(frozen_at=time.time(),files=files,budgets=BUDGETS,stage=1,arms=ARMS,repeats=3,cases=12,planned_reviews=216,new_model_calls_before_freeze=0,source_only=True,no_gold_to_reviewer=True,no_runtime=True,no_scip=True,location_ID_decoding_lossless=True,unmatched_not_automatically_false=True))
    return verify()

def verify():
    f=load(ROOT/'freeze.json')
    for p,h in f['files'].items():assert sha(ROOT/p)==h,('Frozen input/code changed',p)
    return dict(files=len(f['files']),freeze_sha256=sha(ROOT/'freeze.json'))

if __name__=='__main__':print(json.dumps(freeze()))
