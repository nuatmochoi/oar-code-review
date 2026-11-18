"""Mechanical supplement gate; no gold parsing, model calls, or native queries."""
import json,re,time
from pathlib import Path
from common import ROOT,BUDGETS,MODEL,REPEATS,ARMS,load,sha,digest,write_new,render_evidence
from experiment import initial_evidence
from claim_controller import public_diff_evidence,ClaimController
from index_selection import reference

def run():
 assert not (ROOT/'results').exists()
 origin=load(ROOT/'origin_manifest.json');old=Path(origin['parent_run']);assert sha(old/'freeze.json')==origin['parent_freeze']['sha256']
 errors=[];unchanged=[];modified=[]
 approved={'common.py','budget_transport.py','experiment.py','grading.py'}
 for rel,info in origin['copied_files'].items():
  assert sha(info['origin'])==info['sha256'],('V1 changed',rel)
  if sha(ROOT/rel)==info['sha256']:unchanged.append(rel)
  else:assert rel in approved,('unexpected code/data change',rel);modified.append(rel)
 cases=load(ROOT/'prepared/cases.json');parent_cases={c['id']:c for c in load(old/'prepared/cases.json')};assert {c['id'] for c in cases}=={'HO_GO01_a','HO_GO01_b'}
 checks=[];source_paths={}
 for c in cases:
  assert c==parent_cases[c['id']],('case object changed',c['id'])
  for snapshot,spec in c['snapshots'].items():
   paths=set(c['changed_files'])|{b['path'] for b in c['initial_blocks'] if b['snapshot']==snapshot}
   for rel in paths:
    if rel in spec['files']:p=Path(spec['root'])/rel;h=spec['files'][rel]
    elif rel in spec.get('external_files',{}):e=spec['external_files'][rel];p=Path(e['absolute_source']);h=e['sha256']
    else:continue # A deleted file can legitimately be absent in one snapshot.
    source_paths[str(p)]=h
  ref=reference(c);assert sha(ref['path'])==ref['sha256']
  index=load(ref['path']);assert index['case_manifest_sha256']==digest(dict(language=c['language'],snapshots=c['snapshots'],environment_reference=c.get('environment_reference')))
  ev=initial_evidence(c);ClaimController(ev)
  rendered=render_evidence(ev)
  maps=[json.loads(line[len('<diff_citation '):-1]) for line in rendered.split('\n') if line.startswith('<diff_citation ')]
  for b in public_diff_evidence(c['diff']):
   m=next(x for x in maps if x['id']==b['id']);expanded={n for a,z in m['supplied_physical_ranges'] for n in range(a,z+1)}
   assert expanded==set(map(int,b['physical_rows']));assert m['path']==b['path'] and m['snapshot']==b['snapshot']
  checks.append(dict(case_id=c['id'],case_object_sha256=digest(c),source_pool_sha256=digest(c['snapshots']),diff_citation_maps=len(maps),index_reference=ref,source_blocks_preserved=True,physical_row_map_exact=True))
 for p,h in source_paths.items():assert sha(p)==h,('shared source changed',p)
 for name in ('prompts.py','schemas.py','EVALUATION_KO.md','go_tools.py','oar_core.py','cli_transport.py','reliable_transport.py'):
  assert sha(ROOT/name)==sha(old/name),('shared method changed',name)
 # Compare budgets by parsing only their literal assignment; no parent runtime.
 import ast
 def budgets(path):
  tree=ast.parse(Path(path).read_text());node=next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='BUDGETS' for t in n.targets));return {k.arg:ast.literal_eval(k.value) for k in node.keywords}
 assert BUDGETS==budgets(old/'common.py')
 dry=load(ROOT/'verification/input_drycheck.json');assert dry['passed'] and dry['model_calls']==dry['native_calls']==0
 log=(ROOT/'verification/tests_final.log').read_text();assert log.rstrip().endswith('OK');n=int(re.search(r'Ran (\d+) tests',log)[1])
 tests=dict(passed=True,tests=n,log_path=str(ROOT/'verification/tests_final.log'),log_sha256=sha(ROOT/'verification/tests_final.log'),model_calls=0,native_calls=0,scope='Unit/mocked controller, renderer, binding, budget admission only')
 write_new(ROOT/'verification/tests_final.json',tests)
 report=dict(passed=True,created_at=time.time(),model_calls=0,native_calls=0,parent_source_files_unchanged=True,
  copied_unchanged=unchanged,explicitly_modified=modified,case_checks=checks,verified_shared_source_files=len(source_paths),
  source_body_objects_same=True,system_prompt_schema_rubric_budgets_same=True,index_pools_same=True,
  parent_freeze_reference=origin['parent_freeze'],drycheck_path=str(ROOT/'verification/input_drycheck.json'),drycheck_sha256=sha(ROOT/'verification/input_drycheck.json'),
  nominal_inputs_are_not_claimed_as_observed_followup_answers=True,provider_actual_tokens_unmeasured=True,
  separate_supplement=True,merge_with_V1_as_replication=False,review_results_expected=len(cases)*REPEATS*len(ARMS),maximum_physical_model_calls=len(cases)*REPEATS*7)
 write_new(ROOT/'audit/pre_model_audit.json',report);return dict(passed=True,tests=n,verified_shared_source_files=len(source_paths),reviews=report['review_results_expected'],maximum_physical_model_calls=report['maximum_physical_model_calls'])
if __name__=='__main__':print(json.dumps(run()))
