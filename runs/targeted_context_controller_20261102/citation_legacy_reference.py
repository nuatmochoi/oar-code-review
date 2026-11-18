"""Read-only frozen old prompt reference, isolated from collector processes."""
import importlib.util
from pathlib import Path

_path=Path(__file__).resolve().parent.parent/'cross_language_context_20261102/common.py'
_spec=importlib.util.spec_from_file_location('prior_context_common_reference',_path)
_module=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

def old_prompt(case,arm):return _module.prompt_for(case,arm)
