"""Five preselected actual initial reviews; repair acquisition only."""
from concurrent.futures import ProcessPoolExecutor,as_completed
import copy,json
from common import ROOT,PROJECT,WORKERS,load,sha,write_new
import reexplore_run as r
from freeze_small import verify

OLD=PROJECT/'runs/openai_api_reexplore48_v3_20261104'
def units():return load(ROOT/'prepared/units.json')['units']
def expected():return [f'{u["case_id"]}_{a}_r{u["repeat"]}'for u in units()for a in('INITIAL','BEFORE','AGENTIC')]

def run():
    verify();cases={c['id']:c for c in load(ROOT/'prepared/cases.json')};jobs=[(cases[u['case_id']],u['repeat'])for u in units()]
    for case,n in jobs:
        src=OLD/'results/reviews'/f'{case["id"]}_AGENTIC_r{n}'/'review.result.json';dest=ROOT/'results/reviews'/f'{case["id"]}_BEFORE_r{n}'/'review.result.json'
        if not dest.exists():
            row=copy.deepcopy(load(src));row.update(arm='BEFORE',review_id=dest.parent.name,prior_actual_result_reference={'path':str(src),'sha256':sha(src)},new_physical_model_in_this_run=False)
            write_new(dest,row)
    with ProcessPoolExecutor(max_workers=WORKERS)as pool:
        for f in as_completed([pool.submit(r.safe_shared,*job)for job in jobs]):f.result()
    for case,n in jobs:r.copy_initial(case,n)
    with ProcessPoolExecutor(max_workers=WORKERS)as pool:
        for f in as_completed([pool.submit(r.safe_branch,case,n,'AGENTIC')for case,n in jobs]):f.result()
    assert all((ROOT/'results/reviews'/rid/'review.result.json').exists()for rid in expected())
    write_new(ROOT/'post/reviewers_complete.json',dict(complete=True,reviews=15,units=5))

if __name__=='__main__':run()
