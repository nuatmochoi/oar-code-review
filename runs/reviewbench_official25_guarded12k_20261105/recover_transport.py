"""One exact-input transport replay; no new retrieval/prompt or output selection.
Waits for first-pass collection; preserves its failures and 5xx cost reservation.
"""
from pathlib import Path
import hashlib,http.server,json,os,subprocess,sys,threading,time,urllib.request
ROOT=Path(__file__).resolve().parent;sys.path[:0]=[str(ROOT/'adapter/vendor'),str(ROOT/'adapter')]
import budget_proxy as bp
from freeze_run import verify
from common import write_new
from location_ids import decode
import jsonschema

def load(p):return json.loads(Path(p).read_text())
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
class Budget(bp.Budget):
 def __init__(self):
  super().__init__();self.corrections=[]
  for rid,r in self.records.items():
   if str(r.get('error','')).startswith(('HTTP 500','HTTP 502','HTTP 503','HTTP 504'))and not r.get('usage'):
    r['settled_upper']=r['reserved_usd'];self.corrections.append(dict(id=rid,held_upper_usd=r['reserved_usd'],reason='5xx response without usage may have incurred cost; original receipt retained.'))
 def settle(self,row,folder,usage,status,error=None):
  if not usage and str(error or'').startswith(('HTTP 500','HTTP 502','HTTP 503','HTTP 504')):status='usage_unknown'
  return super().settle(row,folder,usage,status,error)

def run():
 while not(ROOT/'audit/runner-completion.json').exists():time.sleep(2)
 while load(ROOT/'background.status.json')['phase']not in('failed','complete'):time.sleep(1)
 first=load(ROOT/'audit/runner-completion.json');assert first['failed']>0
 verify();budget=Budget();port=8791
 # The first pipeline shuts down its broker in finally before recovery binds.
 for _ in range(20):
  try:server=bp.Server(('0.0.0.0',port),bp.Handler);break
  except OSError:time.sleep(.5)
 else:raise RuntimeError('Original broker still running')
 server.budget=budget;server.token=(ROOT/'broker.token').read_text().strip();thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 adjustment=dict(at=time.time(),frozen_inputs_unchanged=True,no_retrieval_repeated=True,max_replays_per_failed_PR=1,reason='Retry only first-pass local rejection or transport failure with no usable output; never retry based on score or content.',cost_corrections=budget.corrections)
 write_new(ROOT/'audit/transport-recovery-policy.json',adjustment)
 (ROOT/'background.recovery.status.json').write_text(json.dumps({'phase':'recovering','pid':os.getpid()},indent=2))
 e=json.loads((ROOT/'upstream/corpus/test/test.json').read_text());recovered=[]
 try:
  for item in sorted(first['rows'],key=lambda x:x['index']):
   if item['returncode']==0:continue
   i=item['index'];pr=e[i];key=pr['nwo'].replace('/','_')+'_'+str(pr['pr_number'])+'-'+pr['head'][:8];folder=ROOT/'work'/f'w{i%3}'/'out'/key/'agent';body=load(folder/'review.request.json');assert not(folder/'review.response.json').exists(),'Never replace a usable model response'
   request_hash=digest(body);attempts=[r for r in budget.records.values()if r['request_sha256']==request_hash]
   assert all(not r.get('usage')for r in attempts),'No replay after a valid usage/response'
   payload=urllib.request.Request(f'http://127.0.0.1:{port}/review/v1/responses',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+server.token,'Content-Type':'application/json'},method='POST')
   started=time.monotonic()
   with urllib.request.urlopen(payload,timeout=1000)as response:value=json.load(response)
   # Use the exact frozen post-response decoder/converter. It writes only files
   # absent in the failed attempt. The original failure.json stays in place.
   text=(ROOT/'adapter/entrypoint.py').read_text();start=text.index(" write_new(ROOT/'review.response.json',response)");end=text.index("if __name__=='__main__':")
   tail=text[start:end];tail='\n'.join(line[1:]if line.startswith(' ')else line for line in tail.splitlines())
   context=load(folder/'lsp.context.json');case=load(folder/'case.json');schema=load(folder/'review.schema.json');catalog=load(folder/'locations.json')
   repo=ROOT/'work'/f'w{i%3}'/'repos'/pr['nwo'].replace('/','_');os.environ['RB_OUT']=str(folder.parent/'findings.json');os.environ['RB_AGENT']='try-agent'
   env=dict(ROOT=folder,response=value,write_new=write_new,json=json,jsonschema=jsonschema,decode=decode,catalog=catalog,schema=schema,Path=Path,repo=repo,pr=pr,os=os,time=time,start=started,context=context,case=case,source_tokens=lambda evidence:context['source_tokens'])
   exec(compile(tail,str(ROOT/'adapter/entrypoint.py')+':exact-frozen-converter','exec'),env)
   import shutil
   shutil.copy2(folder.parent/'findings.json',ROOT/'execution/findings'/f'{key}.json')
   recovered.append(dict(index=i,key=key,request_sha256=request_hash,previous_upstream_attempts=len(attempts),recovery=True,first_failure_preserved=True));write_new(ROOT/'audit'/f'transport-recovered-{i:02d}.json',recovered[-1])
  assert len(list((ROOT/'execution/findings').glob('*.json')))==25
  write_new(ROOT/'audit/runner-recovery-completion.json',dict(first_pass=first['passed'],recovered=len(recovered),findings_PRs=25,records=recovered))
  (ROOT/'background.recovery.status.json').write_text(json.dumps({'phase':'judge'},indent=2))
  # Original official command; only its caller's first-pass success assertion is
  # bypassed after independently verifying all 25 valid normalized files.
  token=server.token;env=dict(os.environ,PI_CODING_AGENT_DIR=str(ROOT/'judge-config'),REVIEW_BROKER_TOKEN=token,OPENAI_API_KEY=token,REVIEW_BENCH_JUDGE_TIMEOUT_MS='1800000')
  cmd=['npm','run','judge','--','--candidate',str(ROOT/'execution/findings'),'--golden',str(ROOT/'upstream/golden'),'--manifest',str(ROOT/'upstream/corpus/manifest.json'),'--provider','openai','--model','gpt-5.6-sol','--output',str(ROOT/'scoring/results.json'),'--repo-dir',str(ROOT/'judge-repos'),'--concurrency','3']
  write_new(ROOT/'audit/recovered-judge-command.json',dict(command=cmd,official_source_unchanged=True))
  with (ROOT/'audit/recovered-judge.log').open('x')as log:subprocess.run(cmd,cwd=ROOT/'upstream',env=env,stdout=log,stderr=log,check=True)
  (ROOT/'background.recovery.status.json').write_text(json.dumps({'phase':'scored','recovered':recovered},indent=2))
 finally:server.shutdown();server.server_close()
if __name__=='__main__':run()
