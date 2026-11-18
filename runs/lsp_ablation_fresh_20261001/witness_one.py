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
  import requests
  from requests.utils import prepend_scheme_if_needed
  r['module_file']=requests.__file__
  out={u:prepend_scheme_if_needed(u,'http') for u in ['http://user:pass@example.com/path?query','http://user@example.com/path?query']}
  r['boundary']='Actual URL helper, no network/proxy status inference; official public regression-test inputs.'
 elif iid.startswith('pytest'):
  v=ModuleType('_pytest._version');v.version='7.2.0';v.version_tuple=(7,2,0);sys.modules['_pytest._version']=v
  import pytest;r['module_file']=pytest.__file__;os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
  if iid.endswith('10356'):
   from _pytest.mark.structures import get_unpacked_marks
   @pytest.mark.xfail('a')
   class A:pass
   @pytest.mark.xfail('b')
   class B:pass
   @pytest.mark.xfail('c')
   class C(A,B):pass
   out={'marks':[m.args[0] for m in get_unpacked_marks(C)]}
   r['boundary']='Actual decorator/store_mark/get_unpacked_marks, official three-class MRO fixture.'
  else:
   with tempfile.TemporaryDirectory(prefix='lsp-fresh-xfail-') as d:
    d=Path(d);test=d/'test_dynamic.py';test.write_text('import pytest\ndef test_dynamic(request):\n    request.node.add_marker(pytest.mark.xfail(reason="xfail"))\n    assert 0\n')
    reports=[]
    class Capture:
     def pytest_runtest_logreport(self,report):
      if report.when=='call':reports.append({'outcome':report.outcome,'wasxfail':getattr(report,'wasxfail',None)})
    code=pytest.main([str(test),'-q','--assert=plain','-p','no:cacheprovider','--confcutdir',str(d)],plugins=[Capture()]);out={'pytest_exit':int(code),'call_reports':reports}
   r['boundary']='Actual target pytest runner; dynamic xfail official failing fixture; --assert=plain bypasses old AST-rewrite incompatibility with Python3.11; external plugins disabled.'
 else:
  import sympy;r['module_file']=sympy.__file__
  if iid.endswith('12096'):
   from sympy.utilities.lambdify import implemented_function
   f=implemented_function('f',lambda x:x**2);g=implemented_function('g',lambda x:2*x)
   out={'composed':str(f(g(2)).evalf()),'direct':str(f(2).evalf())}
  elif iid.endswith('14531'):
   from sympy import Symbol,S,Eq,Limit,sstr
   x=Symbol('x');out={'Eq':sstr(Eq(x,S(1)/2),sympy_integers=True),'Limit':sstr(Limit(x,x,S(1)/2),sympy_integers=True)}
  else:
   from sympy.utilities.iterables import partitions
   pp=list(partitions(6,k=2));out={'partitions':pp,'distinct_objects':len({id(p) for p in pp})}
  r['boundary']='Actual target SymPy public functions; deterministic issue/doc example inputs.'
 assert Path(r['module_file']).resolve().is_relative_to(root.resolve())
 r.update(ok=True,observation=out)
except Exception as e:
 import traceback;r.update(ok=False,error=repr(e),traceback=traceback.format_exc())
print(json.dumps(r))
