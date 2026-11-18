"""Freeze method, schemas, budgets and evaluation before fresh model calls."""
import os,time,json
from pathlib import Path
from common import ROOT,PROJECT,load,sha,write_new,BUDGETS,ARMS,MODEL

def verify():
    f=load(ROOT/'freeze.json')
    for p,h in f['files'].items():assert sha(ROOT/p)==h,('Frozen file changed',p)
    for p,h in load(ROOT/'prepared/previous_references.json')['files'].items():assert sha(p)==h,('Prior seal changed',p)
    return dict(files=len(f['files']),freeze_sha256=sha(ROOT/'freeze.json'))

def preserve_previous():
    old=Path(load(ROOT/'prepared/previous_references.json')['prior_root']);done=load(old/'completion.json')
    assert all(sha(old/p)==h for p,h in done['files'].items()),'Previous completed experiment changed'
    original=PROJECT/'runs/openai_api_reexplore48_v3_20261104';stopped=load(original/'audit/stop_after_interim_20261104.json');assert all(sha(p)==h for p,h in stopped['references'].items())
    assert(original/'results/model_gate/STOP').exists()
    dependencies=load(original/'dependency_freeze.json')['files'];assert all(sha(p)==h for p,h in dependencies.items())
    aborted_files=0
    for path in load(ROOT/'prepared/previous_references.json')['files']:
        if Path(path).name=='ABORTED_BEFORE_FAIR_COMPARISON.json':
            a=load(path);base=Path(path).parents[1];assert all(sha(base/p)==h for p,h in a['files'].items());aborted_files+=len(a['files'])
    return dict(aborted_attempt_files=aborted_files,previous_files=len(done['files']),original_outputs=len(stopped['references']),original_dependencies=len(dependencies),original48_stays_stopped=True)

def freeze():
    assert not(ROOT/'freeze.json').exists()and not list((ROOT/'results').glob('**/initial.result.json'))and not list((ROOT/'evaluation').glob('**/initial.result.json'))
    for name in('native_preflight','test_receipt','admission_preflight','judge_delivery_preflight','initial_reuse_preflight'):assert load(ROOT/'audit'/f'{name}.json')['passed']
    preserved=preserve_previous();files={}
    for base,dirs,names in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in('cache','logs','results','evaluation','grades','__pycache__','.pytest_cache')]
        for name in names:
            p=Path(base)/name
            if p.is_file()and(p.suffix in('.py','.json','.md','.go')or p.name=='frontier-go')and p.name!='RUN_STATE_KO.md':files[str(p.relative_to(ROOT))]=sha(p)
    write_new(ROOT/'freeze.json',dict(recorded_at=time.time(),files=files,model=MODEL,budgets=BUDGETS,arms=ARMS,selected_units=5,unique_PRs=4,planned_reviews=20,planned_fresh_reviewer_calls=30,reused_exact_full_diff_initials=5,fresh_reviewer_calls_before_freeze=0,fresh_judge_calls_before_freeze=0,preserved=preserved,scope='Known development regression. Fresh identical full-diff initial shared; R0 common infrastructure+static retrieval, R1 explicit discovery, R2 question-conditioned path selection. Same3 calls/200k reviewer tokens, same native/source/file caps. Native first prefix is newly executed. No runtime/SCIP. Protected identical evaluator fact pool. No score-based retries.'))
    return verify()
if __name__=='__main__':print(json.dumps(freeze()))
