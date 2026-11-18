import json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
while not(ROOT/'scoring/results.json').exists():
 p=ROOT/'background.recovery.status.json'
 if p.exists()and json.loads(p.read_text()).get('phase')=='failed':raise SystemExit('Transport recovery failed')
 time.sleep(2)
with (ROOT/'audit/finalize-recovered.log').open('x')as f:code=subprocess.call([sys.executable,'-B',str(ROOT/'finish_recovered.py')],cwd=ROOT,stdout=f,stderr=f)
(ROOT/'audit/finalize.exit.json').write_text(json.dumps({'returncode':code},indent=2))
raise SystemExit(code)
