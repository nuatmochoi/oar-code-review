"""Audit frozen inputs, actual native traces, prompt binding and model responses."""
import ast,hashlib,json,re
from pathlib import Path
import experiment as e
from ace_review.context_adaptive import token_count,digest
from ace_review.protocol_hypothesis import mask_source
R=e.R;ROOT=e.ROOT;OLD=e.OLD
read=e.read;sha=e.sha
freeze=read(R/'freeze.json');seal=read(R/'algorithm_seal.json');labels=read(R/'labels.json')
checks={'algorithm_hashes':all(sha(ROOT/p)==h for p,h in seal['files'].items()),'freeze_hashes':all(sha(ROOT/p)==h for p,h in freeze['files'].items())}
workspace_errors=[]
for p in (R/'workspace_manifests').glob('*.json'):
    m=read(p);root=Path(m['root'])
    if read(root/'pyrightconfig.json')!=m['pyrightconfig']:workspace_errors.append((p.stem,'config'))
    for f,h in m['sources'].items():
        if digest((root/f).read_text())!=h:workspace_errors.append((p.stem,f))
checks['workspace_hashes']=not workspace_errors
packets={};packet_errors=[];native=0
for arm in ['C0','C1']:
    for p in sorted((R/'packets'/arm).glob('*.json')):
        d=read(p);a=d['audit'];stem=p.stem.rsplit('_',1)[0];kind=p.stem.rsplit('_',1)[1]
        if token_count(d['source'])>5200 or token_count(d['plain'])>800:packet_errors.append((arm,p.name,'budget'))
        if len(a['queries'])>(6 if kind=='initial' else 12) or any(q.get('error') or q['depth']>3 for q in a['queries']):packet_errors.append((arm,p.name,'queries'))
        if not a['root_coverage'] or not a['has_roots']:packet_errors.append((arm,p.name,'roots'))
        if arm=='C0' and p.read_bytes()!=(OLD/'packets'/p.name).read_bytes():packet_errors.append((arm,p.name,'baseline_changed'))
        m=read(R/'workspace_manifests'/f'{stem}.json');base_stem=stem[:-1]+('b' if stem[-1]=='a' else 'a')
        bm=read(R/'workspace_manifests'/f'{base_stem}.json')
        # Exact numbered source checks include any preserved module locations.
        lines_by_id={}
        for version,key,root in [('head','source_lines',Path(m['root'])),('base','base_lines',Path(bm['root']))]:
            for file,lines in a[key].items():
                source=(root/file).read_text();masked=mask_source(source).splitlines()
                for ln in lines:
                    if not 1<=ln<=len(masked) or f'{ln}: {masked[ln-1]}' not in d['source']:packet_errors.append((arm,p.name,'supplied_line',file,ln))
                lines_by_id[f'E-{version}-{digest(file)[:10]}']=set(lines)
        # Inventory hashes and source spans are independently checked against
        # the immutable file; exact inventory recomputation is policy-fixture tested.
        for inv in a.get('class_inventories',[]):
            source=(Path(m['root'])/inv['file']).read_text()
            raw='\n'.join(source.splitlines()[inv['line']-1:inv['end_line']])
            if digest(source)!=inv['full_file_source_sha256'] or digest(raw)!=inv['full_class_source_sha256']:packet_errors.append((arm,p.name,'inventory_provenance'))
            if '['+inv['evidence_id']+']' not in d['source']:packet_errors.append((arm,p.name,'inventory_not_supplied'))
        ids=set(re.findall(r'\[(E-[^\]]+)\]',d['source']+'\n'+d['plain']))|{'DIFF'}
        packets[(arm,stem,kind)]=(d,ids,lines_by_id,sha(p))
for p in (R/'lsp_initial').glob('*.json'):
    d=read(p);actual=[x for x in d['trace'] if x.get('method') in ['textDocument/definition','textDocument/references']]
    native+=len(actual)
    if len(actual)!=len(d['fixed_audit']['queries']):packet_errors.append(('C1',p.name,'native_trace_count'))
checks['packet_and_native_integrity']=not packet_errors
rows=[]
for job in freeze['jobs']:
    rid=job['run_id'];arm=job['arm'];stem=f"{job['case_id']}_{job['world']}";tp=R/'trajectories'/f'{rid}.json'
    base={**job,'label':labels[job['case_id']]['worlds'][job['world']]}
    if not tp.exists():rows.append({**base,'status':'missing','errors':['missing_trajectory']});continue
    t=read(tp);errors=[];warnings=[];recs=[]
    for stage,kind in [(1,'initial'),(2,'fixed')]:
        rp=R/'raw'/f'{rid}_s{stage}.json'
        if not rp.exists():continue
        rec=read(rp);recs.append(rec);inp=read(R/'inputs'/rp.name)
        pkt,ids,lineids,ph=packets[(arm,stem,kind)]
        if rec['status']!='ok':errors.append(f'transport_or_schema_{stage}')
        if rec['tool_audit']['tool_call_count']:errors.append(f'model_tools_{stage}')
        if rec['model']!=freeze['model']:errors.append('model')
        if digest(inp['prompt'])!=inp['prompt_sha256'] or rec['prompt_sha256']!=inp['prompt_sha256']:errors.append('prompt_binding')
        if inp['packet_sha256']!=ph or rec['packet_sha256']!=ph:errors.append('packet_binding')
        prior={k:v for k,v in recs[0]['response'].items() if k!='context_requests'} if stage==2 and recs[0].get('response') else None
        expected=e.build_prompt(read(R/'cases'/f'{stem}.json'),e.Packet(pkt),False,stage==1,prior)
        if inp['prompt']!=expected:errors.append('prompt_reconstruction')
        if stage==2 and rec.get('response'):
            final=rec['response'];review=final['review'];a=pkt['audit']
            def valid(k):
                if k in ids:return True
                match=re.fullmatch(r'(E-(?:head|base)-[a-f0-9]+):(\d+)(?:-(\d+))?',k)
                if match and match[1] in lineids:
                    lo=int(match[2]);hi=int(match[3] or match[2])
                    if lo<=hi and set(range(lo,hi+1)).issubset(lineids[match[1]]):warnings.append('validated_line_qualified:'+k);return True
                return False
            if (review['status']=='reported_issues')!=bool(review['issues']):errors.append('status_issue')
            for i,issue in enumerate(review['issues']):
                if any(not valid(k) for k in issue['evidence_ids']):errors.append(f'issue_evidence_{i}')
                if issue['file'] not in a['source_lines'] and issue['file'] not in a['base_lines']:errors.append(f'issue_file_{i}')
                if issue['line'] not in set(a['source_lines'].get(issue['file'],[]))|set(a['base_lines'].get(issue['file'],[])):warnings.append(f'issue_line_diff_check_{i}')
            for i,c in enumerate(final['checks']):
                if any(not valid(k) for k in c['evidence_ids']):errors.append(f'check_evidence_{i}')
                if any(j>=len(review['issues']) for j in c['issue_indices']):errors.append(f'issue_index_{i}')
    if len(recs)!=2:errors.append('incomplete_stages')
    pkt=packets[(arm,stem,'fixed')][0]
    rows.append({**base,'status':t['status'],'errors':errors,'warnings':warnings,'first':recs[0].get('response') if recs else None,'final':recs[1].get('response') if len(recs)==2 else None,'physical_calls':t.get('physical_calls',0),'source_tokens':token_count(pkt['source']),'observation_tokens':token_count(pkt['plain']),'seconds':sum(x['duration_seconds'] for x in recs),'usage':[u for x in recs for u in x.get('usage',[])]})
checks['complete_trajectories']=len(rows)==144 and all(x['status']!='missing' for x in rows)
checks['all_operational_valid']=all(x['status']=='ok' and not x['errors'] for x in rows)
checks['exact_model_record_inventory']={p.name for p in (R/'raw').glob('*_s[12].json')}=={f"{j['run_id']}_s{k}.json" for j in freeze['jobs'] for k in [1,2]}
out={'checks':checks,'actual_new_native_requests':native,'C0_new_native_requests':0,'C0_native_packet_reuse':'old sealed run','workspace_errors':workspace_errors,'packet_errors':packet_errors,'rows':rows}
(R/'audit_all.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in out.items() if k!='rows'},ensure_ascii=False,indent=2))
