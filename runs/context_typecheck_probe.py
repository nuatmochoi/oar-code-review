"""Separate runtime diagnostic for the incorrect isinstance-exception explanation."""
import json
import sys
import tempfile
import typing
from pathlib import Path

from ace_review.counterfactual import write_new

results = []
for mode in ('wb', 'w'):
    with tempfile.NamedTemporaryFile(mode=mode) as stream:
        record = {'mode': mode, 'wrapper_type': type(stream).__name__,
                  'has_binary_buffer': getattr(stream, 'buffer', None) is not None}
        try:
            record['isinstance_BinaryIO'] = isinstance(stream, typing.BinaryIO)
            record['typecheck_error'] = None
        except Exception as error:
            record['typecheck_error'] = f'{type(error).__name__}: {error}'
        try:
            stream.write('hello')
            record['text_write_error'] = None
        except Exception as error:
            record['text_write_error'] = f'{type(error).__name__}: {error}'
        results.append(record)
report = {'python': sys.version, 'typing_BinaryIO_is_class': isinstance(typing.BinaryIO, type),
          'checks': results, 'used_for_model_input': False,
          'scope': 'Post-output diagnostic of a recurring wrong explanation; primary target definition remains the predeclared binary-tempfile str-write failure.'}
write_new(Path(__file__).parent / 'context_necessity1/typecheck_probe.json', report)
print(json.dumps(report, indent=2))
