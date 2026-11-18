"""Post-review adjudication check; does not change frozen inputs or primary witnesses."""
import collections,collections.abc,inspect,json,subprocess,sys
from pathlib import Path
R=Path(__file__).resolve().parent
if len(sys.argv)>1:
 for name in ['Mapping','MutableMapping','Sequence','Iterable','Callable','MutableSet']:
  if not hasattr(collections,name):setattr(collections,name,getattr(collections.abc,name))
 if not hasattr(inspect,'getargspec'):inspect.getargspec=inspect.getfullargspec
 root=R/'checkouts/sympy__sympy-14711'/sys.argv[1];sys.path.insert(0,str(root))
 from sympy.physics.vector import ReferenceFrame
 n=ReferenceFrame('N');zero=0*n.x
 try:value={'value':str(sum([n.x,zero])),'exception':None}
 except Exception as e:value={'exception':type(e).__name__,'message':str(e)}
 print(json.dumps({'revision':sys.argv[1],'zero_product_type':type(zero).__name__,'zero_product_repr':str(zero),'sum':value}))
else:
 rows=[]
 for rev in ['original','fixed']:
  p=subprocess.run([sys.executable,__file__,rev],capture_output=True,text=True,check=True);rows.append(json.loads(p.stdout.splitlines()[-1]))
 (R/'vector_dispatch_check.json').write_text(json.dumps({'posthoc':True,'reason':'Check model claim that 0*N.x itself supplies scalar zero to __add__. Primary witness/inputs remain frozen.','rows':rows},indent=2)+'\n');print(json.dumps(rows))
