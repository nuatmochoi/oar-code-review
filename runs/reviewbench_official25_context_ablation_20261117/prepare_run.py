"""Prepare immutable requests and independent official-judge workspaces."""
import json
import shutil
import subprocess
from config import *
from experiment import load, write, sha, plan_request, final_request, tokens
from jsonschema import Draft202012Validator

LATEST=ROOT.parent/'reviewbench_candidate_union25_20261114/arms/UNION'

def prepare():
    assert not (ROOT/'freeze.json').exists(), 'Already frozen'
    rows=[]
    for arm in ARMS:
        dest=ROOT/'arms'/arm
        upstream=dest/'upstream';upstream.mkdir(parents=True,exist_ok=True)
        for name in ('package.json','package-lock.json','scripts','schema','corpus','golden','node_modules'):
            target=upstream/name
            if not target.exists():target.symlink_to((ORIGINAL/'upstream'/name).resolve())
        config=dest/'judge-config';config.mkdir(exist_ok=True)
        models=load(LATEST/'judge-config/models.json')
        for provider in models['providers'].values():
            provider['baseUrl']=f'http://127.0.0.1:{PORT}/judge/{arm}/v1'
            provider['apiKey']='REVIEW_BROKER_TOKEN'
        (config/'models.json').write_text(json.dumps(models,indent=2))
        (config/'auth.json').write_text('{}')
        if not (config/'bin').exists():shutil.copytree(LATEST/'judge-config/bin',config/'bin')
        if not (dest/'judge-repos').exists():
            subprocess.run(['cp','-cR',str(LATEST/'judge-repos'),str(dest/'judge-repos')],check=True)
    for path in sorted((ROOT/'public').glob('[0-9][0-9].json')):
        packet=load(path)
        for arm in ARMS:
            plan=plan_request(packet,arm)
            final,evidence,catalog=final_request(packet,arm,dict(unit_assessments=[],questions=[],reasoning=''))
            for body in (plan,final):Draft202012Validator.check_schema(body['text']['format']['schema'])
            # Count all textual JSON here; live official exact count repeats before final dispatch.
            estimate=tokens(plan)+PLAN_OUTPUT+tokens(final)+PLAN_OUTPUT+8192+FINAL_OUTPUT
            assert estimate<TOKEN_CAP,(packet['index'],arm,estimate)
            target=ROOT/'prepared'/arm/f'{packet["index"]:02d}.plan.json'
            if target.exists():assert load(target)==plan
            else:write(target,plan)
            rows.append(dict(index=packet['index'],arm=arm,plan_reference_tokens=tokens(plan),
                final_without_plan_reference_tokens=tokens(final),reserved_review_tokens=estimate,
                evidence_records=len(evidence),locations=len(catalog['locations'])))
    assert len(rows)==75
    (ROOT/'preflight.json').write_text(json.dumps(dict(requests=rows,max_reserved_review_tokens=max(r['reserved_review_tokens'] for r in rows)),indent=2))
    print(json.dumps(dict(preflight=len(rows),max_reserved_tokens=max(r['reserved_review_tokens'] for r in rows))))

def freeze():
    assert not (ROOT/'freeze.json').exists()
    inputs=[*ROOT.glob('*.py'),ROOT/'PROTOCOL_KO.md',ROOT/'preflight.json',
            *ROOT.glob('public/*.json'),*ROOT.glob('prepared/*/*.json'),*ROOT.glob('vendor/*.py'),
            *ROOT.glob('audit/*.json'),*ROOT.glob('arms/*/judge-config/models.json'),
            *ROOT.glob('arms/*/judge-config/bin/*')]
    upstream=ORIGINAL/'upstream'
    for part in ('scripts','schema','golden'):
        inputs += [p for p in (upstream/part).rglob('*') if p.is_file()]
    inputs += [upstream/'corpus/manifest.json',upstream/'package.json',upstream/'package-lock.json']
    for prior in (ORIGINAL,OLD):
        inputs += list(prior.glob('scoring/results*.json'))+list(prior.glob('arms/*/scoring/results*.json'))
    inputs=list(dict.fromkeys(p.resolve() for p in inputs))
    write(ROOT/'freeze.json',dict(created_at=__import__('datetime').datetime.now().astimezone().isoformat(),
        official_commit='03c62fe06ca42b39216402ebbcb9aec765109a59',conditions=list(ARMS),PRs=25,
        model=MODEL,review_token_cap=TOKEN_CAP,total_estimated_usd_cap=TOTAL_USD,arm_estimated_usd_cap=ARM_USD,
        files={str(p):sha(p) for p in inputs}))
    from freeze_run import verify
    print('Frozen',len(verify()['files']),'files')

if __name__=='__main__':
    import sys
    freeze() if '--freeze' in sys.argv else prepare()
