"""Fresh per-PR Linux Docker caches; no past-run or host source pool mounted."""
from pathlib import Path
import os
ROOT=Path(os.environ.get('REVIEW_RUN_ROOT','/work/out/agent')).resolve()
CACHE=ROOT/'cache'
def sandbox_command(command):return list(map(str,command))
def isolation_environment():
 paths={k:CACHE/v for k,v in {'GOCACHE':'go-build','GOPATH':'gopath','GOPLSCACHE':'gopls','XDG_CACHE_HOME':'xdg','XDG_CONFIG_HOME':'config','GOTMPDIR':'tmp/go','TMPDIR':'tmp','PYTHONPYCACHEPREFIX':'bytecode'}.items()}
 for p in paths.values():p.mkdir(parents=True,exist_ok=True)
 return {**{k:str(v)for k,v in paths.items()},'GOMODCACHE':str(CACHE/'go-mod'),'GOENV':'off','GOFLAGS':'-mod=readonly','GOTOOLCHAIN':'local','GOPROXY':'off','GOSUMDB':'off','GOTELEMETRY':'off','PYTHONDONTWRITEBYTECODE':'1'}
