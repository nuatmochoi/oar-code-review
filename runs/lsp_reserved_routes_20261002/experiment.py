"""Frozen automatic native policy versus equal-cap B on two previously unused PRs."""
import concurrent.futures
import difflib
import hashlib
import importlib.util
import json
import random
from pathlib import Path
import shared
from ace_review.context_adaptive import Corpus, digest, token_count
from ace_review.context_adaptive_run import common
from ace_review.isolated_review import call_packet

R = Path(__file__).resolve().parent
ROOT = shared.ROOT
read, save, prompt = shared.read, shared.save, shared.prompt
sp = importlib.util.spec_from_file_location('automatic_route_policy_frozen', R / 'policy.py')
policy = importlib.util.module_from_spec(sp); sp.loader.exec_module(policy)


def stage(sources, stem):
    root = R / 'workspaces' / stem
    root.mkdir(parents=True, exist_ok=False)
    for file, source in sources.items():
        p = root / file; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(source)
    config = {'include': ['**/*.py'], 'pythonVersion': '3.11', 'typeCheckingMode': 'off', 'autoSearchPaths': False, 'useLibraryCodeForTypes': False, 'extraPaths': ['.']}
    save(root / 'pyrightconfig.json', config)
    save(R / 'workspace_manifests' / f'{stem}.json', {'source_sha256': {f: digest(t) for f, t in sources.items()}, 'pyrightconfig': config})
    return root


def prepare_one(job):
    row, cid, world, direction = job
    all_src = {v: shared.full_sources(R / 'checkouts' / row['instance_id'] / v) for v in ['original', 'fixed']}
    base, head = ('original', 'fixed') if direction == 'forward' else ('fixed', 'original')
    versions = {'base': all_src[base], 'head': all_src[head]}
    changed = sorted(f for f in all_src['original'] if all_src['original'][f] != all_src['fixed'].get(f))
    diff = ''.join(''.join(difflib.unified_diff(versions['base'][f].splitlines(True), versions['head'][f].splitlines(True), fromfile='a/' + f, tofile='b/' + f)) for f in changed)
    scope = 'Review only newly introduced violations of this declared public API contract for the fixed reproduction inputs. A fix is not an introduced regression merely because behavior changes. Do not assume unstated runtime bindings or values.\n' + read(R / 'scopes.json')[row['instance_id']]['scope']
    case = {'scope': scope, 'diff': diff}
    corpus = Corpus(versions, {v: {'nodes': {}, 'edges': []} for v in versions}, diff)
    resolver = shared.native_policy.ref.old.Bindings(corpus, versions['head'])
    root = stage(versions['head'], f'{cid}_{world}')
    client = shared.native_policy.Client(root)
    try:
        bs, ba = shared.native_policy.b.packet(corpus, case, resolver, client, 'B')
    finally: client.close()
    save(R / 'lsp_traces' / f'{cid}_{world}_B.json', {'trace': client.trace, 'initialization': client.initialization, 'stderr': client.errors})
    client = shared.native_policy.Client(root)
    try:
        native = shared.native_policy.ref.LSPBackend(resolver, client)
        source, obs, audit = policy.packet(versions['head'], diff, scope, resolver, native, client, shared.native_policy.b.edited_lines)
    finally: client.close()
    save(R / 'lsp_traces' / f'{cid}_{world}_A.json', {'trace': client.trace, 'initialization': client.initialization, 'stderr': client.errors})
    p = {'case_id': cid, 'world': world, 'instance_id': row['instance_id'], 'repository': row['repo'], 'group': 'fresh_reserved_routes', 'common': common({'repository': row['repo'], 'scope': scope, 'diff': diff}), 'workspace_reference': str(root.relative_to(ROOT)), 'source_sha256': corpus.source_hashes, 'B': bs.render(), 'B_audit': ba, 'B_observations': shared.native_policy.observation(ba['jumps'], []), 'A': source, 'A_audit': audit, 'A_observations': obs, 'source_selection': {'rule': 'All production _pytest and public pytest Python sources; tests/witnesses excluded; same indexed corpus and full workspace for B/A', 'changed_files': changed}}
    assert bs.tokens <= 2600 and token_count(source) <= 2600
    save(R / 'packets' / f'{cid}_{world}.json', p)
    print(f'Prepared {cid}_{world}: B={bs.tokens}, A={token_count(source)}, blocked={len(audit["blocked"])}', flush=True)


def prepare_sources():
    seal = read(R / 'algorithm_seal.json')
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in seal['files'].items())
    # Same shared maximum cap/query budget. B retains its prior scheduling and representation.
    shared.native_policy.b.CAP = 2600
    for folder in ['packets', 'inputs', 'raw', 'lsp_traces', 'workspaces', 'workspace_manifests']:
        (R / folder).mkdir(exist_ok=False)
    labels = {}; jobs = []; rng = random.Random(20261028)
    for i, row in enumerate(read(R / 'selected.json'), 1):
        cid = f'V{i:02d}'; directions = ['forward', 'reverse']; rng.shuffle(directions)
        labels[cid] = {'instance_id': row['instance_id'], 'repository': row['repo'], 'directions': dict(zip(['a', 'b'], directions)), 'worlds': {w: 'safe' if d == 'forward' else 'unsafe' for w, d in zip(['a', 'b'], directions)}}
        jobs.extend((row, cid, world, direction) for world, direction in zip(['a', 'b'], directions))
    save(R / 'labels.json', labels)
    save(R / 'schema.json', read(ROOT / 'runs/lsp_fixture_binding_v2_20261002/schema.json'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(prepare_one, jobs))


def prepare():
    if not (R / 'packets').exists(): prepare_sources()
    assert read(R / 'witness_validation.json')['all_contracts_verified']
    jobs = []
    for pp in sorted((R / 'packets').glob('*.json')):
        p = read(pp)
        for repeat in [1, 2, 3]:
            for arm in ['B', 'A']:
                rid = f'{pp.stem}_{arm}_r{repeat}'; text = prompt(p, arm)
                inp = {'run_id': rid, 'case_id': p['case_id'], 'world': p['world'], 'group': p['group'], 'arm': arm, 'repeat': repeat, 'prompt': text, 'prompt_sha256': digest(text), 'prompt_reference_tokens': token_count(text)}
                save(R / 'inputs' / f'{rid}.json', inp)
                jobs.append({k: inp[k] for k in ['run_id', 'case_id', 'world', 'group', 'arm', 'repeat']})
    files = [p for p in R.glob('*') if p.is_file()] + [p for folder in ['packets', 'inputs', 'lsp_traces', 'workspace_manifests'] for p in (R / folder).glob('*.json')]
    prior = ROOT / 'runs/lsp_fixture_binding_v2_20261002/freeze.json'
    files += [prior] + [ROOT / p for p in read(prior)['files']]
    save(R / 'freeze.json', {'model': 'gpt-5.6-sol', 'effort': 'low', 'logical_reviews': 24, 'physical_calls': 24, 'jobs': jobs, 'shared_identical_inputs': {}, 'source_cap': 2600, 'observation_cap': 400, 'native_query_cap_per_arm': 12, 'repeats': 3, 'workers': 4, 'retry': False, 'provider_seed_control': False, 'model_calls_before_freeze': 0, 'algorithm_sealed_before_new_PR_selection': True, 'primary_contrast': 'Equal maximum source/observation/native query budgets B versus reserved follow-up traversal A; two previously unselected PRs of same repository, not repository/file/dataset-blind holdout. Actual source lengths and source framing may differ.', 'files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})
    print('Frozen 24 fresh physical model calls', flush=True)


def run():
    f = read(R / 'freeze.json')
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in f['files'].items())
    for m in (R / 'workspace_manifests').glob('*.json'):
        assert all(digest((R / 'workspaces' / m.stem / p).read_text()) == h for p, h in read(m)['source_sha256'].items())
    jobs = list(f['jobs']); random.Random(20261029).shuffle(jobs)
    def one(job):
        rid = job['run_id']; assert not (R / 'raw' / f'{rid}.json').exists()
        call_packet(read(R / 'inputs' / f'{rid}.json'), R / 'raw', f['model'], read(R / 'schema.json'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(one, jobs))


if __name__ == '__main__':
    import sys
    if sys.argv[1] == 'prepare-sources': prepare_sources()
    elif sys.argv[1] == 'prepare': prepare()
    else: run()
