import collections,collections.abc,inspect,json,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve();iid=sys.argv[2]
for name in ['Mapping','MutableMapping','Sequence','Iterable','Callable','MutableSet']:
 if not hasattr(collections,name):setattr(collections,name,getattr(collections.abc,name))
if not hasattr(inspect,'getargspec'):inspect.getargspec=inspect.getfullargspec
sys.path.insert(0,str(root));result={'instance_id':iid,'compatibility':'Python3.11 collections/inspect aliases; no production changes; snapshot source imported first.'}
try:
 import sympy
 assert Path(sympy.__file__).resolve().is_relative_to(root)
 result['module_file']=sympy.__file__;result['version']=sympy.__version__
 try:
  if iid.endswith('17139'):
   x=sympy.Symbol('x');expr=sympy.cos(x)**sympy.I;value=sympy.simplify(expr);observation={'returned':True,'exception':None,'unchanged':value==expr,'repr':str(value),'is_real_I':sympy.I.is_real}
  else:
   fn=sympy.lambdify([], (1,), modules='math');value=fn();observation={'returned':True,'exception':None,'type':type(value).__name__,'equals_singleton_tuple':value==(1,),'repr':repr(value),'generated_source':inspect.getsource(fn)}
 except Exception as exc:observation={'returned':False,'exception':type(exc).__name__,'message':str(exc)}
 result.update(ok=True,observation=observation)
except Exception as exc:
 import traceback;result.update(ok=False,error=repr(exc),traceback=traceback.format_exc())
print(json.dumps(result))
