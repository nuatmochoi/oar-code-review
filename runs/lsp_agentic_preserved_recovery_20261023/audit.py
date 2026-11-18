"""Audit frozen evidence, preserved source, actual native retrieval and fresh reviews."""
import collections,json,re
from pathlib import Path
import experiment as e
from ace_review.context_adaptive import digest,token_count
from ace_review.protocol_hypothesis import mask_source
R=e.R;ROOT=e.ROOT;read=e.read;sha=e.sha
f=read(R/'freeze.json');seal=read(R/'algorithm_seal.json');labels=read(R/'labels.json')
checks={'algorithm_hashes':all(sha(ROOT/p)==h for p,h in seal['files'].items()),'freeze_hashes':all(sha(ROOT/p)==h for p,h in f['files'].items())}
workspace_errors=[];manifests={p.stem:read(p) for p in (R/'workspace_manifests').glob('*.json')}
for stem,m in manifests.items():
 root=Path(m['root'])
 if read(root/'pyrightconfig.json')!=m['pyrightconfig']:workspace_errors.append([stem,'config'])
 for file,h in m['sources'].items():
  if digest((root/file).read_text())!=h:workspace_errors.append([stem,file])
checks['workspace_hashes']=not workspace_errors
packet_errors=[];packet_cache={};coverage=[]
header=re.compile(r'^\[(E-(head|base)-[a-f0-9]+)\] (head|base) ([^;]+);')
def packet_audit(packet,stem,key,seed=None):
 a=packet['audit'];ids=set(re.findall(r'\[(E-[^\]]+)\]',packet['source']+'\n'+packet['plain']))|{'DIFF'};lineids={};supplied=collections.defaultdict(dict);current=None
 for line in packet['source'].splitlines():
  match=header.match(line)
  if match:current=(match[2],match[4]);continue
  if line.startswith('[E-'):current=None;continue
  m=re.match(r'^(\d+): (.*)$',line)
  if current and m:
   ln=int(m[1]);prev=supplied[current].get(ln)
   if prev is not None and prev!=m[2]:packet_errors.append([key,'conflicting_source',current,ln])
   supplied[current][ln]=m[2]
 if token_count(packet['source'])>5200 or token_count(packet['plain'])>800:packet_errors.append([key,'budget'])
 if len(a['queries'])>18 or len(a.get('additional_queries',[]))>6 or any(q['depth']>3 for q in a['queries']):packet_errors.append([key,'query_budget_depth'])
 if not a['root_coverage'] or not a['has_roots']:packet_errors.append([key,'roots'])
 if seed is not None:
  if not packet['source'].startswith(seed['source']):packet_errors.append([key,'prefix_lost'])
  for k in ['source_lines','base_lines']:
   for file,lines in seed['audit'][k].items():
    if not set(lines)<=set(a[k].get(file,[])):packet_errors.append([key,'audit_protection',file])
 for version,k,worldstem in [('head','source_lines',stem),('base','base_lines',stem[:-1]+('b' if stem[-1]=='a' else 'a'))]:
  root=Path(manifests[worldstem]['root'])
  for file,lines in a[k].items():
   actual=mask_source((root/file).read_text()).splitlines();rendered=supplied[(version,file)]
   if set(rendered)!=set(lines):packet_errors.append([key,'rendered_vs_audit_lines',version,file])
   for ln in lines:
    if not 1<=ln<=len(actual) or rendered.get(ln)!=actual[ln-1]:packet_errors.append([key,'source_content',version,file,ln])
   lineids[f'E-{version}-{digest(file)[:10]}']=set(lines)
 for version,file in supplied:
  if file not in a['source_lines' if version=='head' else 'base_lines']:packet_errors.append([key,'untracked_source',version,file])
 for inv in a.get('class_inventories',[]):
  source=(Path(manifests[stem]['root'])/inv['file']).read_text();raw='\n'.join(source.splitlines()[inv['line']-1:inv['end_line']])
  if digest(source)!=inv['full_file_source_sha256'] or digest(raw)!=inv['full_class_source_sha256']:packet_errors.append([key,'inventory_hash'])
  if inv['evidence_id'] not in ids:packet_errors.append([key,'inventory_id'])
 return ids,lineids
seeds={};fixed={};native_counts={'fixed_preparation':0,'reused_fixed_preparation':0,'agentic_followup':0}
for stem in sorted(manifests):
 seed=read(R/'packets'/'seed'/f'{stem}_initial.json');legacy=read(R/'packets'/'legacy'/f'{stem}_fixed.json');previous=read(e.PREVIOUS/'packets'/'C0'/f'{stem}_fixed.json')
 if legacy!=previous or seed['source']!=legacy['source'] or seed['plain']!=legacy['plain']:packet_errors.append([stem,'legacy_changed'])
 seeds[stem]=seed;packet_cache[('seed',stem)]=packet_audit(seed,stem,'seed:'+stem)
 p=read(R/'packets'/'F'/f'{stem}_fixed.json');fixed[stem]=p;packet_cache[('F',stem)]=packet_audit(p,stem,'F:'+stem,seed)
 trace=read(R/'lsp_fixed'/f'{stem}.json')['actual_trace'];n=sum(x.get('method') in ['textDocument/definition','textDocument/references'] for x in trace);native_counts['reused_fixed_preparation']+=n
 if n!=len(p['audit'].get('additional_queries',[])):packet_errors.append([stem,'F_native_trace_count'])
rows=[];first_hashes=collections.defaultdict(set)
def valid_id(k,ids,lineids):
 if k in ids:return True
 m=re.fullmatch(r'(E-(?:head|base)-[a-f0-9]+):(\d+)(?:-(\d+))?',k)
 return bool(m and m[1] in lineids and int(m[2])<=int(m[3] or m[2]) and set(range(int(m[2]),int(m[3] or m[2])+1))<=lineids[m[1]])
for job in f['jobs']:
 rid=job['run_id'];stem=f"{job['case_id']}_{job['world']}";base={**job,'label':labels[job['case_id']]['worlds'][job['world']]};tp=R/'trajectories'/f'{rid}.json';errors=[];warnings=[];recs={};seed=seeds[stem];final_packet=seed
 if not tp.exists():rows.append({**base,'status':'missing','errors':['missing_trajectory']});continue
 t=read(tp);retp=R/'retrieval'/f'{rid}.json'
 if retp.exists():
  ret=read(retp);final_packet={'source':ret['source'],'plain':ret['observations'],'audit':ret['audit']}
  if job['arm']=='F':
   if final_packet!=fixed[stem]:errors.append('fixed_packet_changed')
   ids,lineids=packet_cache[('F',stem)]
  else:
   ids,lineids=packet_audit(final_packet,stem,rid,seed)
   n=sum(x.get('method') in ['textDocument/definition','textDocument/references'] for x in ret['actual_additional_native_trace']);native_counts['agentic_followup']+=n
   if n!=len(final_packet['audit'].get('additional_queries',[])):packet_errors.append([rid,'G_native_trace_count'])
  if ret['packet_sha256']!=e.packet_digest(final_packet):errors.append('retrieval_packet_hash')
  final_ids=(ids,lineids)
 else:final_ids=packet_cache[('seed',stem)]
 for stage,packet,idpair in [(1,seed,packet_cache[('seed',stem)]),(2,final_packet,final_ids)]:
  rp=R/'raw'/f'{rid}_s{stage}.json'
  if not rp.exists():continue
  rec=read(rp);recs[stage]=rec;inp=read(R/'inputs'/rp.name);ids,lineids=idpair
  if rec['status']!='ok':errors.append(f'transport_or_schema_{stage}')
  if rec['tool_audit']['tool_call_count']:errors.append(f'model_tools_{stage}')
  if rec['model']!=f['model']:errors.append('model')
  if digest(inp['prompt'])!=inp['prompt_sha256'] or inp['prompt_sha256']!=rec['prompt_sha256']:errors.append('prompt_binding')
  ph=e.packet_digest(packet)
  if inp['packet_sha256']!=ph or rec['packet_sha256']!=ph:errors.append('packet_binding')
  prior={k:v for k,v in recs[1]['response'].items() if k!='context_requests'} if stage==2 and recs.get(1,{}).get('response') else None
  if inp['prompt']!=e.build_prompt(read(R/'cases'/f'{stem}.json'),packet,stage==1,prior):errors.append('prompt_reconstruction')
  if stage==1:first_hashes[stem].add(inp['prompt_sha256'])
  response=rec.get('response')
  if not response:continue
  review=response['review'];a=packet['audit'];stage_errors=[]
  if (review['status']=='reported_issues')!=bool(review['issues']):stage_errors.append('status_issue')
  for section,items in [('issue',review['issues']),('check',response['checks'])]:
   for i,item in enumerate(items):
    if any(not valid_id(k,ids,lineids) for k in item['evidence_ids']):stage_errors.append(f'{section}_evidence_{i}')
    if section=='issue':
     if item['file'] not in a['source_lines'] and item['file'] not in a['base_lines']:stage_errors.append(f'issue_file_{i}')
     if item['line'] not in set(a['source_lines'].get(item['file'],[]))|set(a['base_lines'].get(item['file'],[])):warnings.append(f's{stage}:issue_line_diff_check_{i}')
    elif any(j<0 or j>=len(review['issues']) for j in item['issue_indices']):stage_errors.append(f'issue_index_{i}')
  if stage==2:errors.extend(stage_errors)
  else:warnings.extend('first_stage:'+x for x in stage_errors)
  assess=response['context_assessment'];used={x['evidence_id'] for x in assess['used_evidence']};dismissed={x['evidence_id'] for x in assess['dismissed_evidence']};missing={x['evidence_id'] for x in assess['missing_connections']};invalid=sorted(k for k in used|dismissed|missing if not valid_id(k,ids,lineids))
  cited={k for item in review['issues']+response['checks'] for k in item['evidence_ids']};sourceids=set(re.findall(r'\[(E-[^\]]+)\]',packet['source']))
  normalize=lambda values:{re.sub(r':\d+(?:-\d+)?$','',k) for k in values}
  coverage.append({'run_id':rid,'arm':job['arm'],'stage':stage,'available_source_ids':sorted(sourceids),'used_ids':sorted(used),'dismissed_ids':sorted(dismissed),'missing_anchor_ids':sorted(missing),'invalid_assessment_ids':invalid,'used_dismissed_overlap':sorted(normalize(used)&normalize(dismissed)),'unaccounted_source_ids':sorted(sourceids-normalize(used)-normalize(dismissed)),'cited_ids':sorted(cited),'note':'Self-reported use and file-ID citation do not prove use of newly supplied lines.'})
 if len(recs)!=2:errors.append('incomplete_stages')
 if any(q.get('error') for q in final_packet['audit'].get('additional_queries',[])):errors.append('native_query_error')
 rows.append({**base,'status':t['status'],'errors':errors,'warnings':warnings,'first':recs.get(1,{}).get('response'),'final':recs.get(2,{}).get('response'),'physical_calls':t['physical_calls'],'source_tokens':token_count(final_packet['source']),'observation_tokens':token_count(final_packet['plain']),'seconds':sum(x['duration_seconds'] for x in recs.values()),'usage':[u for x in recs.values() for u in x.get('usage',[])]})
checks.update(packet_and_native_integrity=not packet_errors,shared_first_prompts=all(len(v)==1 for v in first_hashes.values()),complete_trajectories=len(rows)==144 and all(x['status']!='missing' for x in rows),all_operational_valid=all(x['status']=='ok' and not x['errors'] for x in rows))
actual={p.name for p in (R/'raw').glob('*_s[12].json')};planned={f"{j['run_id']}_s{k}.json" for j in f['jobs'] for k in [1,2]};checks['exact_model_record_inventory']=actual==planned
out={'checks':checks,'actual_new_native_requests':native_counts['agentic_followup'],'native_counts':native_counts,'cached_seed_native_queries':sum(len(p['audit']['queries']) for p in seeds.values()),'workspace_errors':workspace_errors,'packet_errors':packet_errors,'rows':rows}
(R/'audit_all.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));(R/'context_consumption_audit.json').write_text(json.dumps({'rows':coverage,'assessment_errors_are_auxiliary':True},ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in out.items() if k!='rows'},ensure_ascii=False,indent=2))
