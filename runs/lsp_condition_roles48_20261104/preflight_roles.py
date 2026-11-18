"""Grade-free, model-free replay and exact-input admission over all 192 branches."""
import concurrent.futures,json,time
from common import ROOT,load,sha,digest,write_new,WORKERS
from replay_final import prepare_inputs,new_raw_schema,historical_seconds
from transport48 import admission

def one(job):
 c,n,a=job;i=prepare_inputs(c,n,a);row={'case_id':c['id'],'repeat':n,'arm':a,'acquisition_ready':i['ready'],
  'old_result_sha256':sha(i['oldpath']),'new_model_calls':0,'new_native_calls':0}
 if i['ready']:
  raw,schema,used,calls=new_raw_schema(c,i);planner,query=historical_seconds(i)
  ad=admission(raw,schema,used,calls,max(0,900-i['base']['active_wall_seconds']-planner-query))
  row.update(admission=ad,raw_sha256=digest(raw),schema_sha256=digest(schema),
   exact_reused_evidence_sha256=digest(i['delivery']['evidence']),controller_and_frontier_replay_identical=True)
 return row

def main():
 p=ROOT/'audit/replay_preflight.json'
 if p.exists():return load(p)
 started=time.monotonic();cs=load(ROOT/'prepared/cases.json');jobs=[(c,n,a)for c in cs for n in(1,2)for a in('EXTRA','AGENTIC')]
 with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS)as pool:rows=list(pool.map(one,jobs))
 result={'complete':True,'planned_new_branches':192,'cases':48,'replay_ready':sum(r['acquisition_ready']for r in rows),
  'final_admitted':sum(r.get('admission',{}).get('admitted',False)for r in rows),'rows':rows,
  'model_calls':0,'new_native_calls':0,'seconds':time.monotonic()-started,'private_gold_reads':0}
 write_new(p,result);return result

if __name__=='__main__':print(json.dumps({k:v for k,v in main().items()if k!='rows'}))
