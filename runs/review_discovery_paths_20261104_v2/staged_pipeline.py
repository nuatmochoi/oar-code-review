"""Bounded reviewers, protected common-source judging and preservation closure."""
import json,os,subprocess,sys,time
from common import ROOT,PROJECT,load,sha,write_new
from freeze_small import verify,preserve_previous

def main():
    verify()
    for module,receipt in(('staged_run.py','post/reviewers_complete.json'),('staged_eval.py','post/staged_aggregate.json')):
        if(ROOT/receipt).exists():continue
        with(ROOT/'logs'/f'{module}.log').open('a')as out:run=subprocess.run([sys.executable,'-B',module],cwd=ROOT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},stdout=out,stderr=subprocess.STDOUT)
        assert run.returncode==0 and(ROOT/receipt).exists(),(module,'failed; preserve logs; no automatic retry')
    verified=verify();prior=preserve_previous();records=[]
    for base in('results','evaluation'):
        for p in(ROOT/base).glob('**/initial.result.json'):
            r=load(p);u=r.get('actual_usage',{}).get('total',{});d=u.get('input_tokens_details')or{};cached=d.get('cached_tokens',0);writes=d.get('cache_write_tokens',0);known=all(type(u.get(k))is int for k in('input_tokens','output_tokens'))
            cost=(max(0,u['input_tokens']-cached-writes)*4+cached*.4+writes*5+u['output_tokens']*20)/1e6 if known else None
            records.append(dict(path=str(p),sha256=sha(p),physical_calls=r.get('physical_attempt_count',0),usage=u,standard_price_equivalent_usd=cost))
    usage=dict(records=records,physical_calls=sum(v['physical_calls']for v in records),standard_price_equivalent_usd=sum(v['standard_price_equivalent_usd']or 0 for v in records),issued_unknown_cost=sum(v['physical_calls']and v['standard_price_equivalent_usd']is None for v in records),scope='Fresh OpenAI reviewer+judge usage, initial shared physically. Frozen rate equivalent, not invoice/balance.')
    abort_path=PROJECT/'runs/review_discovery_paths_20261104/audit/ABORTED_BEFORE_FAIR_COMPARISON.json'
    if abort_path.exists():
        abort=load(abort_path);base=abort_path.parents[1];prior_records=[]
        for path in(base/'results').glob('**/initial.result.json'):
            item=load(path);u=item.get('actual_usage',{}).get('total',{});d=u.get('input_tokens_details')or{};cached=d.get('cached_tokens',0);writes=d.get('cache_write_tokens',0);known=all(type(u.get(k))is int for k in('input_tokens','output_tokens'))
            cost=(max(0,u['input_tokens']-cached-writes)*4+cached*.4+writes*5+u['output_tokens']*20)/1e6 if known else None
            prior_records.append(dict(path=str(path),sha256=sha(path),usage=u,standard_price_equivalent_usd=cost))
        usage['prior_aborted_attempt']=dict(dispatches=abort['dispatches'],received_receipts=len(prior_records),usage_unknown_calls=abort['raw_provider_usage_unknown_for_these'],known_standard_price_equivalent_usd=sum(v['standard_price_equivalent_usd']or 0 for v in prior_records),records=prior_records,initial5_reused_with_no_additional_physical_initial_call=True,semantic_scores_used=False)
        usage['all_attempts_known_standard_price_equivalent_usd']=usage['standard_price_equivalent_usd']+usage['prior_aborted_attempt']['known_standard_price_equivalent_usd']
        usage['all_attempts_usage_unknown_calls']=usage['issued_unknown_cost']+abort['raw_provider_usage_unknown_for_these']
    write_new(ROOT/'post/new_api_usage.json',usage)
    refs={}
    for base,dirs,names in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in('cache','logs','model_gate','__pycache__','.pytest_cache')]
        for name in names:
            p=__import__('pathlib').Path(base)/name
            if p.is_file()and p.name not in('completion.json','RUN_STATE_KO.md'):refs[str(p.relative_to(ROOT))]=sha(p)
    report=PROJECT/'paper/REVIEW_DISCOVERY_PATHS_V2_20261104_KO.md'
    write_new(ROOT/'completion.json',dict(experiment_complete=True,selected_units=5,reviews=20,freeze=verified,preserved=prior,files=refs,report=str(report),report_sha256=sha(report),recorded_at=time.time(),development_only=True,no_automatic_full48_restart=True))
    x=load(ROOT/'post/staged_aggregate.json');print(json.dumps({a:v['cause']for a,v in x['by_arm'].items()}),flush=True)
if __name__=='__main__':main()
