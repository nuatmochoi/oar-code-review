"""Execute a fixed naming producer and replay the paired ASCII consumer key."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .runtime_probe import run_probe as isolation_probe
from .runtime_probe import sandbox_profile, verified_snapshot

PRODUCER = 'src/internal/fourslash/_scripts/makeManual.mts'


def consumer_key(filename):
    if not filename:
        raise ValueError('A nonempty filename is required')
    name = filename.replace('.ts', '').replace('.', '')
    return name[0].upper() + name[1:]


def run_probe(python, snapshot, *, timeout=15, node='/opt/homebrew/bin/node'):
    if not 0 < timeout <= 60:
        raise ValueError('Invalid timeout')
    root, manifest_hash = verified_snapshot(snapshot)
    isolation = isolation_probe('isolation_selftest', python, timeout=timeout)
    if isolation['status'] != 'ok' or not all(isolation['observation'].values()):
        return {'status': 'isolation_failed', 'isolation': isolation}
    source = root / PRODUCER
    record = {'snapshot': str(root), 'source_manifest_sha256': manifest_hash,
              'producer_script_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'isolation': isolation, 'timeout_seconds': timeout,
              'policy': 'deny network, writes outside scratch, forks; reads allowed'}
    with tempfile.TemporaryDirectory(prefix='adaptive-string-probe-') as directory:
        scratch = Path(directory).resolve()
        scripts = scratch / 'internal/fourslash/_scripts'
        generated = scratch / 'internal/fourslash/tests/gen'
        scripts.mkdir(parents=True)
        generated.mkdir(parents=True)
        target = scripts / 'makeManual.mts'
        shutil.copyfile(source, target)
        (generated / 'alpha_test.go').write_text('package fourslash_test\n')
        command = ['/usr/bin/sandbox-exec', '-p', sandbox_profile(scratch), node,
                   str(target), 'alpha_test.go']
        started = time.monotonic()
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                cwd=scratch, env={'PATH': '/usr/bin:/bin', 'TMPDIR': str(scratch),
                                  'LC_ALL': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'})
        except subprocess.TimeoutExpired:
            return record | {'status': 'timeout'}
        except OSError as exc:
            return record | {'status': 'execution_error', 'error': str(exc)}
        record.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
                      duration_seconds=time.monotonic() - started)
        if result.returncode:
            return record | {'status': 'execution_error'}
        output = scripts / 'manualTests.txt'
        if not output.exists():
            return record | {'status': 'invalid_output'}
        stored = [line.strip() for line in output.read_text().splitlines() if line.strip()]
        lookup = consumer_key('alpha.ts')
        observation = {'runtime': subprocess.run([node, '--version'], capture_output=True,
            text=True, check=True).stdout.strip(), 'neutral_generated_filename': 'alpha_test.go',
            'neutral_source_filename': 'alpha.ts', 'producer_stored_keys': stored,
            'consumer_lookup_key': lookup, 'membership': lookup in set(stored),
            'producer_script_executed': True, 'consumer_expression_replayed': True,
            'scope': 'Actual unchanged companion producer executed on neutral ASCII filename in OS sandbox; '
                     'consumer ASCII expression replayed, not full converter or Go test generation.'}
        return record | {'status': 'ok', 'observation': observation}
