"""Single-attempt CLI calls with measured full-input admission and raw receipts.

Provider input/output accounting is authoritative; reference admission and CLI
rollout tracking are not a verified server hard output cap. No response repair,
hidden retries, or intermediate-to-final promotion occurs here.
"""
from pathlib import Path
import hashlib,json,time
import cli_transport as legacy
import reliable_transport as rt
import oar_core
from common import ROOT,MODEL,BUDGETS,WORKERS,MIN_START_INTERVAL,load,write_new

OUTPUT_RESERVE=8000
CLI_OVERHEAD_RESERVE=2048

class BudgetedClient(rt.CliCompletionClient):
 def __init__(self,remaining,**kw):self.remaining=remaining;super().__init__(**kw)
 def _prepare(self,text,temp):
  p=super()._prepare(text,temp);cmd=p['command'];assert cmd[-1]=='-'
  cmd[-1:-1]=['-c','features.rollout_budget.enabled=true','-c',f'features.rollout_budget.limit_tokens={self.remaining}','-c','features.rollout_budget.prefill_token_weight=1.0','-c','features.rollout_budget.sampling_token_weight=1.0','-c',f'features.rollout_budget.reminder_at_remaining_tokens=[{min(8000,self.remaining//2)}]']
  return p

def total_usage(r):
 if r.get('status')=='unattempted'and r.get('physical_attempt_count')==0:return 0
 t=r.get('actual_usage',{}).get('total',{})
 if not r.get('actual_usage',{}).get('per_turn'):return None
 if any(type(t.get(k))is not int for k in('input_tokens','output_tokens')):return None
 return t['input_tokens']+t['output_tokens']

def parse_payload(r,*,require_ok=True):
 if require_ok and(r.get('status')!='ok'or total_usage(r)is None):return None
 try:p=json.loads(r.get('raw_response')or'')
 except(TypeError,ValueError):return None
 return p if isinstance(p,dict)else None

def admission(raw,schema,used_tokens=0,used_calls=0,remaining_wall=900,*,final_reserve=0):
 text,_=legacy.compose_input(legacy.DEFAULT_ORDER,raw['system'],raw.get('shared',''),raw['prompt'],raw.get('suffix',''))
 pretty=json.dumps(schema,ensure_ascii=False,indent=2)+'\n'
 cmd=legacy.command_for(Path('/no_model_admission_schema'),MODEL)
 instruction=json.loads(next(v.split('=',1)[1]for v in cmd if v.startswith('developer_instructions=')))
 count=oar_core.count_reference_tokens;prompt=count(text);schema_n=count(pretty);dev=count(instruction)
 remaining=None if used_tokens is None else BUDGETS['max_model_tokens']-used_tokens
 overhead=schema_n+dev+CLI_OVERHEAD_RESERVE;required=prompt+overhead+OUTPUT_RESERVE
 cli_timeout=max(0,min(240,remaining_wall-WORKERS*MIN_START_INTERVAL))
 return dict(admitted=remaining is not None and used_calls<BUDGETS['max_model_calls']and remaining>=required+final_reserve and cli_timeout>0,
  used_tokens=used_tokens,used_calls=used_calls,remaining=remaining,prompt_reference_tokens=prompt,
  actual_pretty_schema_reference_tokens=schema_n,developer_reference_tokens=dev,
  CLI_overhead_reserve=CLI_OVERHEAD_RESERVE,output_reserve=OUTPUT_RESERVE,required_reserve=required,
  explicit_future_final_reserve=final_reserve,CLI_timeout_seconds=cli_timeout,
  composed_sha256=hashlib.sha256(text.encode()).hexdigest(),actual_pretty_schema_sha256=hashlib.sha256(pretty.encode()).hexdigest(),developer_sha256=hashlib.sha256(instruction.encode()).hexdigest(),
  scope='Full exact composed stdin + actual pretty schema + developer instruction + explicit CLI/output reserves. Not a verified server max-output hard cap.')

def complete(folder,label,raw,schema,used_tokens=0,used_calls=0,remaining_wall=900,*,final_reserve=0):
 folder=Path(folder);dest=folder/label/'initial.result.json'
 if dest.exists():return load(dest)
 a=admission(raw,schema,used_tokens,used_calls,remaining_wall,final_reserve=final_reserve)
 write_new(folder/(label+'.admission.json'),a)
 if not a['admitted']:
  reason='active_wall_budget'if a['CLI_timeout_seconds']<=0 else'model_admission_budget'
  r=dict(status='unattempted',actual_usage={'per_turn':[],'total':{}},raw_response=None,
   failure_kinds=[reason],physical_attempt_count=0,attempts_without_reported_usage=0,
   input_sha256=a['composed_sha256'],measurement_status='known_not_issued',admission=a)
  write_new(folder/(label+'.unattempted.json'),r);write_new(dest,r);return r
 gate=rt.AdmissionGate(ROOT/'results/model_gate',max_live_calls=WORKERS,min_interval=MIN_START_INTERVAL)
 client=BudgetedClient(a['remaining'],out_dir=folder,review_id=label,schema=schema,model=MODEL,gate=gate,max_calls=1,timeout=a['CLI_timeout_seconds'])
 client.begin_stage('initial')
 try:client.complete(**raw)
 except rt.CallFailed:pass
 except Exception as exc:
  # Preserve wrapper failure and any real terminal receipt. Never turn a
  # response-less wrapper exception into known-zero provider cost.
  write_new(folder/(label+'.wrapper_exception.json'),dict(type=type(exc).__name__,message=str(exc)))
  if not dest.exists():
   terminal=sorted((folder/label/'attempts/initial').glob('attempt_*/result.json'))
   if terminal:
    r=load(terminal[-1]);r['wrapper_exception_preserved']=True
   else:r=dict(status='error',raw_response=None,actual_usage={'per_turn':[],'total':{}},physical_attempt_count=1,attempts_without_reported_usage=1,failure_kinds=['wrapper_exception_unknown_issue_state'])
   write_new(dest,r)
 return load(dest)
