import asyncio,collections,collections.abc,copy,functools,inspect,json,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve();iid=sys.argv[2];sys.path.insert(0,str(root/'src' if (root/'src').is_dir() else root))
for n in ['Mapping','MutableMapping','Sequence','Iterable','Callable','MutableSet']:
 if not hasattr(collections,n):setattr(collections,n,getattr(collections.abc,n))
if not hasattr(inspect,'getargspec'):inspect.getargspec=inspect.getfullargspec
if iid=='pytest-dev__pytest-7324':
 import types
 version_stub=types.ModuleType('_pytest._version');version_stub.version='0+research';sys.modules['_pytest._version']=version_stub
result={'instance_id':iid,'ok':False}
try:
 package=iid.split('__')[1].rsplit('-',1)[0];module=__import__(package);assert Path(module.__file__).resolve().is_relative_to(root),(module.__file__,root)
 result['imported_file']=module.__file__
 if package=='django':
  from django.conf import settings
  settings.configure(SECRET_KEY='research-fixture',USE_I18N=False,USE_TZ=False,DEFAULT_CHARSET='utf-8',INSTALLED_APPS=[])
 try:
  if iid=='django__django-11119':
   from django.template import Engine
   e=Engine(autoescape=False,loaders=[('django.template.loaders.locmem.Loader',{'t':'{{ value }}'})]);v=e.render_to_string('t',{'value':'<b>'});obs={'value':v,'contract':v=='<b>'}
  elif iid=='sympy__sympy-11618':
   from sympy import Point,sqrt
   v=Point(2,0).distance(Point(1,0,2));obs={'value':str(v),'contract':v==sqrt(5)}
  elif iid=='django__django-11133':
   from django.http import HttpResponse
   v=HttpResponse(memoryview(b'My Content')).content;obs={'value':repr(v),'contract':v==b'My Content'}
  elif iid=='django__django-11880':
   from django.forms import CharField
   f=CharField(error_messages={'required':'original'});g=copy.deepcopy(f);g.error_messages['required']='changed';obs={'original_message':str(f.error_messages['required']),'contract':f.error_messages['required']=='original'}
  elif iid=='django__django-14787':
   from django.utils.decorators import method_decorator
   def deco(f):
    @functools.wraps(f)
    def wrapped(*a,**kw):return f.__name__,f(*a,**kw)
    return wrapped
   class T:
    @method_decorator(deco)
    def hello(self):return 'hello'
   v=T().hello();obs={'value':repr(v),'contract':v==('hello','hello')}
  elif iid=='django__django-16136':
   from django.views import View
   from django.http import HttpResponse,HttpRequest
   class Demo(View):
    async def post(self,request):return HttpResponse('ok')
   req=HttpRequest();req.method='GET';req.path='/demo'
   async def go():return (await Demo.as_view()(req)).status_code
   v=asyncio.run(go());obs={'status_code':v,'contract':v==405}
  elif iid=='django__django-16485':
   from decimal import Decimal
   from django.template.defaultfilters import floatformat
   v=[str(floatformat(x,0)) for x in ['0.00',Decimal('0.00')]];obs={'value':v,'contract':v==['0','0']}
  elif iid=='sympy__sympy-16450':
   from sympy import Symbol,posify
   xp,_=posify(Symbol('x',finite=True));obs={'finite':xp.is_finite,'contract':xp.is_finite is True}
  elif iid=='sympy__sympy-15017':
   from sympy import Array
   a=Array(3);obs={'length':len(a),'iteration_length':len(list(a)),'contract':len(a)==len(list(a))==1}
  elif iid=='sympy__sympy-19346':
   from sympy import Symbol,srepr
   x,y=Symbol('x'),Symbol('y');v=srepr({x:y});obs={'value':v,'contract':v=="{Symbol('x'): Symbol('y')}"}
  elif iid=='sympy__sympy-24562':
   from sympy import Rational
   v=Rational('0.5','100');obs={'value':str(v),'contract':v==Rational(1,200)}
  elif iid=='sympy__sympy-17655':
   from sympy import Point,sympify
   a,b=Point(0,0),Point(1,1);l=a+sympify(2.0)*b;r=a+b*sympify(2.0);obs={'left':str(l),'right':str(r),'contract':l==r}
  elif iid=='pytest-dev__pytest-7324':
   from _pytest.mark.expression import Expression
   visited=[];v=Expression.compile('False').evaluate(lambda x:visited.append(x) or True);obs={'value':v,'visited':visited,'contract':v is True and visited==['False']}
  elif iid=='pallets__flask-5014':
   from flask import Blueprint
   try:Blueprint('',__name__);obs={'raised':False,'contract':False}
   except ValueError as exc:obs={'raised':True,'type':type(exc).__name__,'contract':True}
  else:raise AssertionError(iid)
 except Exception as exc:obs={'exception':type(exc).__name__,'message':str(exc),'contract':False}
 result.update(ok=True,observation=obs)
except Exception as exc:
 import traceback;result.update(error=repr(exc),traceback=traceback.format_exc())
print(json.dumps(result))
