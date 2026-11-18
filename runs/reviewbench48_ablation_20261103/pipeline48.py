"""Sequential frozen phases; no hidden retry of a claimed model call."""
from pathlib import Path
import json,subprocess,sys,time
from common import ROOT,load,write_new
from freeze48 import verify_freeze

def phase(name,args,done):
 if done():return
 log=ROOT/'logs'/f'{name}.log';log.parent.mkdir(parents=True,exist_ok=True)
 event={'phase':name,'started_at':time.time(),'command':[sys.executable,'-B',*args]}
 with log.open('a')as out:
  r=subprocess.run([sys.executable,'-B',*args],cwd=ROOT,stdout=out,stderr=subprocess.STDOUT)
 event.update(ended_at=time.time(),returncode=r.returncode)
 with(ROOT/'logs/pipeline_events.jsonl').open('a')as out:out.write(json.dumps(event)+'\n')
 print(json.dumps(event),flush=True)
 assert r.returncode==0,(name,'failed; preserved raw log',str(log))
 assert done(),(name,'returned without its terminal artifact')

def main():
 verify_freeze()
 phase('gold_run',['evaluate48.py','gold-run'],lambda:(ROOT/'evaluation/gold_completion.json').exists())
 phase('gold_seal',['evaluate48.py','gold-seal'],lambda:(ROOT/'private/gold_validation_seal.json').exists())
 phase('reviewers',['experiment48.py','run'],lambda:len(list((ROOT/'results/reviews').glob('*/review.result.json')))==576)
 phase('judges',['evaluate48.py','review-run'],lambda:len(list((ROOT/'grades/final').glob('*.json')))+len(list((ROOT/'grades/final_failures').glob('*.json')))==576)
 phase('aggregate',['post/aggregate48.py'],lambda:(ROOT/'post/aggregate.json').exists())
 phase('usage',['post/usage48.py'],lambda:(ROOT/'post/usage.json').exists())
 phase('mechanism',['post/mechanism48.py'],lambda:(ROOT/'post/mechanism.json').exists())
 phase('report',['post/build_report48.py','--native-costs','post/usage.json'],lambda:(ROOT/'RESULTS_KO.md').exists())
 write_new(ROOT/'post/pipeline_finished.json',{'complete':True,'reviews':576,'recorded_at':time.time(),'requires_final_audit_and_preservation_seal':True})

if __name__=='__main__':main()
