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
