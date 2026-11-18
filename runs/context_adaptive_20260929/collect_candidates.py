"""Read-only source screening; these files are NOT executable benchmark pairs."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
screen = json.loads((ROOT / 'new_data_screen.json').read_text())
jobs = []
for case in screen['candidates']:
    for version in ('base', 'head'):
        for file in case['source_files_to_inspect']:
            if Path(file).is_absolute() or '..' in Path(file).parts:
                raise ValueError(file)
            jobs.append((case, version, file))


def collect(job):
    case, version, file = job
    commit = case[version]
    url = f'https://raw.githubusercontent.com/{case["repository"]}/{commit}/{file}'
    path = ROOT / 'candidate_sources' / case['repository'].replace('/', '__') / case['pr_url'].rsplit('/', 1)[1] / version / file
    row = {'repository': case['repository'], 'pr_url': case['pr_url'], 'version': version,
           'commit': commit, 'file': file, 'url': url}
    try:
        response = requests.get(url, timeout=30)
        row['http_status'] = response.status_code
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != response.content:
            raise ValueError('existing source differs')
        if not path.exists():
            path.write_bytes(response.content)
        row.update(sha256=hashlib.sha256(response.content).hexdigest(), bytes=len(response.content),
                   local_path=str(path.relative_to(ROOT)), status='downloaded')
    except (requests.RequestException, OSError, ValueError) as error:
        row.update(status='unavailable', error=str(error))
    return row


with ThreadPoolExecutor(max_workers=4) as pool:
    receipts = list(pool.map(collect, jobs))
report = {'status': 'source_screen_only_not_gold', 'source_files': receipts,
          'downloaded': sum(r['status'] == 'downloaded' for r in receipts),
          'unavailable': sum(r['status'] != 'downloaded' for r in receipts),
          'complete_repository_snapshots': False, 'paired_witness_ready': False}
(ROOT / 'candidate_source_receipts.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({k: report[k] for k in ('downloaded', 'unavailable', 'paired_witness_ready')}))
