import ast,json,sys
from pathlib import Path
R=Path(__file__).resolve().parent;sys.path.insert(0,str(R));import experiment as ex
p=ex.policy;fixture=R/'policy_fixture';fixture.mkdir(exist_ok=True);(fixture/'pyrightconfig.json').write_text('{"typeCheckingMode":"off","pythonVersion":"3.11"}')
s='class A:\n    def run(self):\n        return 1\n\nclass B:\n    def run(self):\n        return 2\n\ndef use_a(a: A):\n    return a.run()\n\ndef use_b(b: B):\n    return b.run()\n';(fixture/'api.py').write_text(s);vv={'head':{'api.py':s},'base':{'api.py':s}};c=ex.Corpus(vv,{v:{'nodes':{},'edges':[]} for v in vv},'');r=p.ref.old.Bindings(c,vv['head']);client=p.Client(fixture)
try:line,char=p.declaration_position(r,'api.py','A.run');raw=client.references('api.py',line,char);hits,exc=p.locations(c,client,raw)
finally:client.close()
checks={'native_references_supported':bool(client.initialization['capabilities'].get('referencesProvider')),'correct_caller_reference':any(c.units[k].symbol=='use_a' for k,l in hits),'unrelated_same_name_excluded':not any(c.units[k].symbol=='use_b' for k,l in hits)}
# Missing large function must receive signature/edited branch/parameter constructor without changing protected B source.
noise='\n'.join('def noise_'+str(i)+'():\n'+''.join(f'    x{j} = {j}\n' for j in range(12))+'    return 0\n' for i in range(20));giant='def giant(value, *, cls=int):\n'+''.join(f'    unused_{i} = {i}\n' for i in range(180))+'    if value:\n        value += 1\n    return cls(value)\n\ndef entry():\n    return giant(7, cls=str)\n';s=giant+noise;old=s.replace('value += 1','value += 0');(fixture/'api.py').write_text(s);vv={'head':{'api.py':s},'base':{'api.py':old}};dd='--- a/api.py\n+++ b/api.py\n@@ -183 +183 @@\n-        value += 0\n+        value += 1\n';c=ex.Corpus(vv,{v:{'nodes':{},'edges':[]} for v in vv},dd);r=p.ref.old.Bindings(c,vv['head']);st=p.EvidenceState(c)
for k,u in c.units.items():
 if u.symbol.startswith('noise_'):st.add([k],'L1',1450)
entry=r.unit('api.py','entry')[0];st.add([entry],'L1',1600);baseline={'B':st.render(),'B_audit':{'state':st.audit(),'jumps':[]}};client=p.Client(fixture)
try:new,a,o=p.packet(c,vv,{'scope':'entry() must respect cls','diff':dd},r,client,baseline,'P')
finally:client.close()
checks.update({'P_no_extra_queries':not a['extra_navigation'],'protected_whole_source_unchanged':all(new.lines[k]==st.lines[k] for k in a['protected_ids']),'missing_branch_added':'value += 1' in new.render(),'parameter_constructor_added':'return cls(value)' in new.render(),'partial_marked':'PARTIAL ANCHORS; omitted code unknown' in new.render(),'only_unprotected_evicted':not bool(set(a['evicted_ids'])&set(a['protected_ids'])),'P_caps':new.tokens<=1600 and ex.token_count(o)<=400})
seed={'B':new.render(),'B_audit':{'state':a['state'],'jumps':[]},'inherited_protected_ids':sorted(set(a['protected_ids'])|{x['unit_id'] for x in a['additions']})};client=p.Client(fixture)
try:rr,ra,ro=p.packet(c,vv,{'scope':'entry() must respect cls','diff':dd},r,client,seed,'R')
finally:client.close()
checks.update({'R_native_queries':0<len(ra['extra_navigation'])<=3,'P_core_unchanged_in_R':all(rr.lines[k]==new.lines[k] for k in seed['inherited_protected_ids']),'R_no_changed_branch_reselection':not any(x['reason']=='missing_changed_branch_parameter_construction' for x in ra['additions']),'R_caps':rr.tokens<=1600 and ex.token_count(ro)<=400,'R_no_native_error':not any(x['error'] for x in ra['extra_navigation'])})
# A visible changed function must receive its own used import in shared P, not just new fragment imports.
ss='from math import sqrt\n\ndef public(x):\n    return sqrt(x)\n';oldss=ss.replace('sqrt(x)','sqrt(x)+0');vv={'base':{'api.py':oldss},'head':{'api.py':ss}};dd='--- a/api.py\n+++ b/api.py\n@@ -4 +4 @@\n-    return sqrt(x)+0\n+    return sqrt(x)\n';cc=ex.Corpus(vv,{v:{'nodes':{},'edges':[]} for v in vv},dd);res=p.ref.old.Bindings(cc,vv['head']);st=p.EvidenceState(cc)
for k,u in cc.units.items():
 if u.kind=='function':st.add([k],'L1',1600)
seed0={'B':st.render(),'B_audit':{'state':st.audit(),'jumps':[]}};ii,ia,io=p.packet(cc,vv,{'scope':'public(4)','diff':dd},res,None,seed0,'P');checks['visible_changed_import_added']='from math import sqrt' in ii.render()
(R/'native_tests.json').write_text(json.dumps({'same_name_reference_result':raw,'augmentation_audit':a,'source':new.render(),'trace':client.trace,'R_audit':ra,'R_source':rr.render()},indent=2));result={'checks':checks,'passed':all(checks.values())};(R/'policy_tests.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2));assert result['passed']
