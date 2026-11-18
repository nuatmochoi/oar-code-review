from pathlib import Path
import base64,json,os,subprocess,sys,zlib
ROOT=Path(__file__).resolve().parent;OLD=ROOT.parent/'reviewbench_official25_lsp_20261105';index=int(sys.argv[1])if len(sys.argv)>1 else 0;e=json.loads((ROOT/'upstream/corpus/test/test.json').read_text())[index]
key=e['nwo'].replace('/','_')+'_'+str(e['pr_number'])+'-'+e['head'][:8];out=ROOT/'audit'/f'prepare-smoke-{index}';out.mkdir(exist_ok=True)
env=dict(os.environ,REVIEW_PREFIX_ZLIB=base64.b64encode(zlib.compress((ROOT/'prefixes'/f'{index:02d}.json').read_bytes())).decode())
cmd=['docker','run','--rm','--platform','linux/amd64','--entrypoint','python','-v',str(ROOT/'work'/f'w{index%3}'/'repos'/e['nwo'].replace('/','_'))+':/work/repo','-v',str(OLD/'work'/f'w{index%3}'/'pr'/key)+':/work/pr:ro','-v',str(out)+':/work/out','-e','GIT_CONFIG_COUNT=1','-e','GIT_CONFIG_KEY_0=safe.directory','-e','GIT_CONFIG_VALUE_0=*','-e','RB_NWO='+e['nwo'],'-e','RB_REPO=/work/repo','-e','RB_PR_JSON=/work/pr/pr.json','-e','RB_DIFF=/work/pr/diff.patch','-e','RB_OUT=/work/out/findings.json','-e','REVIEW_PREPARE_ONLY=1','-e','REVIEW_PREFIX_ZLIB','research-reviewbench-guarded:20261105','-B','/opt/agent/entrypoint.py']
with (ROOT/'audit'/f'prepare-smoke-{index}.log').open('x')as f:code=subprocess.call(cmd,env=env,stdout=f,stderr=f)
print('prepare exit',code)
if code:print((ROOT/'audit'/f'prepare-smoke-{index}.log').read_text()[-2000:])
raise SystemExit(code)
