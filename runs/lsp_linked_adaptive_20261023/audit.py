"""Operational and retrieval integrity; author grounding is a separate stage."""
import hashlib,json,re
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1]
from ace_review.context_adaptive import token_count
read=lambda p:json.loads(p.read_text())
f=read(R/'freeze.json');labels=read(R/'labels.json');seal=read(R/'algorithm_seal.json')
checks={'algorithm_hashes':all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in seal['files'].items()),'freeze_hashes':all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items())}
if (R/'runtime_checkpoint.json').exists():
 checkpoint=read(R/'runtime_checkpoint.json')
 checks['checkpoint_artifacts_preserved']=all(hashlib.sha256((R/p).read_bytes()).hexdigest()==h for p,h in checkpoint['preserved_hashes'].items())
workspace_errors=[]
for p in (R/'workspace_manifests').glob('*.json'):
 if read(R/'workspaces'/p.stem/'pyrightconfig.json')!=read(p)['pyrightconfig']:workspace_errors.append((p.stem,'pyrightconfig.json'))
 for file,h in read(p)['sources'].items():
  from ace_review.context_adaptive import digest
  if digest((R/'workspaces'/p.stem/file).read_text())!=h:workspace_errors.append((p.stem,file))
checks['workspace_hashes']=not workspace_errors
rows=[];native=0
for p in (R/'lsp_initial').glob('*.json'):native+=sum(x.get('method') in ['textDocument/definition','textDocument/references'] for x in read(p)['trace'])
for job in f['jobs']:
 rid=job['run_id'];tp=R/'trajectories'/f'{rid}.json'
 if not tp.exists():rows.append({**job,'status':'missing','label':labels[job['case_id']]['worlds'][job['world']]});continue
 t=read(tp);errors=[];warnings=[];records=[]
 for st in [1,2]:
  rp=R/'raw'/f'{rid}_s{st}.json'
  if rp.exists():
   rec=read(rp);records.append(rec)
   if rec['status']!='ok':errors.append('model_transport_or_schema_s'+str(st))
   if rec['tool_audit']['tool_call_count']:errors.append('model_native_tools')
   inp=read(R/'inputs'/f'{rid}_s{st}.json')
   if hashlib.sha256(inp['prompt'].encode()).hexdigest()!=inp['prompt_sha256']:errors.append('prompt_digest')
   if rec['prompt_sha256']!=inp['prompt_sha256']:errors.append('response_prompt_binding')
   if rec['model']!=f['model']:errors.append('model_mismatch')
 if len(records)!=2:errors.append('incomplete_model_stages')
 final=(records[-1].get('response') if len(records)==2 else None);first=records[0].get('response') if records else None
 rp=R/'retrieval'/f'{rid}.json';retrieval=read(rp) if rp.exists() else None
 if retrieval:
  a=retrieval['audit'];actual=sum(x.get('method') in ['textDocument/definition','textDocument/references'] for x in retrieval['actual_additional_native_trace']);native+=actual
  if token_count(retrieval['source'])>5200 or token_count(retrieval['observations'])>800 or len(a['queries'])>12:errors.append('budget')
  if job['arm'].endswith('1') and actual!=len(a['queries'])-len(read(R/'packets'/f'{job["case_id"]}_{job["world"]}_initial.json')['audit']['queries']):errors.append('native_trace_count')
  if any(q['error'] for q in a['queries']):errors.append('native_query_error')
  if final:
   rev=final['review']
   if (rev['status']=='reported_issues')!=bool(rev['issues']):errors.append('status_issue_consistency')
   inp=read(R/'inputs'/f'{rid}_s2.json')['prompt'];ids=set(re.findall(r'\[(E-[^\]]+)\]',inp))|{'DIFF'}
   def valid_citation(k):
    if k in ids:return True
    match=re.fullmatch(r'(E-(head|base)-[a-f0-9]+):(\d+)(?:-(\d+))?',k)
    if not match or match[1] not in ids:return False
    from ace_review.context_adaptive import digest
    lines=a['source_lines' if match[2]=='head' else 'base_lines']
    lo=int(match[3]);hi=int(match[4] or match[3])
    valid=lo<=hi and any(match[1]==f'E-{match[2]}-{digest(file)[:10]}' and set(range(lo,hi+1)).issubset(ls) for file,ls in lines.items())
    if valid:warnings.append('validated_line_qualified_citation:'+k)
    return valid
   for i,x in enumerate(rev['issues']):
    if any(not valid_citation(k) for k in x['evidence_ids']):errors.append(f'evidence_id_issue_{i}')
    if x['file'] not in a['source_lines'] and x['file'] not in a['base_lines']:errors.append(f'issue_file_not_supplied_{i}')
    if x['line'] not in set(a['source_lines'].get(x['file'],[]))|set(a['base_lines'].get(x['file'],[])):warnings.append(f'issue_line_requires_diff_check_{i}')
   for i,x in enumerate(final['checks']):
    if any(not valid_citation(k) for k in x['evidence_ids']):errors.append(f'evidence_id_check_{i}')
    if any(k>=len(rev['issues']) for k in x['issue_indices']):errors.append(f'issue_index_check_{i}')
 rows.append({**job,'status':t['status'],'label':labels[job['case_id']]['worlds'][job['world']],'errors':errors,'warnings':warnings,'first':first,'final':final,'request_count':t.get('request_count',0),'accepted_requests':t.get('accepted_requests',0),'source_tokens':retrieval['audit']['source_tokens'] if retrieval else 0,'observation_tokens':token_count(retrieval['observations']) if retrieval else 0,'physical_calls':len(records),'usage':[u for rec in records for u in rec.get('usage',[])],'seconds':sum(rec['duration_seconds'] for rec in records)})
checks['complete_trajectories']=len(rows)==288 and all(x['status']!='missing' for x in rows)
checks['all_operational_valid']=all(x['status']=='ok' and not x.get('errors') for x in rows)
expected_records={f"{job['run_id']}_s{stage}.json" for job in f['jobs'] for stage in [1,2]}
checks['exact_model_record_inventory']={p.name for p in (R/'raw').glob('*_s[12].json')}==expected_records
summary={}
for arm in ['P0Q0','P1Q0','P0Q1','P1Q1']:
 rr=[x for x in rows if x['arm']==arm];valid=[x for x in rr if x['status']=='ok' and not x.get('errors')];summary[arm]={'trajectories':len(rr),'valid':len(valid),'raw_unsafe_reported':sum(x['label']=='unsafe' and x['final']['review']['status']=='reported_issues' for x in valid),'raw_safe_no_issue':sum(x['label']=='safe' and x['final']['review']['status']=='no_supported_issue' for x in valid),'safe_reported':sum(x['label']=='safe' and x['final']['review']['status']=='reported_issues' for x in valid),'abstentions':sum(x['final']['review']['status']=='insufficient_evidence' for x in valid),'accepted_gap_requests':sum(x.get('accepted_requests',0) for x in rr),'physical_calls':sum(x.get('physical_calls',0) for x in rr)}
result={'checks':checks,'summary_ungraded':summary,'actual_native_semantic_requests':native,'rows':rows,'workspace_errors':workspace_errors,'note':'Raw statuses are NOT grounded accuracy. Prior fixed/initial native traversals are reused across repeats/arms, never counted as fresh native requests. Model responses are all fresh.'}
(R/'audit_all.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))
