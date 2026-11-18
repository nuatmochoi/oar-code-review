"""Credential-variable correction only; frozen official judge and inputs unchanged."""
import hashlib,json,os,subprocess,time
from pathlib import Path
from freeze_run import verify
ROOT=Path(__file__).resolve().parent
verify();token=(ROOT/'broker.token').read_text().strip()
env=dict(os.environ,PI_CODING_AGENT_DIR=str(ROOT/'judge-config'),REVIEW_BROKER_TOKEN=token,OPENAI_API_KEY=token,REVIEW_BENCH_JUDGE_TIMEOUT_MS='1800000')
for k in ['OPENAI_PROJECT_ID','OPENAI_ORG_ID','ANTHROPIC_API_KEY']:env.pop(k,None)
command=json.loads((ROOT/'audit/judge-command.json').read_text())['command']
record=dict(before_paid_judge_calls=True,changed_at=time.time(),change='OPENAI_API_KEY is set to the budget broker token, the same token already supplied as REVIEW_BROKER_TOKEN. No account API key enters the judge process.',reason='Provider environment credential takes precedence over custom registry apiKey; initial judge requests were rejected locally with 403.',frozen_inputs_prompts_schema_budget_scoring_unchanged=True,official_command=command,launcher_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),initial_failures_preserved=True)
with (ROOT/'audit/judge-auth-transport-amendment.json').open('x')as f:json.dump(record,f,indent=2)
with (ROOT/'audit/judge.auth-recovered.log').open('x')as f:code=subprocess.call(command,cwd=ROOT/'upstream',env=env,stdout=f,stderr=f)
with (ROOT/'audit/judge.auth-recovered.exit.json').open('x')as f:json.dump({'returncode':code},f)
raise SystemExit(code)
