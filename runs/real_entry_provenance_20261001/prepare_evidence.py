"""Save verified source chains separately from frozen synthetic experiments."""
import ast,hashlib,json
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1]
read=lambda p:json.loads(p.read_text())
chains=[]
for cid,snapshot,files in [('F04','7151',['api/settings.py','api/apps/kb_app.py','rag/settings.py']),('F08','9560',['server.py'])]:
    case=read(ROOT/f'runs/context_fresh6_20260929/experiment_v1/cases/{cid}.json')
    for file in files:
        pinned=(ROOT/f'runs/context_fresh6_20260929/snapshots/{snapshot}'/file).read_text()
        for world in case['worlds'].values():
            for sources in world['versions'].values():assert sources[file]==pinned,(cid,file)
        chains.append(dict(case=cid,file=file,snapshot=snapshot,sha256=hashlib.sha256(pinned.encode()).hexdigest(),unchanged_across_worlds_and_revisions=True))
server=(ROOT/'runs/context_fresh6_20260929/snapshots/9560/server.py').read_text();lines=server.splitlines()
registration=[dict(file='server.py',line=i,source=line.strip()) for i,line in enumerate(lines,1) if 'middlewares = [cache_control]' in line or 'web.Application(client_max_size=max_upload_size, middlewares=middlewares)' in line]
assert len(registration)==2
facts=dict(source_identity_checks=chains,middleware_registration=registration,provenance_chains=[
 dict(case='F04',chain=['api.settings.init_settings: environment DOC_ENGINE -> conditional backend constructor -> global docStoreConn','api.apps.kb_app.update: changed pagerank <= 0 -> {remove: PAGERANK_FLD}','rag.settings.PAGERANK_FLD = pagerank_fea','InfinityConnection.update -> database update payload'],receiver_resolution='conditional on DOC_ENGINE=infinity; unset source default selects ESConnection',request_contract={'existing_pagerank':1,'request_pagerank':0,'parser_id':'naive','authentication_and_ownership':'declared passing fixtures'},production_deployment_verified=False),
 dict(case='F08',chain=['PromptServer.__init__: cache_control middleware registered in web.Application','@routes.get(/view) -> view_image','filename query -> missing-file response status404','cache_control: request.path extension guard -> early return'],actual_route='/view',filename_query='missing.png',changed_404_cache_branch_reached=False,production_deployment_verified=False)],model_calls=0)
(R/'provenance.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2)+'\n')
print('Verified unchanged real entry sources; recorded two condition-aware provenance chains.')
