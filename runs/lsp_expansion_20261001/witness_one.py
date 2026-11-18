"""Execute real package implementations; compatibility/transport boundaries explicit."""
import collections,collections.abc,inspect,json,logging,os,sys,tempfile,traceback
from pathlib import Path
from types import SimpleNamespace,ModuleType
root=Path(sys.argv[1]); iid=sys.argv[2]
for name in ['Mapping','MutableMapping','Sequence','Iterable','Callable','MutableSet']:
 if not hasattr(collections,name):setattr(collections,name,getattr(collections.abc,name))
if not hasattr(inspect,'getargspec'):inspect.getargspec=inspect.getfullargspec
sys.path.insert(0,str(root/'src' if (root/'src/_pytest').exists() else root))
result={'instance_id':iid,'compatibility':'Python3.11 legacy collections aliases and inspect.getargspec alias; no patched production behavior.'}
try:
 if iid.startswith('psf'):
  import requests
  result['module_file']=requests.__file__
  if iid.endswith('1921'):
   s=requests.Session();s.headers['Accept-Encoding']=None;p=s.prepare_request(requests.Request('GET','http://example.com/'));value={'header_present':'Accept-Encoding' in p.headers}
  elif iid.endswith('5414'):
   try:requests.Request('GET','http://.example.com').prepare();value={'exception':None}
   except Exception as e:value={'exception':type(e).__name__}
  elif iid.endswith('2931'):
   try:
    p=requests.Request('PUT','http://example.com',data='ööö'.encode()).prepare();value={'type':type(p.body).__name__,'body_hex':p.body.hex() if isinstance(p.body,bytes) else str(p.body),'exception':None}
   except Exception as e:value={'exception':type(e).__name__}
  else:
   s=requests.Session();s.send=lambda req,**kw:req.method;value={'prepared_method':s.request(b'GET','http://example.com')};result['boundary']='Actual Session.request and preparation; Session.send returns prepared method, no network.'
 elif iid.startswith('pytest'):
  version=ModuleType('_pytest._version');version.version='4.5.0' if iid.endswith('5262') else '7.2.0' if iid.endswith('10051') else '6.0.0';version.version_tuple=tuple(int(x) for x in version.version.split('.'));sys.modules['_pytest._version']=version
  result['build_metadata_shim']='In-memory generated _pytest._version from SWE-bench version; source files unchanged.'
  import _pytest
  result['module_file']=_pytest.__file__
  if iid.endswith('5262'):
   from _pytest.capture import EncodedFile
   with tempfile.TemporaryFile(mode='w+b') as f:value={'mode':EncodedFile(f,'utf-8').mode}
  elif iid.endswith('7521'):
   from _pytest.capture import FDCapture
   obs=[]
   for text in [b'test\r\n',b'test\r']:
    c=FDCapture(1)
    try:c.start();os.write(1,text);obs.append(c.snap())
    finally:c.done()
   value={'outputs':obs}
  else:
   import _pytest.logging as m
   h=m.LogCaptureHandler(); store={m.caplog_handler_key:h,m.caplog_records_key:{'setup':[],'call':h.records}}
   item=SimpleNamespace(stash=store,_store=store)
   c=m.LogCaptureFixture(item,**({'_ispytest':True} if iid.endswith('10051') else {}))
   result['boundary']='Actual fixture and handler; minimal item stash/_store dictionary instead of pytest runner lifecycle.'
   if iid.endswith('10051'):
    h.emit(logging.LogRecord('probe',logging.INFO,'probe',1,'first',(),None));c.clear();h.emit(logging.LogRecord('probe',logging.INFO,'probe',1,'after',(),None));value={'call_records':[x.getMessage() for x in c.get_records('call')],'handler_records':[x.getMessage() for x in h.records]}
   else:
    c.set_level(41);c._finalize();value={'handler_level_after_finalize':h.level}
 else:
  import sympy
  result['module_file']=sympy.__file__
  if iid.endswith('22914'):
   from sympy import symbols,Min,Max
   from sympy.printing.pycode import PythonCodePrinter
   x,y=symbols('x y');p=PythonCodePrinter();value={'min':p.doprint(Min(x,y)),'max':p.doprint(Max(x,y))}
  elif iid.endswith('23950'):
   from sympy import Contains,Symbol,S
   try:value={'set':str(Contains(Symbol('x'),S.Reals).as_set()),'exception':None}
   except Exception as e:value={'exception':type(e).__name__}
  elif iid.endswith('23534'):
   from sympy import symbols,Function
   value={'class':type(symbols(('q:2','u:2'),cls=Function)[0][0]).__name__}
  else:
   from sympy.physics.vector import ReferenceFrame
   a=ReferenceFrame('A')
   try:value={'equal':bool(a.x+0==a.x),'exception':None}
   except Exception as e:value={'exception':type(e).__name__}
 result.update(ok=True,observation=value)
 assert Path(result['module_file']).resolve().is_relative_to(root.resolve()),'Imported installed package instead of target checkout'
except Exception as e:result.update(ok=False,error=repr(e),traceback=traceback.format_exc())
print(json.dumps(result,ensure_ascii=False))
