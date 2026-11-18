import collections,collections.abc,contextlib,inspect,io,json,os,sys,tempfile
from pathlib import Path
from types import ModuleType
root=Path(sys.argv[1]);iid=sys.argv[2]
for n in ['Mapping','MutableMapping','Sequence','Iterable','Callable','MutableSet']:
 if not hasattr(collections,n):setattr(collections,n,getattr(collections.abc,n))
if not hasattr(inspect,'getargspec'):inspect.getargspec=inspect.getfullargspec
sys.path.insert(0,str(root/'src'));v=ModuleType('_pytest._version');v.version='6.2.0';v.version_tuple=(6,2,0);sys.modules['_pytest._version']=v;os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1';r={'instance_id':iid,'compatibility':'Python3.11 collections/inspect aliases and in-memory generated version metadata. --assert=plain; no external plugins.'}
try:
 import pytest;import _pytest;_pytest._version=v;r['module_file']=pytest.__file__;assert Path(pytest.__file__).resolve().is_relative_to(root.resolve())
 with tempfile.TemporaryDirectory(prefix='auto-route-holdout-') as td:
  d=Path(td);reports=[]
  class Capture:
   def pytest_runtest_logreport(self,report):reports.append(report)
  def run(args):
   old=os.getcwd();buf=io.StringIO();os.chdir(d)
   try:
    with contextlib.redirect_stdout(buf),contextlib.redirect_stderr(buf):code=pytest.main(args+['--assert=plain','-p','no:cacheprovider','--confcutdir',str(d)],plugins=[Capture()])
   finally:os.chdir(old)
   assert 'INTERNALERROR>' not in buf.getvalue(),buf.getvalue();return int(code),buf.getvalue()
  if iid.endswith('6202'):
   (d/'test_sample.py').write_text('import pytest\n@pytest.mark.parametrize("a", ["..["])\ndef test_boo(a):\n    assert 0\n')
   code,text=run(['test_sample.py','-q']);rep=next(x for x in reports if x.when=='call');out={'exit':code,'head_line':rep.head_line,'nodeid':rep.nodeid,'headline_preserves_parameter':rep.head_line=='test_boo[..[]','failed_calls':sum(x.when=='call' and x.failed for x in reports)}
  else:
   (d/'test_sample.py').write_text('import pytest\n@pytest.mark.parametrize("data", [b"Hello World"])\ndef test_data(data):\n    pass\n')
   code,text=run(['test_sample.py','--setup-show','-q']);out={'exit':code,'passed_calls':sum(x.when=='call' and x.passed for x in reports),'bytes_warning':'BytesWarning' in text,'setup_report_failed':any(x.when=='setup' and x.failed for x in reports),'output':text}
 r.update(ok=True,observation=out)
except Exception as e:
 import traceback;r.update(ok=False,error=repr(e),traceback=traceback.format_exc())
print(json.dumps(r))
