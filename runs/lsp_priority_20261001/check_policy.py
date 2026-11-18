"""Pre-call integration gates: priority, native property/global extraction, caps."""
import json,sys
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(R));import experiment as ex
from ace_review.context_adaptive import Corpus
src={'api.py':'from service import Holder\nfrom settings import LIMIT\n\ndef entry():\n    worker = Holder()\n    return worker.engine.run(LIMIT)\n','service.py':'class Service:\n    def run(self, x):\n        return x + 1\n\nclass Holder:\n    @property\n    def engine(self) -> Service:\n        return Service()\n','settings.py':'LIMIT = 7\n'}
root=R/'policy_fixture';root.mkdir(exist_ok=True)
for p,s in src.items():(root/p).write_text(s)
(root/'pyrightconfig.json').write_text(json.dumps({'pythonVersion':'3.11','typeCheckingMode':'off','autoSearchPaths':False,'extraPaths':['.']}))
diff='--- a/api.py\n+++ b/api.py\n@@ -6,1 +6,1 @@\n-    return worker.engine.run(0)\n+    return worker.engine.run(LIMIT)\n';base=dict(src,**{'api.py':src['api.py'].replace('run(LIMIT)','run(0)')});corpus=Corpus({'base':base,'head':src},{v:{'nodes':{},'edges':[]} for v in ['base','head']},diff);resolver=ex.policy.ref.old.Bindings(corpus,src);results={}
for arm in ['B','C']:
 client=ex.Client(root)
 try:state,audit=ex.policy.packet(corpus,{'diff':diff,'scope':'Only entry() is used.'},resolver,client,arm)
 finally:client.close()
 results[arm]={'source':state.render(),'audit':audit,'trace':client.trace}
checks={'B_queries_changed_call_first':results['B']['audit']['jumps'][0]['line']==6,'C_queries_receiver_getter':any(j['kind']=='receiver_provenance' and j['targets']==[('service.py','Holder.engine')] for j in results['C']['audit']['jumps']),'C_supplies_getter_body':'return Service()' in results['C']['source'],'C_supplies_global_declaration':'LIMIT = 7' in results['C']['source'],'caps':all(len(x['audit']['jumps'])<=12 and x['audit']['state']['tokens']<=1600 and ex.token_count(ex.obs(x['audit']['jumps']))<=400 for x in results.values()),'no_server_error':all(not j.get('error') for x in results.values() for j in x['audit']['jumps'])}
(R/'policy_tests.json').write_text(json.dumps({'checks':checks,'passed':all(checks.values()),'results':results},indent=2)+'\n');print(json.dumps(checks));assert all(checks.values())
