"""Post-run supplement: avoid the frozen joint witness's first-exception short circuit."""
import json,subprocess
from pathlib import Path
R=Path(__file__).resolve().parent
py=R.parents[1]/'tools/venv-linked-adaptive-20261023/bin/python'
code=r'''
import collections,collections.abc,inspect,json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
for n in ['Mapping','MutableMapping','Sequence','Iterable','Callable','MutableSet']:
    if not hasattr(collections,n):setattr(collections,n,getattr(collections.abc,n))
if not hasattr(inspect,'getargspec'):inspect.getargspec=inspect.getfullargspec
import django
assert Path(django.__file__).resolve().is_relative_to(root)
from django.conf import settings
settings.configure(SECRET_KEY='research-fixture',USE_I18N=False,USE_TZ=False,DEFAULT_CHARSET='utf-8',INSTALLED_APPS=[])
from decimal import Decimal
from django.template.defaultfilters import floatformat
text='0.00' if sys.argv[2]=='string' else Decimal('0.00')
try:
    value=str(floatformat(text,0));out={'value':value,'contract':value=='0'}
except Exception as exc:
    out={'exception':type(exc).__name__,'message':str(exc),'contract':False}
print(json.dumps({'imported_file':django.__file__,'observation':out}))
'''
rows=[]
for rev in ['original','fixed']:
    for kind in ['string','decimal']:
        for rep in [1,2]:
            p=subprocess.run([str(py),'-c',code,str(R/'checkouts/django__django-16485'/rev),kind],capture_output=True,text=True,timeout=60)
            assert p.returncode==0,p.stderr
            d=json.loads(p.stdout);d.update(revision=rev,input_kind=kind,repeat=rep)
            rows.append(d)
valid=all(x['observation']['contract']==(x['revision']=='fixed') for x in rows)
out={'post_model_run':True,'affects_frozen_inputs_or_labels':False,'reason':'Frozen N05 joint witness stops on the first original-input exception. Verify each input independently without changing the frozen witness or model prompts. Not an additional model experiment.','all_contracts_verified':valid,'rows':rows}
(R/'witness_n05_individual.json').write_text(json.dumps(out,indent=2))
assert valid
print(json.dumps({'individual_input_checks':len(rows),'all_contracts_verified':valid}))
