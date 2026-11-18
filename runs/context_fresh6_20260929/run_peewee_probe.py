import json, subprocess, tempfile
from pathlib import Path
R = Path(__file__).resolve().parent
venv = R.parents[1] / 'tools/venv-fresh6-dependencies'
with tempfile.TemporaryDirectory(prefix='fresh6-peewee-') as tmp:
    profile = Path(tmp) / 'sandbox.sb'
    profile.write_text('(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n(deny file-read* (subpath "${USER_HOME}"))\n(allow file-read-metadata)\n(allow file-read* (literal '.replace('${USER_HOME}', str(Path.home())) + json.dumps(str(R / 'peewee_probe.py')) + ') (subpath ' + json.dumps(str(R / 'pairs/F03/snapshots')) + ') (subpath ' + json.dumps(str(venv)) + '))\n')
    r = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile), str(venv / 'bin/python'), '-I', '-B', str(R / 'peewee_probe.py'), str(R)], text=True, capture_output=True, timeout=20)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    data['sandbox'] = 'network/file writes denied, HOME reads restricted to code/snapshots/dedicated venv'
    (R / 'peewee_boundary_validation.json').write_text(json.dumps(data, indent=2))
    print(r.stdout)
