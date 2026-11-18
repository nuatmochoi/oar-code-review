import difflib,importlib.util,json,sys,tempfile
from pathlib import Path
import policy
R=Path(__file__).resolve().parent;ROOT=R.parents[1]
sys.path.insert(0,str(ROOT/'runs/lsp_callback_routes_20261002'));import shared

def unit_checks():
 base={'m.py':'def untouched():\n    return 0\n\ndef f(x):\n    if x < 0:\n        return 0\n    return x\n'}
 head={'m.py':'def untouched():\n    return 0\n\ndef f(x):\n    return x\n'}
 bd=policy.index(base)[0];hd=policy.index(head)[0]
 roots,deleted,events=policy.changed_roots(base,head,bd,hd);assert roots==[('m.py','f')] and not deleted
 h2={'m.py':'def untouched():\n    return 0\n'}
 roots,deleted,_=policy.changed_roots(base,h2,bd,policy.index(h2)[0]);assert not roots and deleted==[('m.py','f')]
 n=policy.Navigator(base,h2,Path('/tmp'),'');assert 'base m.py' in n.source() and 'head m.py' not in n.source()
 nested={'n.py':'def outer():\n    def inner():\n        return 1\n    return inner()\n'};assert ('n.py','outer.inner') in policy.index(nested)[0]
 # A real native reference check: alias imports, caller, guard and method ownership.
 with tempfile.TemporaryDirectory(prefix='lsp-linked-dev-') as d:
  root=Path(d);src={'a.py':'def convert(x):\n    if x is None:\n        return None\n    return str(x)\n','b.py':'from a import convert as cv\ndef use(x):\n    return cv(x)\n'}
  for f,s in src.items():(root/f).write_text(s)
  (root/'pyrightconfig.json').write_text(json.dumps({'include':['*.py'],'typeCheckingMode':'off'}))
  orig={**src,'b.py':src['b.py'].replace('cv(x)','cv(None)')};nav=policy.Navigator(orig,src,root,'use(x)')
  client=policy.Client(root)
  try:nav.advance(client,6)
  finally:client.close()
  assert any(('a.py','convert') in q['targets'] for q in nav.queries),nav.audit()
  assert 'if x is None:' in nav.source()
  (R/'development_native_fixture.json').write_text(json.dumps({'audit':nav.audit(),'trace':client.trace,'source':nav.source()},indent=2))
 return {'deletion_only_anchor':True,'whole_function_deletion_base_only':True,'nested_declaration':True,'actual_pyright_alias_definition':True}

def seen():
 prior=ROOT/'runs/lsp_callback_routes_20261002';labels=shared.read(prior/'labels.json');rows=shared.read(prior/'selected.json');out=[]
 for cid,world in [('V01','b'),('V02','a')]:
  row=next(r for r in rows if r['instance_id']==labels[cid]['instance_id']);direction=labels[cid]['directions'][world];b,h=('original','fixed') if direction=='forward' else ('fixed','original')
  base=shared.full_sources(prior/'checkouts'/row['instance_id']/b);head=shared.full_sources(prior/'checkouts'/row['instance_id']/h)
  packet=shared.read(prior/'packets'/f'{cid}_{world}.json');root=ROOT/packet['workspace_reference']
  nav=policy.Navigator(base,head,root,packet['common'].split('[DIFF]')[0]);client=policy.Client(root)
  try:nav.advance(client,12)
  finally:client.close()
  a=nav.audit();assert a['has_roots'] and a['root_coverage'];assert a['source_tokens']<=5200 and len(a['queries'])<=12
  (R/f'development_seen_{cid}_{world}.json').write_text(json.dumps({'source':nav.source(),'observations':nav.observations(True),'audit':a,'trace':client.trace},indent=2))
  out.append({'case':cid+'_'+world,'source_tokens':a['source_tokens'],'queries':len(a['queries']),'roots':a['roots'],'root_coverage':a['root_coverage']});print(out[-1],flush=True)
 return out
if __name__=='__main__':
 result={'unit_checks':unit_checks(),'seen':seen(),'model_calls':0};(R/'development_validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
