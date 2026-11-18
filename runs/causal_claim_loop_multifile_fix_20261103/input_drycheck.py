"""Prepare exact stdin/CLI components without issuing a model/native call.

Initial inputs are actual planned public inputs. Later-stage and stress inputs
use explicitly synthetic schema-valid previous outputs, never old model answers.
Provider-billed tokens remain unmeasured before a model call.
"""
import copy,importlib.util,json,hashlib,math
from pathlib import Path
from unittest.mock import patch
import jsonschema
import oar_core as oc
import cli_transport as legacy
from common import ROOT,MODEL,BUDGETS,load,write_new,sha,render_evidence,source_tokens
from experiment import initial_evidence
from claim_controller import ClaimController
from prompts import raw_prompt
from budget_transport import admission_plan,BudgetedClient
from schemas import REVIEW
from index_selection import reference

def fixture(reason='Dry-run fixture only; no semantic judgment.'):
 return dict(assessment='uncertain',findings=[],claims=[],questions=[],reasoning=reason,termination_reason='continue')
def stress_text(target):
 unit='SYNTHETIC_SIZE_FIXTURE_0123456789 '
 return unit*(math.ceil(target/oc.count_reference_tokens(unit))+1)
def prepare(case,evidence,name,stage,used,calls,previous=None,feedback=None,index=None,synthetic=False):
 folder=ROOT/'verification/inputs'/case['id']/name;folder.mkdir(parents=True,exist_ok=False)
 raw=raw_prompt(case,evidence,stage,previous,feedback,None,index)
 text,parts=legacy.compose_input(legacy.DEFAULT_ORDER,raw['system'],raw.get('shared',''),raw['prompt'],raw.get('suffix',''))
 plan=admission_plan(raw,used,calls)
 client=BudgetedClient(plan['remaining'],out_dir=folder/'no_call_client',review_id='prepared',schema=REVIEW,model=MODEL,max_calls=1,spawner=lambda *a,**k: (_ for _ in ()).throw(AssertionError('dry only')))
 cli_folder=folder/'provider_cli';cli_folder.mkdir();prepared=client._prepare(text,cli_folder)
 assert prepared['reference_tokens']==plan['canonical_input_reference_tokens']
 developer=next(x[len('developer_instructions='):] for x in prepared['command'] if x.startswith('developer_instructions='));developer=json.loads(developer)
 provider_known=plan['canonical_input_reference_tokens']+plan['schema_reference_tokens']+oc.count_reference_tokens(developer)
 raw_record=dict(case_id=case['id'],stage=stage,scenario=name,raw=raw,composed_text=text,parts=parts,
  composed_sha256=oc._sha(text),stdin_bytes_sha256=hashlib.sha256(text.encode()).hexdigest(),
  reference_tokens_o200k=plan['canonical_input_reference_tokens'],provider_reported_tokens=None,
  provenance='actual planned initial input' if not synthetic else 'synthetic previous-state/size scenario; never an observed or replayed model answer')
 write_new(folder/'input.json',raw_record);write_new(folder/'stdin.txt',text)
 write_new(folder/'provider_preparation.json',dict(command=prepared['command'],schema_sha256=sha(cli_folder/'schema.json'),
  developer_instruction=developer,known_provider_component_reference_tokens=provider_known,
  provider_reported_tokens=None,unknown_components='CLI/server framing and provider tokenizer/usage are unmeasured without an actual call',
  executable_started=False,environment_secret_values_recorded=False))
 return dict(case_id=case['id'],scenario=name,stage=stage,synthetic_previous_or_stress=synthetic,
  input_path=str(folder/'input.json'),input_sha256=sha(folder/'input.json'),stdin_sha256=sha(folder/'stdin.txt'),
  canonical_chars=len(text),diff_text_exposures=text.count(case['diff']),diff_citation_ids=sum(b['kind']=='diff_hunk' for b in evidence),
  source_union_reference_tokens=source_tokens(evidence),known_provider_component_reference_tokens=provider_known,admission=plan)

def run():
 cases=load(ROOT/'prepared/cases.json');rows=[];old_comparisons=[]
 spec=importlib.util.spec_from_file_location('v1_renderer',ROOT.parent/'causal_claim_loop_20261103/common.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
 with patch('subprocess.Popen',side_effect=AssertionError('NO executable allowed during input drycheck')),patch('subprocess.run',side_effect=AssertionError('NO executable allowed during input drycheck')):
  for case in cases:
   evidence=initial_evidence(case);controller=ClaimController(evidence)
   feedback=dict(state=controller.ledger.export(),last_update=None,rejection_history=[],mechanical_truth_only=True)
   first=prepare(case,evidence,'initial','initial',0,0);assert first['admission']['admitted'];rows.append(first)
   raw=raw_prompt(case,evidence,'initial');new_render=render_evidence(evidence);old_raw={**raw,'prompt':raw['prompt'].replace(new_render,old.render_evidence(evidence),1)}
   old_text,_=legacy.compose_input(legacy.DEFAULT_ORDER,old_raw['system'],'',old_raw['prompt'],'')
   old_comparisons.append(dict(case_id=case['id'],v1_serialization_reconstructed_from_same_public_input_not_old_response=True,
    old_canonical_chars=len(old_text),old_diff_exposures=old_text.count(case['diff']),old_reference_tokens=oc.count_reference_tokens(old_text),
    new_canonical_chars=first['canonical_chars'],new_diff_exposures=first['diff_text_exposures'],new_reference_tokens=first['admission']['canonical_input_reference_tokens']))
   index=load(reference(case)['path']);previous=fixture();jsonschema.validate(previous,REVIEW)
   for arm in ('B','C','D'):
    used=first['known_provider_component_reference_tokens']+oc.count_reference_tokens(json.dumps(previous))
    for calls,stage in ((1,'verify'),(2,'final')):
     row=prepare(case,evidence,f'{arm}_{stage}_nominal_fixture',stage,used,calls,previous,feedback,index if arm=='D' else None,True);rows.append(row);assert row['admission']['admitted']
     used+=row['known_provider_component_reference_tokens']+oc.count_reference_tokens(json.dumps(previous))
   # A budget-feasible but long earlier output can be repeated in prior review
   # and controller state. The schema has no string/array length bound, so this
   # is a stress case, not an asserted mathematical maximum or observed answer.
   long=stress_text(48000);previous=fixture();previous['claims']=[dict(id='DRY_SIZE_ONLY',claim=long,trigger='Synthetic size fixture; no defect assertion.',action='new',change_basis='none',reason='Dry-run only.',conditions=[],challenges=[],termination='unresolved')];jsonschema.validate(previous,REVIEW)
   stress_feedback=copy.deepcopy(feedback);stress_feedback['state']['candidates']=[dict(id='DRY_SIZE_ONLY',claim=long,scope='Hypothetical schema-size boundary only')]
   for calls,stage in ((1,'verify'),(2,'final')):
    used=first['known_provider_component_reference_tokens']+oc.count_reference_tokens(json.dumps(previous))+(0 if stage=='verify' else first['known_provider_component_reference_tokens'])
    assert used<BUDGETS['max_model_tokens']
    row=prepare(case,evidence,'D_'+stage+'_long_prior_state_stress',stage,used,calls,previous,stress_feedback,index,True);rows.append(row);assert not row['admission']['admitted']
 summary=dict(passed=all(r['diff_text_exposures']==1 for r in rows),model_calls=0,native_calls=0,rows=rows,comparisons=old_comparisons,
  provider_reported_tokens=None,provider_token_scope='Only reference counts of exact planned stdin/schema/developer components; no model call was made.',
  worst_case_scope='Schema strings/arrays are unbounded; nominal later prompts and explicit budget-feasible long-prior-state stress only, not an exhaustive finite upper bound.',
  remaining_actual_source_capacity={c['id']:BUDGETS['max_source_tokens']-source_tokens(initial_evidence(c)) for c in cases},
  runtime_gate='Unchanged per-call V1 admission formula is re-evaluated using actual new responses and actual supplied evidence at every call.')
 write_new(ROOT/'verification/input_drycheck.json',summary);return dict(passed=summary['passed'],inputs=len(rows),comparisons=old_comparisons)
if __name__=='__main__':print(json.dumps(run(),ensure_ascii=False))
