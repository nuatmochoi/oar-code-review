import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'runs/lsp_ablation_fresh_20261001'))
sp = importlib.util.spec_from_file_location('auto_route_prior', ROOT / 'runs/lsp_fixture_binding_v2_20261002/experiment.py')
reference = importlib.util.module_from_spec(sp)
sp.loader.exec_module(reference)
native_policy = reference.native_policy
read, save, prompt = reference.read, reference.save, reference.prompt
full_sources = reference.full_sources

# Full production corpus selection extends the same source rule to the cached
# Python benchmark repositories; this is corpus topology, not a symbol route.
def full_sources(root):
    if (root / 'src/_pytest').is_dir():
        return reference.full_sources(root)
    source_root = root / 'src' if (root / 'src').is_dir() else root
    packages = {p.name for p in source_root.iterdir() if p.is_dir() and (p / '__init__.py').is_file() and p.name not in {'tests', 'testing', 'benchmarks', 'examples', 'docs', 'doc'}}
    result = {}
    for p in sorted(source_root.rglob('*.py')):
        rel = p.relative_to(source_root)
        if rel.parts[0] not in packages or any(x in {'tests', 'testing', 'benchmarks', 'examples', '__pycache__'} for x in rel.parts) or p.name.startswith('test_') or p.name == 'conftest.py': continue
        result[rel.as_posix()] = p.read_text()
    assert result, root
    return result
