"""Matched upper-bound admission; raw and no-attempt costs remain distinct."""
import hashlib,json,time
from pathlib import Path
import reliable_transport as rt
import cli_transport as legacy
import oar_core
from common import BUDGETS,MODEL,ROOT,load,write_new
from schemas import REVIEW

class BudgetedClient(rt.CliCompletionClient):
    def __init__(self,remaining,**kwargs):
        self.remaining=remaining
        super().__init__(**kwargs)
    def _prepare(self,text,temp):
        p=super()._prepare(text,temp);command=p['command'];assert command[-1]=='-'
        command[-1:-1]=['-c','features.rollout_budget.enabled=true',
          '-c',f'features.rollout_budget.limit_tokens={self.remaining}',
          '-c','features.rollout_budget.prefill_token_weight=1.0',
          '-c','features.rollout_budget.sampling_token_weight=1.0',
          '-c',f'features.rollout_budget.reminder_at_remaining_tokens=[{min(12000,self.remaining//2)}]']
        return p

def total_usage(result):
    total=result.get('actual_usage',{}).get('total',{})
    if not result.get('actual_usage',{}).get('per_turn'):return None
    if any(type(total.get(k))is not int for k in ('input_tokens','output_tokens')):return None
    return total['input_tokens']+total['output_tokens']

def physical_cost(result):
    if result.get('status')=='unattempted'and result.get('physical_attempt_count',0)==0:return 0
    return total_usage(result)

def admission(raw,used_tokens,used_calls,remaining_wall=None):
    text,_=legacy.compose_input(legacy.DEFAULT_ORDER,raw['system'],raw.get('shared',''),raw['prompt'],raw.get('suffix',''))
    compact=json.dumps(REVIEW);pretty=json.dumps(REVIEW,ensure_ascii=False,indent=2)+'\n'
    command=legacy.command_for(Path('/no_model_admission_schema'),MODEL)
    instruction=next(v.split('=',1)[1]for v in command if v.startswith('developer_instructions='))
    developer=json.loads(instruction)
    prompt_tokens=oar_core.count_reference_tokens(text);schema_tokens=oar_core.count_reference_tokens(compact)
    remaining=None if used_tokens is None else BUDGETS['max_model_tokens']-used_tokens
    wall=BUDGETS['max_wall_seconds']if remaining_wall is None else remaining_wall
    # Four workers, one CLI each, 3s staggering. Actual elapsed/overruns are kept.
    cli_wall=wall-12;required=prompt_tokens+schema_tokens+24000
    return dict(admitted=remaining is not None and used_calls<BUDGETS['max_model_calls']and remaining>=required and cli_wall>0,
      remaining=remaining,used_tokens=used_tokens,used_calls=used_calls,required_reserve=required,
      prompt_reference_tokens=prompt_tokens,schema_reference_tokens=schema_tokens,
      actual_pretty_schema_reference_tokens=oar_core.count_reference_tokens(pretty),
      developer_reference_tokens=oar_core.count_reference_tokens(developer),
      developer_included_in_24000_reserve_not_added_twice=True,
      composed_sha256=hashlib.sha256(text.encode()).hexdigest(),
      actual_pretty_schema_sha256=hashlib.sha256(pretty.encode()).hexdigest(),
      developer_sha256=hashlib.sha256(developer.encode()).hexdigest(),
      CLI_overhead_and_output_reserve=24000,remaining_active_wall_seconds=wall,
      start_gate_reserve_seconds=12,CLI_timeout_seconds=max(0,min(240,cli_wall)),
      scope='Same stdin+compact schema+24000 admission as parent; actual pretty schema/developer separately measured within reserve. No verified server output hard cap.')

def complete(folder,label,raw,used_tokens,used_calls,remaining_wall=None):
    began=time.monotonic();folder=Path(folder);dest=folder/label/'initial.result.json'
    if dest.exists():return load(dest)
    wall=(BUDGETS['max_wall_seconds']if remaining_wall is None else remaining_wall)-(time.monotonic()-began)
    a=admission(raw,used_tokens,used_calls,wall);write_new(folder/(label+'.admission.json'),a)
    if not a['admitted']:
        reason='active_wall_budget'if a['CLI_timeout_seconds']<=0 else'model_admission_budget'
        r=dict(status='unattempted',actual_usage={'per_turn':[],'total':{}},raw_response=None,
          failure_kinds=[reason],physical_attempt_count=0,attempts_without_reported_usage=0,
          input_sha256=a['composed_sha256'],measurement_status='known_not_issued',admission=a)
        write_new(folder/(label+'.unattempted.json'),r);write_new(dest,r);return r
    gate=rt.AdmissionGate(ROOT/'results/gate',max_live_calls=4,min_interval=3)
    client=BudgetedClient(a['remaining'],out_dir=folder,review_id=label,schema=REVIEW,model=MODEL,
      gate=gate,max_calls=1,timeout=a['CLI_timeout_seconds'])
    client.begin_stage('initial')
    try:client.complete(**raw)
    except rt.CallFailed:pass
    return load(dest)

def accepted_payload(result):
    if result['status']!='ok'or total_usage(result)is None:return None
    try:v=json.loads(result['raw_response'])
    except (TypeError,ValueError):return None
    return v if isinstance(v,dict)else None

def raw_review_payload(result):
    try:v=json.loads(result.get('raw_response')or'')
    except (TypeError,ValueError):return None
    return v if isinstance(v,dict)and isinstance(v.get('findings'),list)else None
