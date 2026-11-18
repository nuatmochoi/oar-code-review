from pathlib import Path

ROOT = Path(__file__).resolve().parent
OLD = ROOT.parent / 'reviewbench_changeunits_commit_query25_20261105'
ORIGINAL = ROOT.parent / 'reviewbench_official25_lsp_20261105'
ARMS = ('B0', 'B1', 'B2')
LABELS = {'B0': '공개 PR·diff만', 'B1': 'diff + AST 주변 본문', 'B2': 'diff + AST + LSP 근거'}
MODEL = 'gpt-5.6-sol'
PORT = 8810
TOKEN_CAP = 750_000
SOURCE_CAP = 6_000
PLAN_OUTPUT = 6_144
FINAL_OUTPUT = 16_384
ACTIVE_SECONDS = 3_600
TOTAL_USD = 70.0
ARM_USD = TOTAL_USD / 3
WORKERS = 6
JUDGE_WORKERS_PER_ARM = 2
