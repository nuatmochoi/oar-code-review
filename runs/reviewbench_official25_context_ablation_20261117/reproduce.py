"""Prepare the 25-PR context ablation from audited inputs and a pinned corpus."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from config import ARMS, LABELS, MODEL, PORT, ROOT
from freeze_run import verify

PIN = '03c62fe06ca42b39216402ebbcb9aec765109a59'
DATA = ROOT / 'reproduction'
UPSTREAM = ROOT / 'upstream'
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_LFS_SKIP_SMUDGE='1')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def load(path):
    return json.loads(path.read_text())


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if path.exists():
        if path.read_text() != serialized:
            raise ValueError(f'Existing artifact differs: {path.relative_to(ROOT)}')
    else:
        path.write_text(serialized, encoding='utf-8')


def run(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, env=GIT_ENV, capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError(f'{args[0]} {args[1]} failed in {cwd or ROOT}')
    return result.stdout


def fetch_repo(destination, url, refs):
    destination.mkdir(parents=True, exist_ok=True)
    if not (destination / '.git').exists():
        run('git', 'init', '-q', str(destination))
    local = Path(url)
    if local.is_absolute() and local.is_dir():
        objects = run('git', 'rev-parse', '--path-format=absolute', '--git-path', 'objects', cwd=local)
        alternates = destination / '.git/objects/info/alternates'
        alternates.write_bytes(objects)
    present = all(
        subprocess.run(['git', '-C', str(destination), 'cat-file', '-e', ref + '^{commit}'],
                       env=GIT_ENV, capture_output=True, timeout=40).returncode == 0
        for ref in refs
    )
    if not present:
        run('git', 'fetch', '--quiet', '--no-tags', url, *refs, cwd=destination)


def ensure_upstream(source, shared_node_modules):
    fetch_repo(UPSTREAM, str(source.resolve()) if source else 'https://github.com/review-bench/ReviewBench.git', [PIN])
    run('git', 'checkout', '--quiet', '--detach', PIN, cwd=UPSTREAM)
    if run('git', 'rev-parse', 'HEAD', cwd=UPSTREAM).decode().strip() != PIN:
        raise ValueError('Wrong ReviewBench revision')
    modules = UPSTREAM / 'node_modules'
    if shared_node_modules:
        if not shared_node_modules.is_dir():
            raise ValueError('ReviewBench node_modules path is unavailable')
        if not modules.exists():
            modules.symlink_to(shared_node_modules.resolve(), target_is_directory=True)
    else:
        run('npm', 'ci', '--no-audit', '--no-fund', cwd=UPSTREAM)


def rehydrate(item, pr, cache):
    name = pr['nwo']
    if not re.fullmatch(r'[A-Za-z0-9._-]+/[A-Za-z0-9._-]+', name) or {'.', '..'} & set(name.split('/')):
        raise ValueError('Invalid corpus repository name')
    base, head = pr['base'], pr['head']
    if not all(re.fullmatch(r'[0-9a-f]{40}', rev) for rev in (base, head)):
        raise ValueError('Invalid pinned commit')
    cache_repo = ROOT / 'repo_cache' / name.replace('/', '__')
    mirror_url = 'https://github.com/review-bench/' + name.replace('/', '_') + '.git'
    url = mirror_url
    if cache:
        checkout_paths = [
            cache / 'judge-repos' / name.replace('/', '__') / head[:8],
            *cache.glob('work/w*/repos/' + name.replace('/', '_')),
        ]
        location = next((path for path in checkout_paths if (path / '.git').is_dir()), None)
        if location is None:
            raise ValueError(f'Local benchmark checkout missing: {name}')
        url = str(location.resolve())
    fetch_repo(cache_repo, url, [base, head])
    patch = run('git', 'diff', '--full-index', '--no-ext-diff', '--no-textconv', '--no-color',
                f'{base}...{head}', '--', cwd=cache_repo).decode('utf-8')
    widths = iter(item['diff_index_widths'])

    def restore_index(match):
        try:
            left, right = next(widths)
        except StopIteration as err:
            raise ValueError(f'Unexpected diff index line for {name}') from err
        return 'index ' + match[1][:left] + '..' + match[2][:right]

    patch = re.sub(r'^index ([a-f0-9]{40})\.\.([a-f0-9]{40})(?= |\r?$)',
                   restore_index, patch, flags=re.MULTILINE)
    if next(widths, None) is not None:
        raise ValueError(f'Missing diff index lines for {name}')
    if digest(patch.encode()) != item['diff_sha256']:
        patch = patch.replace('\r\n', '\n')
    if digest(patch.encode()) != item['diff_sha256']:
        raise ValueError(f'Rebuilt diff differs for {name}')
    packet = {
        'index': item['index'], 'key': item['key'], 'pr': pr,
        'case': {**item['case'], 'title': pr['title'], 'body': pr['body'], 'diff': patch},
        'units': item['units'], 'contexts': item['contexts'],
    }
    # Keep the original key order, including fields whose order is visible to the model.
    packet['case'] = {key: packet['case'][key] for key in item['case_field_order']}
    if digest(canonical(packet)) != item['packet_sha256']:
        raise ValueError(f'Rebuilt reviewer input differs for {name}')
    for arm in ARMS:
        judge_repo = ROOT / 'arms' / arm / 'judge-repos' / name.replace('/', '__') / head[:8]
        fetch_repo(judge_repo, str(cache_repo), [base, head])
        if not run('git', 'remote', cwd=judge_repo).strip():
            run('git', 'remote', 'add', 'origin', mirror_url, cwd=judge_repo)
        run('git', 'checkout', '--quiet', '--detach', head, cwd=judge_repo)
    return packet


def prepare(upstream_source, cache, shared_node_modules):
    from experiment import final_request, plan_request, tokens
    from jsonschema import Draft202012Validator
    from config import PLAN_OUTPUT, TOKEN_CAP

    if (ROOT / 'freeze.json').exists():
        raise FileExistsError('Use a fresh checkout for a new run')
    ensure_upstream(upstream_source, shared_node_modules)
    corpus = load(UPSTREAM / 'corpus/test/test.json')
    dataset = load(DATA / 'inputs.json')
    if len(corpus) != 25 or len(dataset) != 25:
        raise ValueError('Expected exactly 25 fixed PRs')
    with ThreadPoolExecutor(max_workers=4) as pool:
        packets = list(pool.map(lambda pair: rehydrate(*pair, cache), zip(dataset, corpus)))
    for arm in ARMS:
        folder = ROOT / 'arms' / arm
        link = folder / 'upstream'
        if not link.exists():
            link.symlink_to('../../upstream', target_is_directory=True)
        # Provider reads the disposable loopback token from the process environment.
        judge = {
            'providers': {'openai': {
                'baseUrl': f'http://127.0.0.1:{PORT}/judge/{arm}/v1',
                'apiKey': 'REVIEW_BROKER_TOKEN', 'api': 'openai-responses',
                'models': [{'id': MODEL, 'name': 'Frozen reviewer/judge', 'reasoning': True,
                            'contextWindow': 1000000, 'maxTokens': 16384, 'input': ['text'],
                            'cost': {'input': 4, 'output': 20, 'cacheRead': 0.4, 'cacheWrite': 10}}],
            }},
        }
        write(folder / 'judge-config/models.json', judge)
    manifest = []
    for item, packet in zip(dataset, packets):
        i = packet['index']
        write(ROOT / f'public/{i:02}.json', packet)
        manifest.append({'index': i, 'key': packet['key'], 'nwo': packet['pr']['nwo'],
                         'pr_number': packet['pr']['pr_number']})
        for arm in ARMS:
            plan = plan_request(packet, arm)
            if digest(canonical(plan)) != item['plan_sha256'][arm]:
                raise ValueError(f'{arm}/{i:02}: planning request differs from historical request')
            final, _, _ = final_request(packet, arm, {'unit_assessments': [], 'questions': [], 'reasoning': ''})
            for body in (plan, final):
                Draft202012Validator.check_schema(body['text']['format']['schema'])
            estimate = tokens(plan) + PLAN_OUTPUT + tokens(final) + PLAN_OUTPUT + 8192 + final['max_output_tokens']
            if estimate >= TOKEN_CAP:
                raise ValueError(f'{arm}/{i:02}: input budget exceeded')
            write(ROOT / f'prepared/{arm}/{i:02}.plan.json', plan)
    write(ROOT / 'public/manifest.json', manifest)
    write(ROOT / 'audit/source_provenance.json', load(DATA / 'source_metrics.json'))
    write(ROOT / 'audit/historical_source_hashes.json', {
        f'public/{item["index"]:02}.json': item['packet_sha256'] for item in dataset
    })
    pinned_files = [
        *ROOT.glob('*.py'), *ROOT.glob('vendor/*.py'), *ROOT.glob('public/*.json'),
        *ROOT.glob('prepared/*/*.json'), *ROOT.glob('arms/*/judge-config/models.json'),
        *ROOT.glob('audit/*.json'), *DATA.glob('*.json'),
        UPSTREAM / 'package.json', UPSTREAM / 'package-lock.json',
        UPSTREAM / 'corpus/manifest.json', UPSTREAM / 'corpus/test/test.json',
    ]
    for name in ('golden', 'scripts', 'schema'):
        pinned_files.extend(p for p in (UPSTREAM / name).rglob('*') if p.is_file())
    write(ROOT / 'freeze.json', {
        'format': 'reviewbench25-portable-v1', 'official_commit': PIN, 'PRs': 25,
        'files': {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted(pinned_files)},
    })
    print(json.dumps({'reviewer_packets_verified': len(packets), 'historical_plan_requests_verified': 25 * len(ARMS),
                      'frozen_files': len(verify()['files']), 'paid_calls': 0}))


def check_scores():
    from report import metrics

    historical = load(DATA / 'recorded_counts.json')
    scores = {}
    for arm, row in historical.items():
        result = metrics(row['counts'])
        keys = ('grounded_F1', 'augmented_F1', 'total_FP')
        if any(result[k] != row[k] for k in keys):
            raise ValueError(f'{arm}: recorded metrics mismatch')
        scores[LABELS[arm]] = {k: result[k] for k in keys}
    print(json.dumps(scores, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    setup = sub.add_parser('prepare')
    setup.add_argument('--upstream-source', type=Path, help='Local repository containing the pinned ReviewBench commit')
    setup.add_argument('--mirror-cache', type=Path, help='Existing checked-out ReviewBench mirror repos for offline preparation')
    setup.add_argument('--shared-node-modules', type=Path, help='Existing pinned upstream npm installation for offline preparation')
    sub.add_parser('check-recorded-scores')
    sub.add_parser('report')
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.upstream_source, args.mirror_cache, args.shared_node_modules)
    elif args.command == 'check-recorded-scores':
        check_scores()
    else:
        from report import build

        write(ROOT / 'reproduced_summary.json', build())


if __name__ == '__main__':
    main()
