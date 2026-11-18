from pathlib import Path
import json,os,subprocess
from freeze_run import verify
ROOT=Path(__file__).resolve().parent
if __name__=='__main__':
 verify();run=json.loads((ROOT/'audit/runner-completion.json').read_text());assert run['passed']==25 and run['failed']==0
 files=list((ROOT/'execution/findings').glob('*.json'));assert len(files)==25
 env=dict(os.environ,PI_CODING_AGENT_DIR=str(ROOT/'judge-config'),REVIEW_BROKER_TOKEN=(ROOT/'broker.token').read_text().strip(),OPENAI_API_KEY=(ROOT/'broker.token').read_text().strip(),REVIEW_BENCH_JUDGE_TIMEOUT_MS='1800000')
 # No real account token is inherited by official judge.
 for k in ['OPENAI_PROJECT_ID','OPENAI_ORG_ID','ANTHROPIC_API_KEY']:env.pop(k,None)
 cmd=['npm','run','judge','--','--candidate',str(ROOT/'execution/findings'),'--golden',str(ROOT/'upstream/golden'),'--manifest',str(ROOT/'upstream/corpus/manifest.json'),'--provider','openai','--model','gpt-5.6-sol','--output',str(ROOT/'scoring/results.json'),'--repo-dir',str(ROOT/'judge-repos'),'--concurrency','3']
 with (ROOT/'audit/judge-command.json').open('x')as f:json.dump(dict(command=cmd,unmodified_official_CLI=True,judge_model_differs_from_leaderboard=True),f,indent=2)
 with (ROOT/'audit/judge.log').open('x')as f:code=subprocess.call(cmd,cwd=ROOT/'upstream',env=env,stdout=f,stderr=f)
 with (ROOT/'audit/judge-exit.json').open('x')as f:json.dump(dict(returncode=code),f)
 raise SystemExit(code)
