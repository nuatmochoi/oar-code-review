"""Direct project-billed Responses API; credentials never enter artifacts."""
from pathlib import Path
import sys,json,hashlib,time,urllib.request,urllib.error
import jsonschema
from common import ROOT,PROJECT,MODEL,BUDGETS,WORKERS,MIN_START_INTERVAL,load,write_new,sha
sys.path.insert(0,str(PROJECT/'tools'))
from research_openai_credentials import load_credentials,headers
import cli_transport as legacy
import reliable_transport as rt
import oar_core

OUTPUT_RESERVE=16384
API_OVERHEAD_RESERVE=2048
ENDPOINT='https://api.openai.com/v1/responses'

def credentials_ready():
    c=load_credentials()
    return dict(configured=True,source=c['source'],project_explicit=bool(c.get('project')),organization_explicit=bool(c.get('organization')))

def total_usage(r):
    if r.get('status')=='unattempted'and r.get('physical_attempt_count')==0:return 0
    usage=r.get('actual_usage',{});v=usage.get('total',{})
    if not usage.get('per_turn')or any(type(v.get(k))is not int for k in('input_tokens','output_tokens')):return None
    return v['input_tokens']+v['output_tokens']

def parse_payload(r,*,require_ok=True):
    if require_ok and(r.get('status')!='ok'or total_usage(r)is None):return None
    try:v=json.loads(r.get('raw_response')or'')
    except(TypeError,ValueError):return None
    return v if isinstance(v,dict)else None

def request_body(raw,schema,max_output_tokens=OUTPUT_RESERVE):
    text,_=legacy.compose_input(legacy.DEFAULT_ORDER,raw['system'],raw.get('shared',''),raw['prompt'],raw.get('suffix',''))
    from ace_review.isolated_review import OFFLINE_INSTRUCTION
    return dict(model=MODEL,input=[{'role':'developer','content':OFFLINE_INSTRUCTION},{'role':'user','content':text}],reasoning={'effort':'low'},
        text={'format':{'type':'json_schema','name':'research_output','strict':True,'schema':schema}},max_output_tokens=max_output_tokens,tools=[],store=False,service_tier='default')

def admission(raw,schema,used_tokens=0,used_calls=0,remaining_wall=900,*,final_reserve=0):
    wire=json.dumps(request_body(raw,schema),ensure_ascii=False,separators=(',',':'));input_n=oar_core.count_reference_tokens(wire)
    remaining=None if used_tokens is None else BUDGETS['max_model_tokens']-used_tokens
    required=input_n+API_OVERHEAD_RESERVE+OUTPUT_RESERVE;timeout=max(0,min(300,remaining_wall-WORKERS*MIN_START_INTERVAL))
    return dict(admitted=remaining is not None and used_calls<BUDGETS['max_model_calls']and remaining>=required+final_reserve and timeout>0,
        used_tokens=used_tokens,used_calls=used_calls,remaining=remaining,prompt_reference_tokens=input_n,API_overhead_reserve=API_OVERHEAD_RESERVE,
        output_reserve=OUTPUT_RESERVE,required_reserve=required,explicit_future_final_reserve=final_reserve,CLI_timeout_seconds=timeout,
        composed_sha256=hashlib.sha256(wire.encode()).hexdigest(),scope='Exact Responses request reference count + API framing allowance + server max_output_tokens. Actual provider usage is authoritative.')

def normalize_response(response):
    pieces=[];refusal=False
    for item in response.get('output',[]):
        for block in item.get('content',[]):
            if block.get('type')=='output_text':pieces.append(block.get('text',''))
            if block.get('type')=='refusal':refusal=True
    u=response.get('usage')or{};known=all(type(u.get(k))is int for k in('input_tokens','output_tokens'))
    return ''.join(pieces),{'per_turn':[u]if known else[],'total':u if known else{}},response.get('status')=='completed'and not refusal

def complete(folder,label,raw,schema,used_tokens=0,used_calls=0,remaining_wall=900,*,final_reserve=0):
    folder=Path(folder);dest=folder/label/'initial.result.json'
    if dest.exists():return load(dest)
    credentials=load_credentials()
    a=admission(raw,schema,used_tokens,used_calls,remaining_wall,final_reserve=final_reserve);write_new(folder/(label+'.admission.json'),a)
    if not a['admitted']:
        value=dict(status='unattempted',actual_usage={'per_turn':[],'total':{}},raw_response=None,failure_kinds=['active_wall_budget'if a['CLI_timeout_seconds']<=0 else'model_admission_budget'],physical_attempt_count=0,measurement_status='known_not_issued',admission=a)
        write_new(dest,value);return value
    body=request_body(raw,schema);write_new(folder/label/'request.json',body)
    supplement=ROOT/'prepared/exact_reuse.json'
    if supplement.exists():
        key=str(folder.relative_to(ROOT))+'/'+label
        saved=load(supplement)['calls'].get(key)
        if saved:
            assert sha(saved['request'])==saved['request_sha256']and sha(saved['provider_result'])==saved['provider_sha256']
            prior_result=load(saved['provider_result'])
            if prior_result['status']=='ok'and load(saved['request'])==body:
                if label=='final':
                    assert sha(saved['delivery'])==saved['delivery_sha256']
                    old=load(saved['delivery']);new=load(folder/'final.delivered_evidence.json')
                    assert old['evidence']==new['evidence']and old['location_catalog']==new['location_catalog']
                write_new(folder/label/'reused.actual.json',dict(**saved,new_run_physical_attempts=0,reused_logical_provider_attempts=prior_result['physical_attempt_count'],selection_rule='Only the same logical unit/stage counterpart; exact complete request equality, never highest score.'))
                write_new(dest,prior_result);return prior_result
    manifest=ROOT/'prepared/shared_reuse_manifest.json'
    prior=load(manifest)['finals'].get(folder.name)if label=='final'and manifest.exists()else None
    if prior:
        assert sha(prior['request'])==prior['request_sha256']and sha(prior['provider_result'])==prior['provider_sha256']
        # Cache hits require exactly equal input+schema+model+generation settings.
        if load(prior['request'])==body:
            assert sha(prior['delivery'])==prior['delivery_sha256']
            old=load(prior['delivery']);new=load(folder/'final.delivered_evidence.json')
            assert old['evidence']==new['evidence']and old['evidence_ids']==new['evidence_ids']
            value=load(prior['provider_result']);write_new(folder/label/'reused.actual.json',prior);write_new(dest,value);return value
    gate=rt.AdmissionGate(ROOT/'results/model_gate',max_live_calls=WORKERS,min_interval=MIN_START_INTERVAL)
    started=time.monotonic();response=None;error=None;request_id=None;issued=False;response_project=None;response_organization=None;rate_limits={}
    with gate.live_slot():
        gate.start(label=str(folder/label))
        try:
            request=urllib.request.Request(ENDPOINT,data=json.dumps(body,ensure_ascii=False).encode(),headers=headers(credentials),method='POST');issued=True
            with urllib.request.urlopen(request,timeout=a['CLI_timeout_seconds'])as reply:
                request_id=reply.headers.get('x-request-id');response_project=reply.headers.get('openai-project');response_organization=reply.headers.get('openai-organization');rate_limits={k:reply.headers.get(k)for k in('x-ratelimit-limit-tokens','x-ratelimit-limit-requests','x-ratelimit-remaining-tokens','x-ratelimit-remaining-requests','x-ratelimit-reset-tokens')};response=json.load(reply)
            write_new(folder/label/'response.json',response)
        except urllib.error.HTTPError as exc:
            request_id=exc.headers.get('x-request-id')if exc.headers else None
            try:code=(json.load(exc).get('error')or{}).get('code')
            except(ValueError,AttributeError):code=None
            # Do not retain message/header bodies that could echo credentials.
            error=dict(kind='http_error',http_status=exc.code,code=code,request_id=request_id)
        except(urllib.error.URLError,TimeoutError,OSError,ValueError)as exc:
            error=dict(kind='transport_or_decode_error',exception_type=type(exc).__name__,request_id=request_id)
    text,usage,normal=normalize_response(response or{});schema_ok=False
    if normal:
        try:jsonschema.validate(json.loads(text),schema);schema_ok=True
        except(jsonschema.ValidationError,ValueError):pass
    value=dict(status='ok'if normal and schema_ok else'error',raw_response=text or None,actual_usage=usage,physical_attempt_count=int(issued),
        attempts_without_reported_usage=int(issued and not usage['per_turn']),failure_kinds=[]if normal and schema_ok else[error['kind']if error else'incomplete_refused_or_invalid_schema'],
        error=error,provider='openai_responses_api',endpoint=ENDPOINT,model=MODEL,reasoning_effort='low',request_id=request_id,
        actual_model=(response or{}).get('model'),response_project=response_project,response_organization=response_organization,rate_limits=rate_limits,
        response_id=(response or{}).get('id'),response_status=(response or{}).get('status'),incomplete_details=(response or{}).get('incomplete_details'),
        timing={'duration_seconds':time.monotonic()-started},schema_validated=schema_ok,no_automatic_retry=True,credentials_source=credentials['source'])
    write_new(dest,value);return value
