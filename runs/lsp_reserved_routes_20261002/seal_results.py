import hashlib, json, sys
from pathlib import Path
R=Path(__file__).resolve().parent
excluded={'result_seal.json'}
def files():
 return sorted(p for p in R.rglob('*') if p.is_file() and p.name not in excluded and '__pycache__' not in p.parts and p.suffix != '.pyc')
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
if '--verify' in sys.argv:
 seal=json.loads((R/'result_seal.json').read_text())
 assert set(seal['files']) == {str(p.relative_to(R)) for p in files()}
 assert all(digest(R/name)==h for name,h in seal['files'].items())
 print(json.dumps({'result_seal_valid': True, 'sealed_files': len(seal['files'])}))
else:
 assert not (R/'result_seal.json').exists()
 (R/'result_seal.json').write_text(json.dumps({'scope':'Automatic route new PR comparison; immutable inputs/raw/grade/report/snapshots; excludes transient pycache and self.', 'files':{str(p.relative_to(R)):digest(p) for p in files()}},indent=2)+'\n')
 print(json.dumps({'sealed_files':len(files())}))
