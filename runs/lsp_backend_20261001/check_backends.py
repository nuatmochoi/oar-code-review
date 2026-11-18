"""Position-identical microbenchmark of genuine LSP and conservative AST bindings."""
import ast,json
from pathlib import Path
from lsp_client import Client
from backend import old,ASTBackend,LSPBackend,position
R=Path(__file__).resolve().parent;root=R/'resolver_workspace';root.mkdir(exist_ok=True)
sources={
 'service.py':'class C:\n    def run(self):\n        return "C"\n\nclass D:\n    def run(self):\n        return "D"\n',
 'public_api.py':'from service import C as Exported\n',
 'queries.py':'''from service import C, D
from public_api import Exported
class Child(C):
    pass
class Wrapper:
    @property
    def engine(self) -> C:
        return C()
def direct():
    return C().run()
def typed_parameter(obj: C):
    return obj.run()
def inherited():
    return Child().run()
def reexport():
    return Exported().run()
def property_receiver():
    return Wrapper().engine.run()
def conditional_factory(flag) -> C:
    if flag:
        return C()
    return C()
def returned_type(flag):
    return conditional_factory(flag).run()
def unknown_parameter(obj):
    return obj.run()
def union_parameter(obj: C | D):
    return obj.run()
def shadowing(run):
    return run()
def unicode_position(obj: C):
    label = "😀"; return obj.run()
'''}
for file,src in sources.items():(root/file).write_text(src)
r=old.Bindings(None,sources);c=Client(root);lsp=LSPBackend(r,c);astb=ASTBackend(r);rows=[]
expect={'direct':['C.run'],'typed_parameter':['C.run'],'inherited':['C.run'],'reexport':['C.run'],'property_receiver':['C.run'],'returned_type':['C.run'],'unknown_parameter':[],'union_parameter':['C.run','D.run'],'shadowing':[],'unicode_position':['C.run']}
try:
 for name,target_names in expect.items():
  def lookup(backend):
   edges=list(backend.resolve_sites('queries.py',name,{}))
   return next(e for e in edges if e['called'].endswith('.run') or (name=='shadowing' and e['called']=='run'))
  a=lookup(astb);b=lookup(lsp);actual=sorted(t[1] for t in b['targets']);assert actual==target_names,(name,actual,target_names,b)
  rows.append(dict(name=name,position=position(sources['queries.py'],b['node']),expected_static_definitions=target_names,AST=[list(t) for t in a['targets']],LSP=[list(t) for t in b['targets']],lsp_raw=b['raw_result'],matches=actual==target_names,runtime_binding_proven=name not in ['typed_parameter','unknown_parameter','union_parameter','returned_type','unicode_position']))
finally:c.close()
(R/'resolver_tests.json').write_text(json.dumps({'pyright_version':'1.1.414','passed':len(rows),'rows':rows,'note':'Declared source fixtures. Union returns both static candidates; typed annotation is not runtime enforcement. No production performance claim.'},ensure_ascii=False,indent=2)+'\n')
(R/'resolver_trace.json').write_text(json.dumps({'initialization':c.initialization,'trace':c.trace,'stderr':c.errors},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'passed':len(rows),'AST_expected_match':sum(sorted(t[1] for t in row['AST'])==row['expected_static_definitions'] for row in rows),'LSP_expected_match':sum(row['matches'] for row in rows)},ensure_ascii=False))
