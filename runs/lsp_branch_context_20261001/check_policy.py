import ast,json,sys
from pathlib import Path
R=Path(__file__).resolve().parent;sys.path.insert(0,str(R));import experiment as ex
from fragments import initial
src='def giant(**args):\n    cls = args.pop("cls", int)\n'+''.join(f'    unused_{i} = {i}\n' for i in range(180))+'    if args:\n        return giant(cls=cls, **args)\n    return cls\n\ndef entry():\n    return giant(cls=str)\n'
base=src.replace('giant(cls=cls, **args)','giant(**args)');diff='--- a/api.py\n+++ b/api.py\n@@ -184 +184 @@\n-        return giant(**args)\n+        return giant(cls=cls, **args)\n';versions={'base':{'api.py':base},'head':{'api.py':src}};corpus=ex.Corpus(versions,{v:{'nodes':{},'edges':[]} for v in versions},diff);case={'scope':'entry() must retain cls through nested giant calls','diff':diff};edits=ex.policy.edited_lines(diff);state=initial(corpus,versions,case,edits);text=state.render();checks={'partial_marked':'PARTIAL ANCHORS; omitted code unknown' in text,'changed_call_visible':'giant(cls=cls, **args)' in text,'parameter_extraction_visible':'args.pop("cls", int)' in text,'caller_argument_visible':'return giant(cls=str)' in text,'initial_cap':state.tokens<=800}
# Native E must add no receiver query for a resolved call, but trigger for an unresolved member.
fixture=R/'policy_fixture';fixture.mkdir(exist_ok=True);(fixture/'pyrightconfig.json').write_text('{"typeCheckingMode":"off","pythonVersion":"3.11"}')
for unresolved in [False,True]:
 member='missing' if unresolved else 'run';s='class Service:\n    def run(self):\n        return 1\n\nclass Holder:\n    @property\n    def engine(self) -> Service:\n        return Service()\n\ndef entry():\n    worker = Holder()\n    return worker.engine.'+member+'()\n';(fixture/'api.py').write_text(s);vv={'base':{'api.py':s.replace(member+'()',member+'() + 0')},'head':{'api.py':s}};dd='--- a/api.py\n+++ b/api.py\n@@ -12 +12 @@\n-    return worker.engine.'+member+'() + 0\n+    return worker.engine.'+member+'()\n';cc=ex.Corpus(vv,{v:{'nodes':{},'edges':[]} for v in vv},dd);rr=ex.policy.ref.old.Bindings(cc,vv['head']);client=ex.Client(fixture)
 try:st,a=ex.policy.packet(cc,{'scope':'entry()','diff':dd},rr,client,'E',vv)
 finally:client.close()
 checks['unresolved_receiver_gate' if unresolved else 'resolved_call_no_receiver_extra']=any(j['origin']=='unresolved_receiver_gate' for j in a['jumps']) if unresolved else not any(j['kind']=='receiver_provenance' for j in a['jumps']);checks['caps_'+str(unresolved)]=st.tokens<=1600 and len(a['jumps'])<=12;checks['native_errors_'+str(unresolved)]=not any(j['error'] for j in a['jumps'])
 (R/('native_test_unresolved.json' if unresolved else 'native_test_resolved.json')).write_text(json.dumps({'audit':a,'source':st.render(),'trace':client.trace},indent=2))
result={'checks':checks,'passed':all(checks.values()),'fragment_source':text};(R/'policy_tests.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(checks,indent=2));assert result['passed']
