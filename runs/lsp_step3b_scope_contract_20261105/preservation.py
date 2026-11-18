"""Content seals for completed prior experiments; never modifies their files."""
from common import ROOT,PROJECT,load,sha,write_new
OLD_RUNS=('lsp_followups_20261104','lsp_followup2_seeds_20261104','lsp_baseline_variance_20261104','lsp_followup3_replanning_20261104','lsp_followup3_budget400k_20261104','lsp_followup4_candidates_20261104')
def seal():
    dest=ROOT/'audit/prior_results_seal.json'
    if dest.exists():return verify()
    files={}
    for name in OLD_RUNS:
        base=PROJECT/'runs'/name;assert(base/'completion.json').exists()
        for p in[base/'completion.json',base/'freeze.json']:
            files[str(p)]=sha(p)
        for sub in('results','evaluation','grades','post'):
            for p in(base/sub).glob('**/*'):
                if p.is_file()and p.suffix in('.json','.md','.py'):files[str(p)]=sha(p)
    write_new(dest,dict(files=files,runs=list(OLD_RUNS),before_new_models=True,no_prior_files_modified=True));return verify()
def verify():
    value=load(ROOT/'audit/prior_results_seal.json')
    for p,h in value['files'].items():assert sha(p)==h,('Prior completed result changed',p)
    return dict(prior_runs=len(value['runs']),prior_files=len(value['files']))
if __name__=='__main__':
    import json
    print(json.dumps(seal()))
