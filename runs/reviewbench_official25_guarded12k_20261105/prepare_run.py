from pathlib import Path
import ast,base64,hashlib,json,shutil,subprocess,zlib
ROOT=Path(__file__).resolve().parent;OLD=ROOT.parent/'reviewbench_official25_lsp_20261105'
for name in ['adapter']:
 shutil.copytree(OLD/name,ROOT/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'),dirs_exist_ok=True)
for name in ['budget_proxy.py','test_budget.py','cost_postprocess.py','freeze_run.py','run_official.py','run_judge.py','native_audit.py','report_post.py','recover_judge_auth.py']:
 shutil.copy2(OLD/name,ROOT/name)
(ROOT/'audit').mkdir(exist_ok=True);(ROOT/'judge-config').mkdir(exist_ok=True)
for name in ['budget-tests.final-preflight.log','parser-tests.final.log','pi-transport-test.json','toolchain-receipt.json','native-readiness.json','source_provenance.json']:
 shutil.copy2(OLD/'audit'/name,ROOT/'audit'/name)
shutil.copy2(OLD/'judge-config/models.json',ROOT/'judge-config/models.json')
if not(ROOT/'upstream').exists():subprocess.run(['git','clone','--quiet','--local','--no-hardlinks',str(OLD/'upstream'),str(ROOT/'upstream')],check=True)
(ROOT/'upstream/node_modules').symlink_to(OLD/'upstream/node_modules')
(ROOT/'prefixes').mkdir(exist_ok=True);manifest=json.loads((ROOT/'upstream/corpus/test/test.json').read_text());refs={};sizes=[]
for i,e in enumerate(manifest):
 key=e['nwo'].replace('/','_')+'_'+str(e['pr_number'])+'-'+e['head'][:8];source=OLD/'work'/f'w{i%3}'/'out'/key/'agent/lsp.context.json';v=json.loads(source.read_text())
 # Public retrieval only. No prior reviewer output, findings, grades, gold.
 allowed=['case_id','language','evidence','cost','stages','source_tokens','source_pool_sha256','external_source_bindings']
 prefix={k:v[k]for k in allowed if k in v};raw=json.dumps(prefix,ensure_ascii=False,separators=(',',':')).encode();p=ROOT/'prefixes'/f'{i:02d}.json';p.write_bytes(raw)
 token=base64.b64encode(zlib.compress(raw)).decode();sizes.append(len(token));refs[str(source)]=hashlib.sha256(source.read_bytes()).hexdigest()
 refs[str(p)]=hashlib.sha256(raw).hexdigest()
for worker in range(3):
 target=ROOT/'work'/f'w{worker}';target.mkdir(parents=True,exist_ok=True)
 subprocess.run(['cp','-cR',str(OLD/'work'/f'w{worker}'/'repos'),str(target/'repos')],check=True)
subprocess.run(['cp','-cR',str(OLD/'judge-repos'),str(ROOT/'judge-repos')],check=True)
prior_refs=json.loads((OLD/'audit/preservation.json').read_text())['files'];prior_refs.update(refs)
for p in [OLD/'completion.json',OLD/'freeze.json',OLD/'scoring/results.json',OLD/'scoring/results.details.json',OLD/'audit/cost.corrected.json']:prior_refs[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
(ROOT/'audit/preservation.json').write_text(json.dumps({'files':prior_refs,'baseline':str(OLD),'public_prefix_only':True,'max_compressed_prefix_env_bytes':max(sizes)},indent=2))
# Reuse exact selection functions, then add generic tree-sitter statement spans.
source=ROOT.parent/'lsp_followup2_seeds_20261104/static_contexts.py';tree=ast.parse(source.read_text());functions=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in ('intervals','inventory','round_robin','queue')]
text='import ast,copy,time\nfrom collections import defaultdict,deque\nfrom common import digest\nfrom context48 import Limit\n'+ast.unparse(ast.Module(functions,type_ignores=[]))+'\n'
(ROOT/'adapter/guarded_selection.py').write_text(text)
print(json.dumps({'prefixes':25,'max_prefix_env_bytes':max(sizes),'old_results_preserved':True}))
