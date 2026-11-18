"""Post-hoc accounting from authoritative API usage; never changes frozen inputs.
Public GPT-5.6+ prompt-cache default ttl 30m = total 1.25x write rate.
"""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def one(receipt,request):
 u=receipt.get('usage')or{};i=u.get('input_tokens');o=u.get('output_tokens')
 if not isinstance(i,int)or not isinstance(o,int):return dict(id=receipt['id'],phase=receipt['phase'],status='usage_unknown',usd=None,upper=receipt.get('settled_upper',receipt['reserved_usd']))
 details=u.get('input_tokens_details')or{};cached=details.get('cached_tokens',0);write=details.get('cache_write_tokens',0);plain=i-cached-write
 assert plain>=0,(receipt['id'],i,cached,write)
 long=i>272000;rate=8 if long else 4;out=30 if long else 20;ttl=(request.get('prompt_cache_options')or{}).get('ttl','30m');assert ttl in('30m','12h')
 # No explicit 12h token breakpoints are sent by either frozen caller.
 factor=2.5 if ttl=='12h'else 1.25
 usd=(plain*rate+cached*rate*.1+write*rate*factor+o*out)/1e6
 return dict(id=receipt['id'],phase=receipt['phase'],status=receipt['status'],input_tokens=i,output_tokens=o,cached_tokens=cached,cache_write_tokens=write,plain_input_tokens=plain,long_context=long,write_ttl=ttl,usd=usd,upper=receipt.get('settled_upper',receipt['reserved_usd']),original_base_rate_estimate=receipt.get('cost',{}).get('usd_list_rate_estimate'))
def summarize():
 rows=[]
 for p in sorted((ROOT/'api').glob('*/receipt.json')):rows.append(one(json.loads(p.read_text()),json.loads((p.parent/'request.json').read_text())))
 result=dict(rows=rows,by_phase={},known_usd=sum(r['usd']or 0 for r in rows),settled_upper=sum(r['upper']for r in rows),unknown_calls=sum(r['usd']is None for r in rows),pricing_sources=['https://developers.openai.com/api/docs/models/gpt-5.6-sol','https://developers.openai.com/api/docs/guides/prompt-caching'],invoice_verified=False,cache_write_corrected_in_postprocessing=True,original_artifacts_unchanged=True)
 for phase in ('review','judge'):
  selected=[r for r in rows if r['phase']==phase];result['by_phase'][phase]=dict(calls=len(selected),known_usd=sum(r['usd']or 0 for r in selected),input_tokens=sum(r.get('input_tokens',0)for r in selected),output_tokens=sum(r.get('output_tokens',0)for r in selected),cached_tokens=sum(r.get('cached_tokens',0)for r in selected),cache_write_tokens=sum(r.get('cache_write_tokens',0)for r in selected),unknown_calls=sum(r['usd']is None for r in selected))
 return result
if __name__=='__main__':
 result=summarize();print(json.dumps({k:v for k,v in result.items()if k!='rows'},indent=2))
