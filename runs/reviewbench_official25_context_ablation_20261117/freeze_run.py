"""Validate the portable run manifest before allowing paid requests."""

import hashlib
import json
from pathlib import Path

from config import ARMS, ROOT

CACHE = {}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify():
    frozen = json.loads((ROOT / 'freeze.json').read_text())
    if frozen.get('format') != 'reviewbench25-portable-v1' or frozen.get('PRs') != 25:
        raise ValueError('Use reproduce.py prepare to generate this run manifest')
    paths = frozen['files']
    required = {
        *(f'public/{i:02}.json' for i in range(25)),
        *(f'prepared/{arm}/{i:02}.plan.json' for i in range(25) for arm in ARMS),
        'upstream/package-lock.json', 'upstream/corpus/manifest.json',
        'audit/source_provenance.json', 'experiment.py', 'budget_proxy.py',
    }
    if not required <= paths.keys():
        raise ValueError('Missing required items in freeze.json')
    for filename, expected in paths.items():
        relative = Path(filename)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError(f'Invalid frozen path: {filename}')
        path = ROOT / relative
        if not path.resolve().is_relative_to(ROOT):
            raise ValueError(f'Frozen path resolves outside the run: {filename}')
        info = path.stat()
        stamp = (info.st_size, info.st_mtime_ns, info.st_ctime_ns, expected)
        if CACHE.get(filename) != stamp and sha(path) != expected:
            raise RuntimeError(f'Frozen file changed: {filename}')
        CACHE[filename] = stamp
    return frozen


if __name__ == '__main__':
    print(json.dumps({'verified_files': len(verify()['files'])}))
