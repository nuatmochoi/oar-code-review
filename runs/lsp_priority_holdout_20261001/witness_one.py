import collections,collections.abc,inspect,json,os,sys,tempfile
from pathlib import Path
from types import ModuleType
root=Path(sys.argv[1]);iid=sys.argv[2]
for n in ['Mapping','MutableMapping','Sequence','Iterable','Callable','MutableSet']:
 if not hasattr(collections,n):setattr(collections,n,getattr(collections.abc,n))
if not hasattr(inspect,'getargspec'):inspect.getargspec=inspect.getfullargspec
sys.path.insert(0,str(root/'src' if (root/'src/_pytest').exists() else root))
r={'instance_id':iid,'compatibility':'Legacy Python3.11 collections/inspect aliases; generated pytest version metadata injected in memory only.'}
try:
 if iid.startswith('psf'):
  import requests;r['module_file']=requests.__file__;out={}
  for method in ['GET','HEAD']:
   p=requests.Request(method,'http://example.com').prepare();out[method]=p.headers.get('Content-Length')
  r['boundary']='Actual Request.prepare, no network.'
 elif iid.startswith('pytest'):
  v=ModuleType('_pytest._version');v.version='7.2.0';v.version_tuple=(7,2,0);sys.modules['_pytest._version']=v
  import pytest;r['module_file']=pytest.__file__;os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
  with tempfile.TemporaryDirectory(prefix='lsp-holdout-skip-') as d:
   d=Path(d);flag=d/'torn_down';test=d/'test_skipped.py';test.write_text('import unittest\nfrom pathlib import Path\n@unittest.skip("skip class")\nclass TestSkipped(unittest.TestCase):\n    def test_x(self):\n        pass\n    def tearDown(self):\n        Path('+repr(str(flag))+').write_text("called")\n')
   code=pytest.main([str(test),'--pdb','-q','-p','no:cacheprovider','--confcutdir',str(d)]);out={'pytest_exit':int(code),'tearDown_called':flag.exists()}
  r['boundary']='Actual target pytest runner with --pdb and class-level unittest.skip; auto plugins disabled; temporary teardown marker. No failing test/PDB interaction.'
 else:
  import sympy;r['module_file']=sympy.__file__
  if iid.endswith('15875'):
   from sympy import I,simplify
   e=-2*I+(1+I)**2;out={'is_zero':e.is_zero,'simplified_is_zero':simplify(e).is_zero}
  else:
   from sympy.core.sympify import kernS
   try:out={'expression':str(kernS('(2*x)/(x-1)')),'exception':None}
   except Exception as e:out={'exception':type(e).__name__,'message':str(e)}
 assert Path(r['module_file']).resolve().is_relative_to(root.resolve())
 r.update(ok=True,observation=out)
except Exception as e:
 import traceback;r.update(ok=False,error=repr(e),traceback=traceback.format_exc())
print(json.dumps(r))
