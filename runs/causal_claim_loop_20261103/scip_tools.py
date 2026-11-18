"""Real paired SCIP preparation; references remain static references.

Only paths already present in the shared snapshot source pool become candidate
priorities. No source is added to that pool and no model is called here.
"""
from pathlib import Path,PurePosixPath
from collections import defaultdict
import hashlib,json,os,shutil,subprocess,time,urllib.parse
from go_tools import _delta_lines,physical

ROOT=Path(__file__).resolve().parent;PROJECT=ROOT.parents[1]
GO_INDEXER=PROJECT/'tools/bin/scip-go';EXPORTER=PROJECT/'tools/bin/scip-export'
PY_INDEXER=ROOT/'go_flow/tools/scip_python/node_modules/.bin/scip-python'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def _write(p,x):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')
def _range(o,enclosing=False):
    r=o.get('enclosingRange' if enclosing else 'range',[])
    if len(r)==3:return r[0]+1,r[0]+1,r[1]+1
    if len(r)==4:return r[0]+1,max(r[0]+1,r[2]+int(r[3]>0)),r[1]+1
    key=('singleLineEnclosingRange' if enclosing else 'singleLineRange')
    if key in o:
        r=o[key];return r.get('line',0)+1,r.get('line',0)+1,r.get('startCharacter',0)+1
    key=('multiLineEnclosingRange' if enclosing else 'multiLineRange')
    if key in o:
        r=o[key];return r['startLine']+1,max(r['startLine']+1,r['endLine']+int(r.get('endCharacter',0)>0)),r.get('startCharacter',0)+1
    return None
def _safe(rel):
    p=PurePosixPath(rel)
    if p.is_absolute() or '..' in p.parts:raise ValueError('unsafe_source_path')
    return str(p)
def _name(symbol):
    descriptor=symbol.split(' ',4)[-1]
    names=__import__('re').findall(r'[A-Za-z_][A-Za-z_0-9]*',descriptor)
    return names[-1] if names else ''
def _byte_column(row,column,encoding):
    """SCIP position encoding is independent of metadata's disk text encoding."""
    offset=column-1
    if offset<0:raise ValueError('negative_SCIP_column')
    if encoding in (1,'UTF8CodeUnitOffsetFromLineStart'):
        if offset>len(row.encode()):raise ValueError('SCIP_column_outside_source')
        row.encode()[:offset].decode();return column,'declared_utf8'
    if encoding in (2,'UTF16CodeUnitOffsetFromLineStart'):
        if offset*2>len(row.encode('utf-16-le')):raise ValueError('SCIP_column_outside_source')
        prefix=row.encode('utf-16-le')[:offset*2].decode('utf-16-le');return len(prefix.encode())+1,'declared_utf16'
    if encoding in (3,'UTF32CodeUnitOffsetFromLineStart'):
        if offset>len(row):raise ValueError('SCIP_column_outside_source')
        return len(row[:offset].encode())+1,'declared_utf32'
    if row.isascii():return column,'unspecified_position_encoding_ASCII_invariant_only'
    return None,'unspecified_position_encoding_nonASCII_unresolved'
def _symbol_key(path,symbol):return (path,symbol) if symbol.startswith('local ') else ('',symbol)
def _modules(files,changed):
    roots={str(PurePosixPath(p).parent) for p in files if p=='go.mod' or p.endswith('/go.mod')}
    selected={'.'} if '.' in roots else set()
    for name in changed:
        owners=[r for r in roots if PurePosixPath(name).is_relative_to(PurePosixPath(r))]
        if owners:selected.add(max(owners,key=lambda x:len(PurePosixPath(x).parts)))
    return sorted(selected)

def _normalize_documents(raw,source,stage,module,snapshot,unresolved,cost):
    """Reject one unbound document, never discard other valid module docs."""
    stage=Path(stage).resolve();module_root=(stage/module).resolve()
    uri=raw.get('metadata',{}).get('projectRoot','')
    project_root=Path(urllib.parse.unquote(urllib.parse.urlparse(uri).path)).resolve()
    if not uri.startswith('file:') or project_root!=module_root:
        raise ValueError('SCIP_project_root_not_exact_index_command_module')
    docs=[]
    for document in raw.get('documents',[]):
        raw_path=document.get('relativePath');rel=None
        try:
            if not isinstance(raw_path,str) or not raw_path or PurePosixPath(raw_path).is_absolute() or '\x00' in raw_path:
                raise ValueError('invalid_SCIP_relative_document_path')
            actual=(project_root/raw_path).resolve()
            if not actual.is_relative_to(stage):raise ValueError('SCIP_document_resolves_outside_staged_snapshot')
            rel=str(actual.relative_to(stage))
            if rel not in source['files']:raise ValueError('SCIP_document_not_in_shared_source_manifest')
            origin=(Path(source['root'])/rel).resolve()
            if not origin.is_relative_to(Path(source['root']).resolve()):raise ValueError('SCIP_original_source_path_escape')
            raw_source=actual.read_bytes();cost['normalization_file_reads']=cost.get('normalization_file_reads',0)+1
            original=origin.read_bytes();cost['normalization_file_reads']+=1
            wanted=source['files'][rel]
            if hashlib.sha256(raw_source).hexdigest()!=wanted or hashlib.sha256(original).hexdigest()!=wanted:
                raise ValueError('SCIP_document_source_hash_mismatch')
            docs.append({**document,'relativePath':rel,'bound_source_sha256':wanted})
        except (ValueError,OSError) as error:
            unresolved.append({'snapshot':snapshot,'kind':'indexed_document_rejected','raw_relative_path':raw_path,'resolved_relative_path':rel,'module_root':module,'reason':str(error),'scope':'per_document_skip_not_snapshot_failure'})
    return docs

def _candidate_rows(case,all_docs,staged_roots,unresolved,budget,cost):
    changes=_delta_lines(case.get('diff',''));candidates=[]
    for snapshot,docs in all_docs.items():
        definitions=defaultdict(list);references=defaultdict(list);relationships=[];anchors=set()
        for doc in docs:
            path=doc['relativePath']
            raw_source=(Path(staged_roots[snapshot])/path).read_bytes();cost['normalization_file_reads']=cost.get('normalization_file_reads',0)+1
            if hashlib.sha256(raw_source).hexdigest()!=doc['bound_source_sha256']:
                unresolved.append({'kind':'SCIP_candidate_source_hash_mismatch','snapshot':snapshot,'path':path});continue
            source_rows=physical(raw_source.decode())
            for info in doc.get('symbols',[]):
                for rel in info.get('relationships',[]):
                    if rel.get('isImplementation'):relationships.append((_symbol_key(path,info['symbol']),_symbol_key(path,rel['symbol'])))
            for occurrence in doc.get('occurrences',[]):
                span=_range(occurrence)
                if not span or not occurrence.get('symbol'):continue
                start,end,raw_column=span;symbol=occurrence['symbol'];role=int(occurrence.get('symbolRoles',0));key=_symbol_key(path,symbol)
                if not 1<=start<=len(source_rows):unresolved.append({'kind':'SCIP_range_outside_source','snapshot':snapshot,'path':path,'range':list(span)});continue
                try:column,position_binding=_byte_column(source_rows[start-1],raw_column,doc.get('positionEncoding'))
                except (ValueError,UnicodeError) as error:
                    unresolved.append({'kind':'SCIP_position_unresolved','snapshot':snapshot,'path':path,'range':list(span),'error':str(error)});continue
                name=_name(symbol)
                if column:
                    suffix=source_rows[start-1].encode()[column-1:].decode();token=__import__('re').match(r'[^\W\d]\w*',suffix)
                    if token:name=token[0]
                row={'snapshot':snapshot,'path':path,'start_line':start,'end_line':end,'line':start,'column':column,'symbol':symbol,'symbol_name':name,'position_encoding':doc.get('positionEncoding','unspecified'),'raw_scip_column':raw_column,'position_binding':position_binding,'file_sha256':doc['bound_source_sha256']}
                (definitions if role & 1 else references)[key].append(row)
                enclosing=_range(occurrence,True) if role & 1 else None
                if any(start<=n<=end or enclosing and enclosing[0]<=n<=enclosing[1] for n in changes[snapshot].get(path,[])):anchors.add(key)
        for symbol in sorted(anchors):
            for relation,rows,query in [('definition',definitions[symbol],'body'),('reference',references[symbol],'references')]:
                for row in rows:
                    candidate={**row,'relation':relation,'query':query,'priority_reason':'symbol_at_or_enclosing_actual_diff_change','binding':{'provider':'SCIP','relation_semantics':'definition' if relation=='definition' else 'static_reference_not_proven_call_or_dataflow','runtime_dispatch':'unresolved','source_pool':'unchanged_shared_allowed_snapshot'},'expected_targets':definitions[symbol]}
                    candidate['id']='SC-'+digest(candidate)[:16];candidates.append(candidate)
        for concrete,abstract in relationships:
            if concrete not in anchors and abstract not in anchors:continue
            for row in definitions[concrete]:
                candidate={**row,'relation':'implementation_candidate','query':'body','priority_reason':'typed_SCIP_isImplementation_neighbor','binding':{'provider':'SCIP','abstract_symbol':abstract[1],'runtime_dispatch':'unresolved','source_pool':'unchanged_shared_allowed_snapshot'},'expected_targets':definitions[concrete],'implementation_query_sites':definitions[abstract]}
                candidate['id']='SC-'+digest(candidate)[:16];candidates.append(candidate)
    unique={c['id']:c for c in candidates};ordered=sorted(unique.values(),key=lambda c:(c['snapshot'],c['path'],c['start_line'],c['column'],c['relation'],c['symbol']))
    limit=int(budget.get('max_scip_candidates',1024))
    if len(ordered)>limit:unresolved.append({'kind':'candidate_limit','total':len(ordered),'kept':limit,'policy':'stable_source_order_not_gold'})
    return ordered[:limit]

def build_pair(case,out_dir,budget):
    """Build BASE/HEAD indices from exact staged snapshot manifests (no old reuse)."""
    out=Path(out_dir).resolve();out.mkdir(parents=True,exist_ok=True);started=time.monotonic();budget=budget or {}
    identity=digest({'language':case['language'],'snapshots':case['snapshots'],'environment_reference':case.get('environment_reference')})
    if (out/'batch.json').exists():
        previous=json.loads((out/'batch.json').read_text())
        if previous['case_manifest_sha256']!=identity:raise ValueError('SCIP prepared batch identity conflict')
        for snap in case['snapshots'].values():
            for rel,h in snap['files'].items():
                if sha(Path(snap['root'])/rel)!=h:raise ValueError('SCIP cached source changed')
        for snapshot in previous['snapshots'].values():
            for a in snapshot.get('artifacts',[]):
                if sha(a['path'])!=a['sha256']:raise ValueError('SCIP prepared artifact changed')
        return {**previous,'reuse':{'kind':'exact_new_run_preparation_replay','new_index_cost':0,'historical_cost':previous['cost']}}
    max_seconds=float(budget.get('max_index_seconds',120));max_bytes=int(budget.get('max_index_bytes',256000000));max_staged_bytes=int(budget.get('max_staged_source_bytes',max_bytes))
    cost={'index_native_calls':0,'index_seconds':0.0,'index_bytes':0,'staged_source_bytes':0,'model_calls':0};raw_paths=[];unresolved=[];snapshots={};all_docs={}
    def command(cmd,cwd,label,env):
        remaining=max_seconds-(time.monotonic()-started)
        if remaining<=0:raise TimeoutError('paired_index_time_budget_exhausted')
        cost['index_native_calls']+=1;begin=time.monotonic()
        stdout=out/(label+'.stdout');stderr=out/(label+'.stderr');receipt=out/(label+'.command.json')
        result=None;error=None
        try:
            with stdout.open('wb') as so,stderr.open('wb') as se:
                result=subprocess.run([str(x) for x in cmd],cwd=cwd,env=env,stdout=so,stderr=se,timeout=remaining)
            if stdout.stat().st_size+stderr.stat().st_size>max_bytes:raise ValueError('native_output_byte_limit')
            if result.returncode:raise RuntimeError('native_exit_'+str(result.returncode))
        except Exception as e:error=f'{type(e).__name__}: {e}'
        finally:
            _write(receipt,{'command':[str(x) for x in cmd],'cwd':str(cwd),'seconds':time.monotonic()-begin,'returncode':result.returncode if result else None,'error':error,'environment':{k:env.get(k) for k in ['GOTOOLCHAIN','GOWORK','GOPROXY','GOSUMDB','GOFLAGS','GOOS','GOARCH','CGO_ENABLED']}})
            raw_paths.extend(map(str,[stdout,stderr,receipt]))
        if error:raise RuntimeError(error)
        return stdout
    for snapshot in ['BASE','HEAD']:
        source=case['snapshots'].get(snapshot)
        if not source:
            snapshots[snapshot]={'status':'missing_snapshot'};unresolved.append({'snapshot':snapshot,'kind':'missing_snapshot'});continue
        stage=out/'staged'/snapshot;docs=[];artifacts=[];module_status=[];staged_files=[]
        try:
            if time.monotonic()-started>=max_seconds:raise TimeoutError('paired_index_time_budget_exhausted_before_snapshot_staging')
            stage.mkdir(parents=True,exist_ok=True)
            for rel,expected in sorted(source['files'].items()):
                if time.monotonic()-started>=max_seconds:raise TimeoutError('paired_index_time_budget_exhausted_during_staging')
                rel=_safe(rel);original=(Path(source['root'])/rel).resolve()
                if not original.is_relative_to(Path(source['root']).resolve()):raise ValueError('snapshot_source_path_mismatch:'+rel)
                if cost['staged_source_bytes']+original.stat().st_size>max_staged_bytes:raise RuntimeError('paired_staged_source_byte_budget_exhausted')
                dest=stage/rel;dest.parent.mkdir(parents=True,exist_ok=True);hasher=hashlib.sha256()
                with original.open('rb') as src,dest.open('xb') as dst:
                    while True:
                        if time.monotonic()-started>=max_seconds:raise TimeoutError('paired_index_time_budget_exhausted_during_file_copy')
                        chunk=src.read(1048576)
                        if not chunk:break
                        if cost['staged_source_bytes']+len(chunk)>max_staged_bytes:raise RuntimeError('paired_staged_source_byte_budget_exhausted')
                        dst.write(chunk);hasher.update(chunk);cost['staged_source_bytes']+=len(chunk)
                if hasher.hexdigest()!=expected:raise ValueError('snapshot_source_hash_mismatch:'+rel)
                staged_files.append({'path':rel,'sha256':expected,'bytes':dest.stat().st_size})
            env=dict(os.environ,GOTOOLCHAIN='local',GOWORK='off',GOPROXY='off',GOSUMDB='off',GOFLAGS='-mod=readonly')
            language=case['language'].lower()
            modules=_modules(source['files'],case.get('changed_files',[])) if language=='go' else ['.']
            if not modules:raise ValueError('no_go_module_in_allowed_snapshot')
            for mi,module in enumerate(modules):
                prefix=f'{snapshot}-{mi:02d}';folder=out/'indices'/snapshot/f'{mi:02d}';folder.mkdir(parents=True,exist_ok=True);index=folder/'index.scip'
                cwd=stage/module
                if language=='go':
                    go_env=command(['go','env','-json','GOVERSION','GOOS','GOARCH','CGO_ENABLED','GOFLAGS','GOTOOLCHAIN'],cwd,prefix+'-go-env',env)
                    cmd=[GO_INDEXER,'index','--module-version=claim-loop-static','--output',index.resolve(),'./...'];indexer=GO_INDEXER
                elif language=='python':
                    environment=case.get('environment',{}).get('scip_python_environment',[])
                    ep=folder/'python_environment.json';_write(ep,environment)
                    cmd=[PY_INDEXER,'index','--cwd',cwd.resolve(),'--project-name','claim-loop-project','--project-version','claim-loop-static','--environment',ep.resolve(),'--output',index.resolve()];indexer=PY_INDEXER
                    unresolved.append({'snapshot':snapshot,'kind':'python_analysis_environment_record','environment_reference':case.get('environment_reference'),'note':'SCIP environment metadata does not prove deployed interpreter or runtime dispatch'})
                else:raise ValueError('unsupported_SCIP_language')
                command(cmd,cwd,prefix+'-index',env)
                if not index.exists() or index.stat().st_size>max_bytes:raise ValueError('missing_or_oversized_index')
                cost['index_bytes']+=index.stat().st_size
                exported=command([EXPORTER,index.resolve()],cwd,prefix+'-export',env);raw=json.loads(exported.read_text())
                artifacts.extend({'path':str(p),'sha256':sha(p)} for p in [index,exported])
                docs.extend(_normalize_documents(raw,source,stage,module,snapshot,unresolved,cost))
                bundles=([indexer.resolve().parent/'dist/scip-python.js'] if language=='python' else [])
                module_status.append({'module_root':module,'documents':len(raw.get('documents',[])),'indexer_path':str(indexer.resolve()),'indexer_sha256':sha(indexer.resolve()),'indexer_bundle_hashes':{str(p):sha(p) for p in bundles},'exporter_sha256':sha(EXPORTER),'metadata':raw.get('metadata',{})})
            expected={p for p in source['files'] if p.endswith('.go' if language=='go' else '.py')};indexed={d['relativePath'] for d in docs};missing=sorted(expected-indexed)
            all_docs[snapshot]=docs
            snapshots[snapshot]={'status':'available' if docs else 'empty_index','source_manifest_sha256':digest(source['files']),'documents':len(docs),'source_file_count':len(expected),'indexed_source_file_count':len(indexed & expected),'unindexed_source_files':missing,'scope_complete':bool(docs) and not missing,'scope_complete_meaning':'file coverage only; semantic completeness not established','modules':module_status,'artifacts':artifacts,'binding':'static_snapshot_index_not_runtime_proof'}
            if missing:unresolved.append({'snapshot':snapshot,'kind':'unindexed_source_files','count':len(missing),'coverage_is_not_semantic_completeness':True})
        except Exception as e:
            snapshots[snapshot]={'status':'failed','source_manifest_sha256':digest(source['files']),'artifacts':artifacts,'error':f'{type(e).__name__}: {e}'}
            unresolved.append({'snapshot':snapshot,'kind':'index_failure','error':snapshots[snapshot]['error']})
        staging_receipt=out/(snapshot+'-staging.json')
        _write(staging_receipt,{'snapshot':snapshot,'completed_files':staged_files,'manifest_file_count':len(source['files']),'status':snapshots[snapshot]['status'],'elapsed_pair_seconds':time.monotonic()-started,'pair_staged_source_bytes':cost['staged_source_bytes'],'pair_max_staged_source_bytes':max_staged_bytes,'pair_max_index_seconds':max_seconds,'partial_staging_preserved':snapshots[snapshot]['status']=='failed'})
        raw_paths.append(str(staging_receipt))
    candidates=_candidate_rows(case,all_docs,{s:out/'staged'/s for s in all_docs},unresolved,budget,cost)
    cost['index_seconds']=time.monotonic()-started
    batch={'version':'paired-scip-shared-pool-priorities-v1','case_id':case.get('case_id',case.get('id')),'case_manifest_sha256':identity,'snapshots':snapshots,'candidates':candidates,'unresolved':unresolved,'raw_paths':raw_paths,'cost':cost,'scip_reference_is_call':False,'extra_source_pool_files':0,'dependency_setup_receipt':str(PY_INDEXER.parents[2]/'install_receipt.json') if PY_INDEXER.exists() else None}
    _write(out/'batch.json',batch);return batch

def recover_pair(case,original_dir,out_dir,budget=None):
    """Offline re-normalization only. Never invoke an indexer or change raw data."""
    original=Path(original_dir).resolve();out=Path(out_dir).resolve();budget=budget or {}
    if original==out or out.is_relative_to(original):raise ValueError('recovery_must_use_separate_new_directory')
    out.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    baseline=json.loads((original/'batch.json').read_text())
    identity=digest({'language':case['language'],'snapshots':case['snapshots'],'environment_reference':case.get('environment_reference')})
    if baseline['case_manifest_sha256']!=identity:raise ValueError('offline_recovery_case_identity_mismatch')
    cost={'index_native_calls':0,'staged_source_bytes':0,'normalization_file_reads':0,'model_calls':0}
    old_hashes={str(original/'batch.json'):sha(original/'batch.json')};unresolved=[];snapshots={};all_docs={};raw_paths=list(baseline.get('raw_paths',[]))
    for prior in baseline['snapshots'].values():
        for artifact in prior.get('artifacts',[]):
            if sha(artifact['path'])!=artifact['sha256']:raise ValueError('previously_recorded_native_artifact_hash_mismatch')
            old_hashes[artifact['path']]=artifact['sha256']
    for path in raw_paths:
        if Path(path).is_file():old_hashes[path]=sha(path)
    max_bytes=int(budget.get('max_index_bytes',256000000))
    for snapshot in ('BASE','HEAD'):
        source=case['snapshots'].get(snapshot);prior=baseline['snapshots'].get(snapshot,{})
        if not source:
            snapshots[snapshot]=dict(prior);continue
        stage=original/'staged'/snapshot;docs=[];artifacts=[];modules=[];native_complete=True
        roots=_modules(source['files'],case.get('changed_files',[])) if case['language'].lower()=='go' else ['.']
        for i,module in enumerate(roots):
            prefix=f'{snapshot}-{i:02d}';command_path=original/(prefix+'-index.command.json');export_command_path=original/(prefix+'-export.command.json');exported=original/(prefix+'-export.stdout')
            if not all(p.exists() for p in (command_path,export_command_path,exported)):
                native_complete=False;unresolved.append({'snapshot':snapshot,'kind':'offline_missing_completed_native_export','module_root':module,'original_status':prior.get('status'),'original_error':prior.get('error')});continue
            try:
                command=json.loads(command_path.read_text());export_command=json.loads(export_command_path.read_text())
                for label,receipt in [('index',command),('export',export_command)]:
                    if receipt.get('error') or receipt.get('returncode')!=0:raise ValueError('original_'+label+'_not_successful')
                if Path(command['cwd']).resolve()!=(stage/module).resolve():raise ValueError('original_index_cwd_not_expected_snapshot_module')
                args=command['command'];index=Path(args[args.index('--output')+1]).resolve()
                if not index.is_relative_to(original/'indices'):raise ValueError('original_index_not_in_preparation_directory')
                if Path(export_command['command'][1]).resolve()!=index:raise ValueError('export_does_not_reference_same_native_index')
                if index.stat().st_size>max_bytes or exported.stat().st_size>max_bytes:raise ValueError('original_artifact_exceeds_recorded_byte_bound')
                raw=json.loads(exported.read_text())
                module_docs=_normalize_documents(raw,source,stage,module,snapshot,unresolved,cost);docs.extend(module_docs)
                module_artifacts=[index,exported,command_path,export_command_path]
                for p in module_artifacts:
                    h=sha(p);old_hashes[str(p)]=h;artifacts.append({'path':str(p),'sha256':h});raw_paths.append(str(p))
                modules.append({'module_root':module,'documents':len(raw.get('documents',[])),'accepted_documents':len(module_docs),'metadata':raw.get('metadata',{}),'reused_native_index':True,'extra_native_calls':0,'original_index_command':str(command_path),'original_export_command':str(export_command_path)})
            except (ValueError,OSError,KeyError,IndexError) as error:
                native_complete=False;unresolved.append({'snapshot':snapshot,'kind':'offline_module_recovery_unresolved','module_root':module,'error':f'{type(error).__name__}: {error}'})
        if docs:
            language=case['language'].lower();expected={p for p in source['files'] if p.endswith('.go' if language=='go' else '.py')};indexed={d['relativePath'] for d in docs};missing=sorted(expected-indexed)
            all_docs[snapshot]=docs
            snapshots[snapshot]={'status':'available','documents':len(docs),'source_manifest_sha256':digest(source['files']),'source_file_count':len(expected),'indexed_source_file_count':len(indexed & expected),'unindexed_source_files':missing,'scope_complete':not missing and native_complete,'scope_complete_meaning':'file coverage only; semantic completeness not established','native_modules_complete':native_complete,'modules':modules,'artifacts':artifacts,'binding':'static_snapshot_index_not_runtime_proof','offline_recovery':{'original_status':prior.get('status'),'original_error':prior.get('error'),'per_document_rejection_does_not_invalidate_valid_documents':True}}
            if missing:unresolved.append({'snapshot':snapshot,'kind':'unindexed_source_files','count':len(missing),'coverage_is_not_semantic_completeness':True})
        else:
            # A timeout, source byte budget failure or absent native index is
            # preserved as a real failure. No fabricated empty-success index.
            snapshots[snapshot]={**prior,'offline_recovery':{'status':'no_valid_native_documents','native_modules_complete':native_complete,'extra_native_calls':0}}
    candidates=_candidate_rows(case,all_docs,{s:original/'staged'/s for s in all_docs},unresolved,budget,cost)
    cost['normalization_seconds']=time.monotonic()-started
    for p,h in old_hashes.items():
        if sha(p)!=h:raise ValueError('original_preparation_artifact_changed_during_recovery')
    receipt={'original_batch':str(original/'batch.json'),'original_artifact_hashes':old_hashes,'original_artifacts_unchanged':True,'extra_indexer_or_exporter_calls':0,'new_staged_bytes':0,'recovery_cost':cost,'historical_preparation_cost':baseline['cost'],'normalizer_sha256':sha(Path(__file__)),'scope':'offline_document_binding_repair_only; native budget/timeouts remain unchanged'}
    _write(out/'recovery.json',receipt)
    batch={'version':'paired-scip-shared-pool-offline-normalization-v2','case_id':baseline.get('case_id'),'id':case.get('id'),'case_manifest_sha256':identity,'snapshots':snapshots,'candidates':candidates,'unresolved':unresolved,'raw_paths':sorted(set(raw_paths+[str(out/'recovery.json')])),'cost':baseline['cost'],'recovery_cost':cost,'cost_scope':'historical native/staging prep cost preserved unchanged; separate actual offline recovery cost is additional','historical_unresolved':baseline.get('unresolved',[]),'scip_reference_is_call':False,'extra_source_pool_files':0,'recovery':receipt,'dependency_setup_receipt':baseline.get('dependency_setup_receipt')}
    _write(out/'batch.json',batch);return batch

def mirror_candidates(case,batch,backend,out_dir,limit=3):
    """Actual gold-blind mirror requests; no equivalence/runtime claim on misses."""
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=True);rows=[]
    chosen=sorted(batch['candidates'],key=lambda c:(c['snapshot'],c['path'],c['start_line'],c['relation'],c['symbol']))[:limit]
    for i,c in enumerate(chosen):
        site=c;query='references' if c['relation']=='reference' else 'definition'
        if c['relation']=='implementation_candidate':
            sites=c.get('implementation_query_sites',[])
            if not sites:rows.append({'candidate_id':c['id'],'status':'unresolved','reason':'No shared-pool abstract definition for an implementation mirror'});continue
            site=sites[0];query='implementation'
        if not site.get('column'):
            rows.append({'candidate_id':c['id'],'status':'unresolved','same_location_fact':False,'implementation_body_supplied':False,'reason':'SCIP column encoding is ambiguous on a nonASCII line'});continue
        q={'id':f'SCIP-mirror-{i}','claim_id':'preflight_mirror','kind':'definition_location','query':query,'snapshot':c['snapshot'],'file':site['path'],'line':site['start_line'],'column':site['column'],'symbol':site['symbol_name'],'reason':'Gold-blind first-sorted SCIP fact mirrored through identical-pool live LSP'}
        result=backend.query(q)
        expected=[c] if c['relation']=='reference' else c.get('expected_targets',[c])
        def exact_location(e,x):
            if e.get('kind') not in ('definition_location','reference_edge') or e.get('snapshot')!=c['snapshot'] or e.get('path')!=x['path'] or e.get('start_line')!=x['start_line']:return False
            target=e.get('binding',{}).get('native_target')
            if target:return target['line']==x['start_line'] and target['utf8_column']==x.get('column')
            payload=json.loads(e['content']);payload=payload.get('location',payload)
            if 'column_start' not in payload or not x.get('column'):return False
            rows=physical((Path(case['snapshots'][c['snapshot']]['root'])/x['path']).read_bytes().decode())
            prefix=rows[x['start_line']-1].encode()[:x['column']-1].decode()
            return payload['column_start']==len(prefix.encode('utf-16-le'))//2+1
        matched=any(exact_location(e,x) for e in result['evidence'] for x in expected)
        body_result=None
        body_targets=c.get('expected_targets',[c]);body_q=None
        if matched and body_targets:
            body_site=body_targets[0]
            body_q={**q,'id':q['id']+'-body','kind':'implementation_body','query':'body','file':body_site['path'],'line':body_site['start_line'],'column':body_site.get('column') or 0,'symbol':body_site['symbol_name'],'reason':'Separate body request at a shared-pool SCIP definition after matching native location; no runtime dispatch assertion'}
            body_result=backend.query(body_q)
        body_supplied=bool(body_result and any(e.get('kind')=='implementation_body' and e.get('mechanical_checks',{}).get('complete_unit') is True and e.get('snapshot')==c['snapshot'] and e.get('path')==x['path'] and e.get('start_line',0)<=x['start_line']<=e.get('end_line',-1) for e in body_result['evidence'] for x in body_targets))
        rows.append({'candidate_id':c['id'],'question':q,'body_question':body_q,'status':'matched_static_location_fact' if matched else 'unresolved','same_location_fact':matched,'implementation_body_supplied':body_supplied,'location_result':result,'body_result':body_result,'runtime_dispatch':'unresolved','note':'Location fact and supplied function body are separate; neither proves runtime call/dispatch'})
    receipt={'policy':'first stable source-sorted candidates, no labels or results consumed','rows':rows,'cost_scope':'Actual backend query costs retained in each result; caller also collects backend.close()'}
    _write(out/'mirror.json',receipt);return receipt
