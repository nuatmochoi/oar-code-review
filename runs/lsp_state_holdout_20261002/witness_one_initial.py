import collections,collections.abc,contextlib,inspect,io,json,os,sys,tempfile
from pathlib import Path
from types import ModuleType
root=Path(sys.argv[1]);iid=sys.argv[2]
for n in ['Mapping','MutableMapping','Sequence','Iterable','Callable','MutableSet']:
 if not hasattr(collections,n):setattr(collections,n,getattr(collections.abc,n))
if not hasattr(inspect,'getargspec'):inspect.getargspec=inspect.getfullargspec
sys.path.insert(0,str(root/'src'));v=ModuleType('_pytest._version');v.version='6.2.0';v.version_tuple=(6,2,0);sys.modules['_pytest._version']=v;os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1';r={'instance_id':iid,'compatibility':'Python3.11 collections/inspect aliases and in-memory generated version metadata. --assert=plain; no external plugins.'}
try:
 import pytest;r['module_file']=pytest.__file__;assert Path(pytest.__file__).resolve().is_relative_to(root.resolve())
 with tempfile.TemporaryDirectory(prefix='state-holdout-') as td:
  d=Path(td);reports=[]
  class Capture:
   def pytest_runtest_logreport(self,report):reports.append(report)
  def run(args):
   old=os.getcwd();buf=io.StringIO();os.chdir(d)
   try:
    with contextlib.redirect_stdout(buf),contextlib.redirect_stderr(buf):code=pytest.main(args+['--assert=plain','-p','no:cacheprovider','--confcutdir',str(d)],plugins=[Capture()])
   finally:os.chdir(old)
   return int(code),buf.getvalue()
  if iid.endswith('5787'):
   (d/'test_sample.py').write_text('def foo():\n    raise ValueError("value error")\ndef test_a():\n    try:\n        foo()\n    except ValueError as e:\n        raise RuntimeError("runtime error") from e\n')
   code,_=run(['test_sample.py','-q']);rep=next(x for x in reports if x.when=='call');rep.longrepr.addsection('title','contents','=');before=len(rep.longrepr.chain)
   from _pytest.reports import TestReport
   loaded=TestReport._from_json(rep._to_json());chain=getattr(loaded.longrepr,'chain',None);out={'exit':code,'chain_before':before,'chain_after':len(chain) if chain is not None else None,'sections':loaded.longrepr.sections,'text_has_value_error':'ValueError' in str(loaded.longrepr),'text_has_runtime_error':'RuntimeError' in str(loaded.longrepr)}
  elif iid.endswith('6197'):
   (d/'test_sample.py').write_text('def test():\n    pass\n');(d/'foopkg').mkdir();(d/'foopkg/__init__.py').write_text('assert False\n');code,text=run(['-q']);out={'exit':code,'passed_calls':sum(x.when=='call' and x.passed for x in reports),'package_collection_error':'ERROR collecting foopkg' in text}
  elif iid.endswith('7432'):
   (d/'test_sample.py').write_text('import pytest\n@pytest.mark.skip\ndef test_skip_location():\n    assert 0\n');code,text=run(['test_sample.py','-q','-rs','--runxfail']);skip=next(x for x in reports if x.skipped);out={'exit':code,'skip_file':Path(skip.longrepr[0]).name,'skip_lineno':skip.longrepr[1],'skipped':skip.skipped}
  else:
   (d/'test_sample.py').write_text('import unittest\nclass Tests(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls):\n        pass\n    def test_1(self):\n        pass\n')
   code,text=run(['test_sample.py','--fixtures']);code_v,text_v=run(['test_sample.py','--fixtures','-v']);out={'exit':code,'exit_verbose':code_v,'default_shows_generated_fixture':'setUpClass_fixture_Tests' in text,'verbose_shows_generated_fixture':'setUpClass_fixture_Tests' in text_v}
 r.update(ok=True,observation=out)
except Exception as e:
 import traceback;r.update(ok=False,error=repr(e),traceback=traceback.format_exc())
print(json.dumps(r))
