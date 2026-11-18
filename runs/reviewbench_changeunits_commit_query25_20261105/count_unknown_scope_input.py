"""Free accounting-only input count; never generates or changes a review."""
import hashlib,json,sys,time,urllib.request
from pathlib import Path
from experiment import ROOT,load,write,sha
from freeze_run import verify
sys.path.insert(0,str(ROOT.parents[1]/'tools'))
from research_openai_credentials import load_credentials,headers

def main():
 verify()
 rid=sys.argv[1]if len(sys.argv)>1 else'20261105T195549-1c436d662a'
 original=ROOT/'api'/rid/'request.json';body=load(original)
 allowed={'model','input','instructions','conversation','previous_response_id','reasoning','text','tools','tool_choice','parallel_tool_calls','truncation','personality'}
 excluded=set(body)-allowed
 assert excluded <= {'max_output_tokens','store','service_tier','metadata','stream','prompt_cache_key','include'},excluded
 request={k:v for k,v in body.items()if k in allowed}
 dest=ROOT/'audit/input_token_counts'/rid;dest.mkdir(parents=True,exist_ok=False)
 write(dest/'request.json',request)
 started=time.time()
 req=urllib.request.Request('https://api.openai.com/v1/responses/input_tokens',data=json.dumps(request).encode(),headers=headers(load_credentials()),method='POST')
 with urllib.request.urlopen(req,timeout=60)as r:
  result=json.load(r);request_id=r.headers.get('x-request-id')
 write(dest/'response.json',result)
 assert result['object']=='response.input_tokens'and isinstance(result['input_tokens'],int)
 tokens=result['input_tokens'];margin=8192
 bound=((tokens+margin)*(10 if tokens+margin>272000 else 5)+body['max_output_tokens']*30)/1e6
 audit=dict(generation_request_id=rid,original_request_sha256=sha(original),count_request_sha256=sha(dest/'request.json'),exact_input_fields_preserved=True,excluded_non_input_fields=sorted(excluded),provider_input_tokens=tokens,additional_input_token_margin=margin,max_output_tokens=body['max_output_tokens'],unknown_cost_upper_usd=bound,usage_remains_unknown=True,model_generation_calls_added=0,count_endpoint_free=True,count_request_id=request_id,elapsed_seconds=time.time()-started,sources=['https://developers.openai.com/api/docs/guides/token-counting','https://help.openai.com/en/articles/4936856-understanding-and-counting-tokens','https://developers.openai.com/api/docs/models/gpt-5.6-sol','https://developers.openai.com/api/docs/pricing'])
 write(dest/'audit.json',audit);verify();print(json.dumps({k:audit[k]for k in ['provider_input_tokens','additional_input_token_margin','unknown_cost_upper_usd','model_generation_calls_added']}))
if __name__=='__main__':main()
