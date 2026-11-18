"""Private mutable build cache; sealed module/source pools are read-only."""
from pathlib import Path
import json,os,subprocess,time
ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[1]
CACHE=ROOT/'cache'
SEALED=PROJECT/'runs/reviewbench48_ablation_20261103/native_validation/cache'

def sandbox_profile():
    path=CACHE/'native-no-old-writes.sb';path.parent.mkdir(parents=True,exist_ok=True)
    value='(version 1)\n(allow default)\n(deny file-write* (subpath '+json.dumps(str(PROJECT))+'))\n(allow file-write* (subpath '+json.dumps(str(ROOT))+'))\n'
    if path.exists():assert path.read_text()==value
    else:path.write_text(value)
    return path

def sandbox_command(command):return ['/usr/bin/sandbox-exec','-f',str(sandbox_profile()),*map(str,command)]

def isolation_environment():
    assert(ROOT/'audit/cache_environment.json').exists(),'Private cache preparation required'
    paths={'GOCACHE':CACHE/'go-build','GOPATH':CACHE/'gopath','GOPLSCACHE':CACHE/'gopls','XDG_CACHE_HOME':CACHE/'xdg','XDG_CONFIG_HOME':CACHE/'config','GOTMPDIR':CACHE/'tmp/go','TMPDIR':CACHE/'tmp','PYTHONPYCACHEPREFIX':CACHE/'python-bytecode'}
    for p in paths.values():p.mkdir(parents=True,exist_ok=True)
    return {**{k:str(v)for k,v in paths.items()},'GOMODCACHE':str(SEALED/'go-mod'),'GOENV':'off','GOFLAGS':'-mod=readonly','GOTOOLCHAIN':'local','GOPROXY':'off','GOSUMDB':'off','GOTELEMETRY':'off','PYTHONDONTWRITEBYTECODE':'1'}

def prepare():
    from common import write_new
    dest=ROOT/'audit/cache_environment.json';assert not dest.exists()
    CACHE.mkdir(exist_ok=True);target=CACHE/'go-build';assert not target.exists()
    started=time.monotonic();cmd=['/bin/cp','-cRp',str(SEALED/'go-build'),str(target)]
    result=subprocess.run(cmd,capture_output=True,text=True,check=True)
    write_new(dest,dict(complete=True,command=cmd,returncode=result.returncode,seconds=time.monotonic()-started,build_cache='APFS clone performance cache only; no hardlinks or ordinary-copy fallback',read_only_module_cache=str(SEALED/'go-mod'),source_identity='Every source read checks frozen snapshot/selected-package identity; no claim that build cache is semantic evidence.',model_calls=0))
    probe=ROOT/'audit/write_boundary_probe.json'
    program='import os,json,sys;blocked=False\ntry:\n f=os.open(sys.argv[1],os.O_WRONLY);os.close(f)\nexcept PermissionError:blocked=True\nwith open(sys.argv[2],"x")as f:json.dump({"old_write_denied":blocked},f)\nsys.exit(0 if blocked else 9)'
    import sys
    result=subprocess.run(sandbox_command([sys.executable,'-B','-c',program,str(PROJECT/'runs/openai_api_reexplore48_v3_20261104/freeze.json'),str(probe)]),env={**os.environ,**isolation_environment()},capture_output=True,text=True,check=True)
    assert json.loads(probe.read_text())['old_write_denied']
    return json.loads(dest.read_text())

if __name__=='__main__':print(json.dumps(prepare()))
