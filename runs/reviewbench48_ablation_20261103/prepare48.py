"""Project final native manifests into public reviewer inputs, without gold."""
from common import ROOT, BUDGETS, load, sha, digest, write_new

def main():
 native=ROOT/'native_validation'
 raw=load(native/'cases_external.json')['cases']
 accounting=load(native/'accounting_summary.json')
 assert accounting['complete'] and len(raw)==48
 costs={r['case_id']:r for r in accounting['paired_index_cost']}
 rows=accounting['rows']+accounting['secondary_rows']
 out=[]
 for original in raw:
  c={k:original[k] for k in ('id','repo','nwo','pr_number','pr_url','base','head','title','body','language','changed_files','snapshots','diff_reference')}
  ref=c['diff_reference'];assert ref['complete'] and sha(ref['path'])==ref['sha256']
  c['diff']=open(ref['path'],encoding='utf-8').read()
  c['public_contract']='Original public PR title:\n'+c['title']+'\n\nOriginal public PR body:\n'+(c['body'] or '')
  c['paired_index_seconds']=costs[c['id']]['combined_primary_secondary_scip_wall_seconds']
  c['scip_usage_allowed']=c['paired_index_seconds']<=BUDGETS['max_index_seconds']
  cr=[r for r in rows if r['case_id']==c['id']]
  def ready(snapshot, primary=False):
   return any(r['snapshot']==snapshot and r['navigation_ready'] and (not primary or r['language']==c['language']) for r in cr)
  c['native_preflight']={'both_snapshot_ready':all(ready(s) for s in ('BASE','HEAD')),
   'primary_both_snapshot_ready':all(ready(s,True) for s in ('BASE','HEAD')),
   'snapshot_ready':{s:ready(s) for s in ('BASE','HEAD')},
   'scope':'Native definition/reference/index readiness, not defect-path or semantic correctness.',
   'accounting_reference':str(native/'accounting_summary.json')}
  c['source_pool_hash']=digest(c['snapshots'])
  out.append(c)
 assert len({c['id'] for c in out})==48
 write_new(ROOT/'prepared/cases.json',out)
 write_new(ROOT/'prepared/manifest.json',{'cases':48,'native_both_ready':sum(c['native_preflight']['both_snapshot_ready'] for c in out),
  'cases_sha256':sha(ROOT/'prepared/cases.json'),'native_cases_sha256':sha(native/'cases_external.json'),
  'accounting_sha256':sha(native/'accounting_summary.json'),'reviewer_inputs_exclude_private_gold':True,
  'source_admission':'Complete fixed public diff; no silent truncation or exclusion on model budget failure.', 'model_calls':0})
 print({'cases':48,'native_both_ready':sum(c['native_preflight']['both_snapshot_ready'] for c in out)})

if __name__=='__main__':main()
