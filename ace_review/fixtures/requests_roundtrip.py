"""Network-free witness. Multiple protocols/payloads remain one defect family."""
import json
import pickle
import sys
import importlib.metadata
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import requests

assert Path(requests.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
records = []
valid = requests.Response()
valid.encoding = 'utf-8'
valid._content = b'{"value": 42}'
assert valid.json() == {'value': 42}
for payload in (b'{', b'not json', b'{"a":1} trailing'):
    response = requests.Response()
    response.encoding = 'utf-8'
    response._content = payload
    try:
        response.json()
    except ValueError as error:
        assert all(hasattr(error, field) for field in ('msg', 'doc', 'pos'))
        for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
            try:
                restored = pickle.loads(pickle.dumps(error, protocol=protocol))
                assert type(restored) is type(error)
                assert (restored.msg, restored.doc, restored.pos) == (error.msg, error.doc, error.pos)
                records.append({'payload': payload.decode(), 'protocol': protocol, 'status': 'pass',
                                'error_type': type(error).__module__ + '.' + type(error).__name__})
            except TypeError as failure:
                if 'doc' not in str(failure) or 'pos' not in str(failure):
                    raise
                records.append({'payload': payload.decode(), 'protocol': protocol, 'status': 'target_failure',
                                'message': str(failure), 'error_type': type(error).__module__ + '.' + type(error).__name__})
    else:
        raise AssertionError('Invalid JSON did not raise ValueError')
failed = any(r['status'] == 'target_failure' for r in records)
print(json.dumps({'status': 'target_failure' if failed else 'pass', 'valid_json_passed': True,
                  'backend': requests.compat.json.__name__, 'checks': records,
                  'python': sys.version,
                  'dependencies': {p: importlib.metadata.version(p) for p in ('urllib3', 'charset-normalizer', 'idna', 'certifi')},
                  'request_version': requests.__version__}))
sys.exit(10 if failed else 0)
