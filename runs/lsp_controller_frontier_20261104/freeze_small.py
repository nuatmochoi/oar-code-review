"""Freeze this development regression before any fresh model request."""
import os,time,json
from pathlib import Path
from common import ROOT,load,sha,write_new,BUDGETS,ARMS,MODEL

def verify():
    f=load(ROOT/'freeze.json')
    for path,h in f['files'].items():assert sha(ROOT/path)==h,('Frozen file changed',path)
    for path,h in load(ROOT/'prepared/previous_references.json')['files'].items():assert sha(path)==h,('Prior artifact changed',path)
    return dict(files=len(f['files']),freeze_sha256=sha(ROOT/'freeze.json'))

def freeze():
    assert not(ROOT/'freeze.json').exists()and not list((ROOT/'results').glob('**/initial.result.json'))and not list((ROOT/'evaluation').glob('**/initial.result.json'))
    assert load(ROOT/'audit/native_preflight.json')['passed']and load(ROOT/'audit/test_receipt.json')['passed']and load(ROOT/'audit/initial_reuse_preflight.json')['passed']
    files={}
    for base,dirs,names in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in('cache','logs','results','evaluation','grades','__pycache__','.pytest_cache')]
        for name in names:
            p=Path(base)/name
            if p.is_file()and (p.suffix in('.py','.json','.md','.go')or p.name=='frontier-go')and p.name!='RUN_STATE_KO.md':files[str(p.relative_to(ROOT))]=sha(p)
    write_new(ROOT/'freeze.json',dict(recorded_at=time.time(),files=files,model=MODEL,budgets=BUDGETS,arms=ARMS,selected_units=5,planned_reviews=15,fresh_reviewer_calls_before_freeze=0,fresh_judge_calls_before_freeze=0,scope='Known development regression: same actual initial, same actual initial/first acquisition/finding format; Same claim-scope policy plus controller-owned current-claim/diff/typed-declaration/call/value-consumer static acquisition. Same permitted source roots; actual delivery may differ. No runtime or SCIP. No score-based retries.'))
    return verify()

if __name__=='__main__':print(json.dumps(freeze()))
