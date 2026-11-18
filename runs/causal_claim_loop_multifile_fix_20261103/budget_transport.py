"""Per-call CLI tracking plus controller admission using reported total usage.

CLI tracking is not asserted to impose a server-side max_output_tokens limit.
Unknown usage and an overrun are preserved failures, never measured zero.
"""
import json
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
        p=super()._prepare(text,temp)
        command=p['command'];assert command[-1]=='-'
        command[-1:-1]=['-c','features.rollout_budget.enabled=true',
            '-c',f'features.rollout_budget.limit_tokens={self.remaining}',
            '-c','features.rollout_budget.prefill_token_weight=1.0',
            '-c','features.rollout_budget.sampling_token_weight=1.0',
            '-c',f'features.rollout_budget.reminder_at_remaining_tokens=[{min(12000,self.remaining//2)}]']
        return p

def total_usage(result):
    total=result.get('actual_usage',{}).get('total',{})
    if not result.get('actual_usage',{}).get('per_turn'):return None
    if 'input_tokens' not in total or 'output_tokens' not in total:return None
    return total['input_tokens']+total['output_tokens']

def admission_plan(raw,used_tokens,used_calls):
    """Same V1 admission formula, exposed for no-model dry verification."""
    remaining=BUDGETS['max_model_tokens']-used_tokens
    text,_=legacy.compose_input(legacy.DEFAULT_ORDER,raw['system'],raw.get('shared',''),raw['prompt'],raw.get('suffix',''))
    prompt_tokens=oar_core.count_reference_tokens(text);schema_tokens=oar_core.count_reference_tokens(json.dumps(REVIEW))
    required_reserve=prompt_tokens+schema_tokens+24000
    return dict(admitted=used_calls<BUDGETS['max_model_calls'] and remaining>=required_reserve,
        used_tokens=used_tokens,used_calls=used_calls,remaining=remaining,required_reserve=required_reserve,
        canonical_input_reference_tokens=prompt_tokens,schema_reference_tokens=schema_tokens,overhead_output_reserve=24000,
        provider_reported_tokens=None,provider_tokens_status='unmeasured until actual model call; reference admission is not provider usage')

def complete(folder,label,raw,used_tokens,used_calls):
    dest=folder/label/'initial.result.json'
    if dest.exists():return load(dest)
    plan=admission_plan(raw,used_tokens,used_calls);remaining=plan['remaining']
    # Reserve a full independent CLI turn, including native interface overhead.
    # The hard boundary here is admission; provider-reported overrun is a failure.
    if not plan['admitted']:
        write_new(folder/(label+'.unattempted.json'),dict(reason='model_admission_budget',**plan))
        return dict(status='unattempted',actual_usage={'per_turn':[],'total':{}},raw_response=None,failure_kinds=['model_admission_budget'],attempts_without_reported_usage=0)
    gate=rt.AdmissionGate(ROOT/'results/gate',max_live_calls=4,min_interval=3)
    client=BudgetedClient(remaining,out_dir=folder,review_id=label,schema=REVIEW,model=MODEL,gate=gate,max_calls=1,timeout=240)
    client.begin_stage('initial')
    try:client.complete(**raw)
    except rt.CallFailed:pass
    return load(dest)

def accepted_payload(result):
    if result['status']!='ok':return None
    usage=total_usage(result)
    if usage is None:return None
    try:v=json.loads(result['raw_response'])
    except (TypeError,ValueError):return None
    return v if isinstance(v,dict) else None

def raw_review_payload(result):
    """Parse actual raw warnings independently of transport/usage acceptance.

The original raw text and invalid structures remain in the transport artifacts.
Only an actual list has a well-defined reported finding cardinality.
"""
    try:v=json.loads(result.get('raw_response') or '')
    except (TypeError,ValueError):return None
    return v if isinstance(v,dict) and isinstance(v.get('findings'),list) else None
