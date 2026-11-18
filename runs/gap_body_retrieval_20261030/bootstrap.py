from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent
WORKSPACE=ROOT.parent.parent
PARENT=ROOT.parent/'causal_verification_v2_20261029'
BUDGET=ROOT.parent/'handoff_budget_diagnostic_20261030'
REPAIR=ROOT.parent/'critic_handoff_repair_20261030'
sys.dont_write_bytecode=True
for p in (PARENT,WORKSPACE):
    if str(p) not in sys.path:sys.path.append(str(p))
