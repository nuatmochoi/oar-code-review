"""Exercise real Click pager code with an upstream-style forced tempfile path."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

snapshot = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(snapshot / 'src'))
import click
import click._termui_impl as impl

assert Path(click.__file__).resolve().is_relative_to(snapshot)
checks, files = [], []
original_tempfile = tempfile.NamedTemporaryFile


def create_tempfile(*args, **kwargs):
    result = original_tempfile(*args, **kwargs)
    files.append(result)
    return result


for text in ('hello', 'second line', '한글'):
    displayed = []
    def display(argv):
        displayed.append(Path(argv[1]).read_bytes())
        return 0
    try:
        with patch.object(impl, 'WIN', True), patch.object(impl, 'isatty', return_value=True), \
                patch.dict(os.environ, {'PAGER': '/bin/cat', 'TERM': 'xterm'}), \
                patch('subprocess.call', side_effect=display), \
                patch('tempfile.NamedTemporaryFile', side_effect=create_tempfile):
            click.echo_via_pager(text, color=False)
        status = 'pass' if displayed == [(text + '\n').encode()] else 'unexpected_output'
        checks.append({'text': text, 'status': status, 'displayed': [b.decode() for b in displayed]})
    except TypeError as error:
        status = 'target_failure' if 'bytes-like object' in str(error) and 'str' in str(error) else 'unexpected_error'
        checks.append({'text': text, 'status': status, 'error': str(error)})
    finally:
        for file in files:
            file.close()
statuses = {c['status'] for c in checks}
status = statuses.pop() if len(statuses) == 1 else 'environment_error'
print(json.dumps({'status': status, 'checks': checks, 'python': sys.version,
                  'library_file': click.__file__, 'native_windows': False,
                  'display_subprocess_stubbed': True}, ensure_ascii=False))
sys.exit(0 if status == 'pass' else 10 if status == 'target_failure' else 20)
