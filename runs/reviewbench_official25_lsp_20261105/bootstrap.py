from pathlib import Path
import ast, hashlib,json,shutil
root=Path(__file__).resolve().parent
project=root.parents[1]
old=project/'runs/lsp_followups_20261104'
ad=root/'adapter';vendor=ad/'vendor';vendor.mkdir(exist_ok=True)
names=['context48','symbol_context','bound_context','search_context','common','oar_core','diff48','schemas','location_ids','warning_tracker','warning_schema']
provenance={}
for n in names:
 src=old/(n+'.py');dst=vendor/src.name
 shutil.copy2(src,dst);provenance[str(src)]=hashlib.sha256(src.read_bytes()).hexdigest()
for name in ['context48','common']:
 p=vendor/(name+'.py');s=p.read_text().replace('ROOT=Path(__file__).resolve().parent','ROOT=Path(__import__("os").environ.get("REVIEW_RUN_ROOT","/work/out/agent")).resolve()').replace('PROJECT=ROOT.parents[1]','PROJECT=Path("/opt/agent")')
 if name=='context48':
  s=s.replace("PRODUCT=PROJECT/'runs/oar_product_pilot_v3_20261026/product_snapshot_20261026'","PRODUCT=Path('/opt/agent/product')").replace("GO_PARSER=PROJECT/'runs/causal_route_separation_20261103/go_flow/go-flow'","GO_PARSER=Path('/opt/agent/vendor/tools/frontier-go')").replace("GOPLS=PROJECT/'tools/bin/gopls'","GOPLS=Path('/usr/local/bin/gopls')").replace("PYRIGHT=PROJECT/'tools/pyright-lsp-20261023/node_modules/.bin/pyright-langserver'","PYRIGHT=Path('/usr/local/bin/pyright-langserver')")
 p.write_text(s)
(vendor/'tools').mkdir(exist_ok=True)
shutil.copy2(old/'tools/frontier-go.go',vendor/'tools/frontier-go.go')
product=project/'runs/oar_product_pilot_v3_20261026/product_snapshot_20261026/review_agent'
shutil.copytree(product,ad/'product/review_agent',ignore=shutil.ignore_patterns('__pycache__','*.pyc'),dirs_exist_ok=True)
tree=ast.parse((old/'stage1.py').read_text());nodes=[]
for n in tree.body:
 if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name)and t.id=='SYSTEM' for t in n.targets):nodes.append(n)
 if isinstance(n,ast.FunctionDef)and n.name=='reviewer_input':nodes.append(n)
s='import copy\nfrom common import render_evidence\nfrom diff48 import diff_evidence\nfrom location_ids import wire_schema,render_catalog\n'+ast.unparse(ast.Module(nodes,type_ignores=[]))+'\n'
offline_tree=ast.parse((project/'ace_review/isolated_review.py').read_text())
for n in offline_tree.body:
 if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name)and t.id=='OFFLINE_INSTRUCTION' for t in n.targets):s+=ast.unparse(n)+'\n'
(ad/'review_prompt.py').write_text(s)
(root/'audit/source_provenance.json').write_text(json.dumps(provenance,indent=2))
