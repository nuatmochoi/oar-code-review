from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent / 'causal_verification_v2_20261029'
WORKSPACE = ROOT.parent.parent
sys.dont_write_bytecode = True
for value in [str(PARENT), str(WORKSPACE)]:
    if value not in sys.path: sys.path.append(value)
