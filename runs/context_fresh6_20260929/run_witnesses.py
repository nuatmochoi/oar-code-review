"""Sandbox all 24 cells twice; do not run model calls until this gate passes."""
import hashlib, json, subprocess, sys, tempfile
from pathlib import Path
R = Path(__file__).resolve().parent
records = []
with tempfile.TemporaryDirectory(prefix='fresh6-witness-') as tmp:
    for cid in [x['case_id'] for x in json.loads((R / 'manifest.json').read_text())]:
        pair = json.loads((R / 'pairs' / cid / 'pair.json').read_text())
        for world in ('safe', 'unsafe'):
            for rev in ('base', 'head'):
                snap = R / 'pairs' / cid / pair['worlds'][world][rev + '_snapshot']
                profile = Path(tmp) / 'profile.sb'
                profile.write_text('(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n(deny file-read* (subpath "${USER_HOME}"))\n(allow file-read* (literal '.replace('${USER_HOME}', str(Path.home())) + json.dumps(str(R / 'witness.py')) + ') (subpath ' + json.dumps(str(snap)) + '))\n')
                for repeat in range(2):
                    p = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile), str(Path(sys.executable).resolve()), '-I', '-B', str(R / 'witness.py'), cid, str(snap)], text=True, capture_output=True, timeout=15)
                    expected = 10 if world == 'unsafe' and rev == 'head' else 0
                    row = {'case_id': cid, 'world': world, 'revision': rev, 'repeat': repeat + 1, 'returncode': p.returncode, 'expected': expected, 'valid': p.returncode == expected, 'stdout': p.stdout, 'stderr': p.stderr}
                    records.append(row)
                    print(json.dumps({k: v for k, v in row.items() if k not in ('stdout', 'stderr')}), flush=True)
                    if not row['valid']:
                        print(p.stdout, p.stderr, flush=True)
(R / 'witness_results.json').write_text(json.dumps({'valid': all((r['valid'] for r in records)), 'records': records, 'witness_sha256': hashlib.sha256((R / 'witness.py').read_bytes()).hexdigest(), 'sandbox': 'network and file writes denied; HOME reads denied except witness code and current source snapshot', 'scope': 'source-extracted component methods; explicit boundary fixtures, not service integration'}, ensure_ascii=False, indent=2))
