"""Data-only snapshots and prospective case preparation; never calls a model."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import argparse,hashlib,json,os,re,shutil,subprocess,tarfile,tempfile,time
from pathlib import Path

ROOT=Path(__file__).resolve().parent;PROJECT=ROOT.parents[1]
PARENT=PROJECT/'runs/targeted_context_controller_20261102'


def load(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2)


def command(argv,*,cwd=None,timeout=300):
    p=subprocess.run(argv,cwd=cwd,text=True,capture_output=True,timeout=timeout)
    if p.returncode:raise RuntimeError(f'command failed{p.returncode}: {argv}: {p.stderr[-1200:]}')
    return p


def cache_for(repo,commits):
    local=PROJECT/'data/repos'/repo.replace('/','__')
    if local.is_dir() and all(subprocess.run(['git','-C',str(local),'cat-file','-e',f'{c}^{{commit}}'],capture_output=True).returncode==0 for c in commits):
        return local,dict(kind='existing_read_only_object_cache',root=str(local))
    cache=ROOT/'prepared/git_cache'/(repo.replace('/','__')+'.git')
    cache.parent.mkdir(parents=True,exist_ok=True)
    if not cache.exists():command(['git','init','--bare',str(cache)])
    receipts=[]
    for commit in commits:
        if subprocess.run(['git','-C',str(cache),'cat-file','-e',f'{commit}^{{commit}}'],capture_output=True).returncode==0:continue
        started=time.monotonic();argv=['git','-C',str(cache),'fetch','--no-tags','--depth=1',f'https://github.com/{repo}.git',commit]
        p=command(argv,timeout=300);receipts.append(dict(argv=argv,duration_seconds=time.monotonic()-started,stdout=p.stdout,stderr=p.stderr))
    return cache,dict(kind='new_isolated_git_cache',root=str(cache),fetches=receipts)


def archive(cache,commit,dest):
    dest=Path(dest)
    if dest.exists():return
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=ROOT/'prepared',suffix='.tar') as f:
        p=subprocess.run(['git','-C',str(cache),'archive','--format=tar',commit],stdout=f,stderr=subprocess.PIPE)
        if p.returncode:raise RuntimeError(p.stderr.decode()[-1200:])
        f.flush();dest.mkdir()
        with tarfile.open(f.name) as tar:tar.extractall(dest,filter='data')


def manifest(root):
    root=Path(root).resolve();files={};symlinks=[]
    for p in sorted(root.rglob('*')):
        if not p.is_file():continue
        if any(part in ('.git','__pycache__','_external') for part in p.relative_to(root).parts):continue
        if p.is_symlink():
            resolved=p.resolve()
            if not resolved.is_relative_to(root):symlinks.append(dict(path=str(p.relative_to(root)),status='outside_snapshot_unavailable'));continue
            symlinks.append(dict(path=str(p.relative_to(root)),target=str(resolved.relative_to(root))))
        files[str(p.relative_to(root))]=sha(p)
    return dict(root=str(root),files=files,file_manifest_sha256=digest(files),symlinks=symlinks,source_pool_scope='all_materialized_repository_regular_files; external libraries explicitly separate')


def copy_tree(source,dest):
    source=Path(source);dest=Path(dest)
    if dest.exists():return
    dest.parent.mkdir(parents=True,exist_ok=True)
    p=subprocess.run(['/bin/cp','-cR',str(source),str(dest)],capture_output=True,text=True)
    if p.returncode:
        if dest.exists():shutil.rmtree(dest)
        shutil.copytree(source,dest,symlinks=True)


def external_files(case):
    key=case['case_id']+'_'+case['direction'];result={}
    if case['language']=='Python':
        records=load(PARENT/'python_targeted/public_cases.json')
        record=next(c for c in records if (c['case_id'],c['direction'])==(case['case_id'],case['direction']))
        raw=record.get('external_sources',{})
        for virtual,v in raw.items():
            result[virtual]=dict(absolute_source=v['absolute_source'],sha256=v['sha256'],origin=v['origin'],
                version=v.get('runtime_version','unknown'),module=v.get('module'),binding_status='assumption',
                binding_evidence=[],assumptions=['Preserved CPython analysis-profile source; original PR lock/deployed interpreter is not established.'])
    else:
        raw=load(PARENT/'go_targeted/runs'/key/'source_manifest.json').get('external_sources',{})
        for virtual,v in raw.items():
            origin=v['origin'];version='unknown';match=re.search(r'@([^/]+)/',origin)
            if match:version=match[1]
            elif '/go' in origin:version='legacy_go1.25_analysis_profile'
            result[virtual]=dict(absolute_source=v['copy'],sha256=v['sha256'],origin=origin,version=version,
                binding_status='assumption',binding_evidence=[],
                assumptions=['Preserved source-origin/hash alone does not establish this PR dependency resolution or runtime dispatch.'])
    for v in result.values():assert sha(v['absolute_source'])==v['sha256']
    return result


def environment_record(case,snapshots):
    declared={}
    for snapshot,data in snapshots.items():
        root=Path(data['root']);refs=[]
        names=['pyproject.toml','setup.py','setup.cfg','tox.ini','go.mod','go.sum','requirements.txt','uv.lock','poetry.lock']
        names += [str(p.relative_to(root)) for p in (root/'.github/workflows').glob('*.yml')] if (root/'.github/workflows').exists() else []
        for name in sorted(set(names)):
            p=root/name
            if not p.is_file():continue
            lines=p.read_text(errors='replace').splitlines()
            version_lines=[dict(line=n,text=t) for n,t in enumerate(lines,1) if re.search(r'python_requires|requires-python|python-version|envlist|^go |^toolchain |\bpy3\d\b',t)]
            refs.append(dict(path=name,sha256=sha(p),version_declaration_lines=version_lines))
        declared[snapshot]=refs
    return dict(language=case['language'],profile_binding_status='assumption',deployed_runtime_observed=False,
        python_executable=str(PROJECT/'runs/causal_verification_v2_20261029/.venv/bin/python'),python_version='3.11.13',
        go_executable=shutil.which('go'),go_version='1.25.0',declared_configuration=declared,
        assumptions=['Legacy development gold was exercised in the stated local analysis profile, not certified original PR deployment.','PR version ranges and configuration are source declarations; do not equate them with a resolved deployed implementation.'])


def development():
    olds=load(PARENT/'prepared/cases.json');pairs={};snapshots={};receipts=[]
    for c in olds:pairs.setdefault(c['case_id'],{})[c['direction']]=c
    for cid,directions in pairs.items():
        started=time.monotonic()
        for direction,c in directions.items():
            dest=ROOT/'prepared/source_pools'/cid/direction
            if c['language']=='Python':
                template=ROOT/'prepared/upstream'/c['repo'].replace('/','__')/c['upstream_base_commit']
                assert template.is_dir(),template
                copy_tree(template,dest)
                for path in c['changed_files']:
                    target=dest/path;target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(Path(c['workspace'])/path,target)
                # Every original legacy Python source remains byte-identical;
                # added upstream docs/config/source are provenance-recorded.
                mismatches=[]
                for p in Path(c['workspace']).rglob('*.py'):
                    rel=p.relative_to(c['workspace']);q=dest/rel
                    if not q.is_file() or sha(q)!=sha(p):mismatches.append(str(rel))
                assert not mismatches,(cid,direction,mismatches[:12])
                provenance=dict(kind='upstream_base_commit_full_tree_plus_preserved_public_HEAD_projection',upstream_commit=c['upstream_base_commit'],
                    overlaid_files=c['changed_files'],legacy_workspace=c['workspace'],is_original_PR_HEAD=False)
            else:
                copy_tree(c['workspace'],dest)
                provenance=dict(kind='preserved_full_Go_materialization',legacy_workspace=c['workspace'],declared_revision=c['head_revision'],
                    is_original_PR_HEAD=direction=='a',corrective_control=direction=='b')
            value=manifest(dest);value['provenance']=provenance;value['external_files']=external_files(c)
            snapshots[cid,direction]=value
        receipts.append(dict(case_id=cid,duration_seconds=time.monotonic()-started,
            file_counts={d:len(snapshots[cid,d]['files']) for d in directions},new_native_LSP_calls=0))
        print(json.dumps(dict(development_pair=cid,status='snapshots_ready')),flush=True)
    cases=[]
    for old in olds:
        cid=old['case_id'];direction=old['direction'];opposite='b' if direction=='a' else 'a'
        states=dict(HEAD=snapshots[cid,direction],BASE=snapshots[cid,opposite])
        env=environment_record(old,states);envpath=ROOT/'prepared/environment'/f'{cid}_{direction}.json'
        write_new(envpath,env)
        c={k:old[k] for k in ('case_id','direction','language','repo','source_url','stratum','diff','changed_files','public_contract')}
        c.update(id=f'{cid}_{direction}',cohort='development',information_condition='public_contract',snapshots=states,
            environment=env,environment_reference=dict(path=str(envpath),sha256=sha(envpath)),
            initial_blocks=old['arms']['targeted'],
            initial_collection_cost=dict(kind='historical_targeted_source_collection_replayed',historical_cost=old['costs']['targeted'],
                source_reference=dict(path=str(PARENT/'prepared/cases.json'),sha256=sha(PARENT/'prepared/cases.json')),
                new_native_LSP_calls=0,preparation_receipt='prepared/development_snapshot_receipt.json'),
            source_pool_provenance='Complete hashes of each materialized full source tree; BASE/HEAD are the preserved review pair, not falsely original PR parent/child commits.')
        cases.append(c)
    write_new(ROOT/'prepared/development_cases.json',cases)
    write_new(ROOT/'prepared/development_snapshot_receipt.json',dict(created_at=datetime.now(timezone.utc).isoformat(),pairs=receipts,source_only=True,models_called=0))
    print(json.dumps(dict(development_directions=len(cases),public_manifest=str(ROOT/'prepared/development_cases.json'))))


def fetch_sources():
    selection=load(ROOT/'audit/selection.json');oldcases=load(PARENT/'prepared/cases.json');repos={}
    for c in oldcases:
        if c['language']=='Python':repos.setdefault(c['repo'],set()).add(c['upstream_base_commit'])
    for c in selection['selected']:repos.setdefault(c['repo'],set()).update((c['source_commit'],c['target_commit']))
    results=[]
    def job(repo,commits):
        start=time.monotonic();cache,receipt=cache_for(repo,sorted(commits))
        snapshots=[]
        for commit in sorted(commits):
            target=ROOT/'prepared/upstream'/repo.replace('/','__')/commit
            archive(cache,commit,target);snapshots.append(dict(commit=commit,root=str(target),file_count=sum(1 for p in target.rglob('*') if p.is_file())))
        return dict(repo=repo,cache=receipt,snapshots=snapshots,duration_seconds=time.monotonic()-start)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(job,repo,commits) for repo,commits in repos.items()]
        for f in as_completed(futures):
            try:value=f.result();results.append(value);print(json.dumps(dict(repo=value['repo'],snapshots=len(value['snapshots']),status='ready')),flush=True)
            except Exception as exc:results.append(dict(status='failed',error=f'{type(exc).__name__}: {exc}'));print(json.dumps(results[-1]),flush=True)
    write_new(ROOT/'prepared/source_fetch_receipt.json',dict(created_at=datetime.now(timezone.utc).isoformat(),repositories=results,models_called=0))
    assert all(r.get('status')!='failed' for r in results)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['fetch','development']);args=parser.parse_args()
    if args.action=='fetch':fetch_sources()
    elif args.action=='development':development()

# Invoked through import below; old fetch/development CLI entry remains intact.
def holdout():
    """Fixed metadata-selected feasible PRs; the collector never reads private gold."""
    import sys
    sys.path.insert(0,str(ROOT/'audit'))
    from collect_holdout_initial import collect,POLICY
    approved=[('langflow-ai/langflow',5388,'HO_PY01','Python'),('mudler/LocalAI',4577,'HO_GO01','Go')]
    cases=[];receipts=[]
    for repo,number,cid,language in approved:
        meta=load(ROOT/'prepared/selected_pr_metadata'/(repo.replace('/','__')+'_'+str(number)+'.merge_base.json'))
        cache,unused=cache_for(repo,[meta['merge_base_sha'],meta['head_sha']])
        sides={}
        for key,commit in [('parent',meta['merge_base_sha']),('head',meta['head_sha'])]:
            root=ROOT/'prepared/upstream'/repo.replace('/','__')/commit
            assert root.is_dir()
            value=manifest(root);value.update(external_files={},provenance=dict(kind='actual_upstream_git_commit_materialization',commit=commit,repository=repo,
                is_original_PR_HEAD=key=='head',is_PR_merge_base=key=='parent',raw_PR_diff_verified=True))
            sides[key]=value
        changed=command(['git','-C',str(cache),'diff','--name-only',meta['merge_base_sha'],meta['head_sha']]).stdout.splitlines()
        for direction,base,head in [('a','parent','head'),('b','head','parent')]:
            base_commit=meta['merge_base_sha'] if base=='parent' else meta['head_sha'];head_commit=meta['merge_base_sha'] if head=='parent' else meta['head_sha']
            diff=command(['git','-C',str(cache),'diff','--no-ext-diff','--unified=3',base_commit,head_commit]).stdout
            states=dict(BASE=sides[base],HEAD=sides[head])
            c=dict(id=f'{cid}_{direction}',case_id=cid,direction=direction,language=language,repo=repo,source_url=f'https://github.com/{repo}/pull/{number}',
                stratum='metadata_selected_unused_PR',cohort='holdout',information_condition='no_extra_hint',
                public_contract='Review the supplied BASE-to-HEAD change for introduced behavioral defects using only the provided source and allowed evidence. No extra scenario or defect hint is supplied.',
                diff=diff,changed_files=changed,snapshots=states,
                source_pool_provenance='Actual PR merge-base/head source trees; a is the original final PR, b is the researcher-requested full reverse rollback control, not an independent PR.',
                change_origin='actual_final_PR' if direction=='a' else 'researcher_reverse_rollback_control',
                revisions=dict(BASE=base_commit,HEAD=head_commit),
                selection_reference=dict(path=str(ROOT/'audit/selection.json'),sha256=sha(ROOT/'audit/selection.json')))
            env=environment_record(c,states)
            env['assumptions']=['Static PR configuration only; no original deployed runtime or resolved environment execution was observed.','The local parser/LSP profile is an analysis assumption, not original PR deployment.']
            envpath=ROOT/'prepared/environment'/f'{cid}_{direction}.json';write_new(envpath,env)
            c['environment']=env;c['environment_reference']=dict(path=str(envpath),sha256=sha(envpath))
            blocks,cost,events=collect(c);c['initial_blocks']=blocks;c['initial_collection_cost']=cost
            eventpath=ROOT/'prepared/holdout_initial_traces'/f'{cid}_{direction}.json'
            write_new(eventpath,dict(policy=POLICY,policy_sha256=digest(POLICY),admissions=events,cost=cost,private_labels_read=False,model_calls=0))
            c['initial_collection_cost']['preparation_reference']=dict(path=str(eventpath),sha256=sha(eventpath))
            cases.append(c);receipts.append(dict(id=c['id'],blocks=len(blocks),source_tokens=cost['source_tokens'],source_files={s:len(v['files']) for s,v in states.items()},collection_cost=cost))
    write_new(ROOT/'prepared/holdout_cases.json',cases)
    write_new(ROOT/'prepared/holdout_preparation_receipt.json',dict(cases=receipts,source_only_collector=True,models_called=0))
    return cases
