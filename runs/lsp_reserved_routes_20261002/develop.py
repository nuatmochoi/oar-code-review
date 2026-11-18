"""Already-seen native development checks. No models and no new PR selection."""
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import shared
from ace_review.context_adaptive import Corpus, token_count
R=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('reserved_development',R/'policy.py');policy=importlib.util.module_from_spec(sp);sp.loader.exec_module(policy)
jobs=[('lsp_auto_routes_20261002','V01_b'),('lsp_auto_routes_20261002','V02_b'),('lsp_fixture_binding_v2_20261002','J04_a')]
def one(job):
 run,stem=job;prior=shared.ROOT/'runs'/run;p=shared.read(prior/'packets'/f'{stem}.json');head={f:(shared.ROOT/p['workspace_reference']/f).read_text() for f in shared.read(prior/'workspace_manifests'/f'{stem}.json')['source_sha256']};diff=p['common'].split('[DIFF]\n',1)[1]
 c=Corpus({'base':head,'head':head},{v:{'nodes':{},'edges':[]} for v in ['base','head']},diff);resolver=shared.native_policy.ref.old.Bindings(c,head);client=shared.native_policy.Client(shared.ROOT/p['workspace_reference'])
 try:
  native=shared.native_policy.ref.LSPBackend(resolver,client);source,obs,audit=policy.packet(head,diff,p['common'].split('[DIFF]',1)[0],resolver,native,client,shared.native_policy.b.edited_lines)
 finally:client.close()
 defs,parents=policy.index(head)
 checks={'source_cap':token_count(source)<=2600,'observation_cap':token_count(obs)<=400,'query_cap':len(audit['jumps'])<=12,'errors_zero':all(not j['error'] for j in audit['jumps']),'whole_packages_and_owners':all(set(lines).issubset(audit['state']['source_lines'][f]) for a in audit['admissions'] for f,lines in policy.package(head,defs,parents,tuple(a['target'])).items())}
 targets=sorted(set(tuple(a['target']) for a in audit['admissions']))
 shared.save(R/f'development_{stem}.json',{'already_seen':True,'model_calls':0,'source':source,'observations':obs,'audit':audit,'checks':checks,'native_trace':client.trace,'initialization':client.initialization,'stderr':client.errors})
 return {'case':stem,'queries':len(audit['jumps']),'tokens':token_count(source),'targets':targets,'checks':checks,'blocked':len(audit['blocked'])}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(one,jobs))
shared.save(R/'development_validation.json',{'rows':results,'all_checks_pass':all(all(x['checks'].values()) for x in results),'model_calls':0})
print(json.dumps(results,indent=2));assert all(all(x['checks'].values()) for x in results)
