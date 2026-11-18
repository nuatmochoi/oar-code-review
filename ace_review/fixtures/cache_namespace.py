"""Network-free library integration witness for key creation/invalidation agreement."""
import importlib.metadata
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import requests_cache
from requests import Request, Response, Session
from client import make_cache

assert Path(requests_cache.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
records = []
for url in ('https://example.invalid/a', 'https://example.invalid/a?x=1', 'https://example.invalid/b?q=hello'):
    cache = make_cache()
    prepared = Session().prepare_request(Request('GET', url))
    response = Response()
    response.request = prepared
    response.url = prepared.url
    response.status_code = 200
    response._content = b'old value'
    cache.save_response(response)
    key = cache.create_key(prepared)
    assert cache.get_response(key).content == b'old value'
    cache.delete_url(url)
    stale = cache.get_response(key) is not None
    records.append({'url': url, 'stored_key': key, 'still_readable_after_delete': stale,
                    'status': 'target_failure' if stale else 'pass'})
failed = any(r['status'] == 'target_failure' for r in records)
print(json.dumps({'status': 'target_failure' if failed else 'pass', 'checks': records,
                  'python': sys.version, 'library_file': requests_cache.__file__,
                  'dependencies': {p: importlib.metadata.version(p) for p in
                      ('requests', 'cattrs', 'attrs', 'url-normalize', 'urllib3')}}))
sys.exit(10 if failed else 0)
