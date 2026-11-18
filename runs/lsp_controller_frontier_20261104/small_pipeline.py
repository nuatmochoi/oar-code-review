"""Bounded reviewer -> judge -> preservation pipeline. No automatic retries."""
import json,os,subprocess,sys,time
from pathlib import Path
from common import ROOT,PROJECT,load,sha,write_new
from freeze_small import verify

def main():
    verify()
    for name,module,done in(('reviewers','small_run.py',ROOT/'post/reviewers_complete.json'),('judges','small_eval.py',ROOT/'post/small_aggregate.json')):
        if done.exists():continue
        with(ROOT/'logs'/f'{name}.log').open('a')as out:
            result=subprocess.run([sys.executable,'-B',module],cwd=ROOT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},stdout=out,stderr=subprocess.STDOUT)
        assert result.returncode==0 and done.exists(),(name,'failed; no retry; preserve raw log')
    previous_small=PROJECT/'runs/causal_scope_separation_20261104';previous_completion=load(previous_small/'completion.json');assert all(sha(previous_small/p)==h for p,h in previous_completion['files'].items())
    verified=verify();old=PROJECT/'runs/openai_api_reexplore48_v3_20261104';previous=load(old/'audit/stop_after_interim_20261104.json')
    assert all(sha(p)==h for p,h in previous['references'].items())
    frozen=load(old/'freeze.json');assert all(sha(old/p)==h for p,h in frozen['files'].items())
    dependencies=load(old/'dependency_freeze.json')['files'];assert all(sha(p)==h for p,h in dependencies.items())
    assert(old/'results/model_gate/STOP').exists()
    records=[]
    for folder in('results','evaluation'):
        for p in(ROOT/folder).glob('**/initial.result.json'):
            r=load(p);u=r.get('actual_usage',{}).get('total',{});d=u.get('input_tokens_details')or{};cached=d.get('cached_tokens',0);writes=d.get('cache_write_tokens',0)
            cost=(max(0,u['input_tokens']-cached-writes)*4+cached*.4+writes*5+u['output_tokens']*20)/1e6 if all(type(u.get(k))is int for k in('input_tokens','output_tokens'))else None
            records.append(dict(path=str(p),sha256=sha(p),issued=r.get('physical_attempt_count',0),usage=u,standard_price_equivalent_usd=cost))
    write_new(ROOT/'post/new_api_usage.json',dict(records=records,physical_calls=sum(r['issued']for r in records),standard_price_equivalent_usd=sum(r['standard_price_equivalent_usd']or 0 for r in records),unknown_issued=sum(r['issued']and r['standard_price_equivalent_usd']is None for r in records),scope='New API reviewer and judge calls only; reused actual initial/before stages remain in logical cost. Rate equivalent, not invoice/balance.'))
    refs={}
    for base,dirs,names in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in('cache','logs','model_gate','__pycache__','.pytest_cache')]
        for name in names:
            p=Path(base)/name
            if p.is_file()and p.name not in('completion.json','RUN_STATE_KO.md'):refs[str(p.relative_to(ROOT))]=sha(p)
    aggregate=load(ROOT/'post/small_aggregate.json');paper=PROJECT/'paper/LSP_CONTROLLER_FRONTIER_20261104_KO.md'
    write_new(ROOT/'completion.json',dict(experiment_complete=True,development_regression_passed=aggregate['regression_gates']['development_regression_passed'],selected_units=5,planned_reviews=15,freeze=verified,previous_small_outputs_unchanged=len(previous_completion['files']),old_outputs_unchanged=len(previous['references']),old_frozen_files_unchanged=len(frozen['files']),old_external_references_unchanged=len(dependencies),files=refs,report=str(paper),report_sha256=sha(paper),recorded_at=time.time(),scope='Completed small development regression; original48 expansion stays stopped; no claim of held-out/generalization performance.'))
    print(json.dumps(aggregate['regression_gates']),flush=True)

if __name__=='__main__':main()
