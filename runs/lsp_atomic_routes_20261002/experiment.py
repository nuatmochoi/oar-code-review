"""Fresh O/C extraction comparison on the already-seen fixture case."""
import concurrent.futures
import difflib
import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path

R = Path(__file__).resolve().parent
ROOT = R.parents[1]
PRIOR = ROOT / 'runs/lsp_output_paths_v2_20261002'
STATE = ROOT / 'runs/lsp_state_holdout_20261002'
sys.path.insert(0, str(ROOT))
# Load the prior runner before the local module named policy to avoid import collision.
sys.path.insert(0, str(ROOT / 'runs/lsp_ablation_fresh_20261001'))
sp = importlib.util.spec_from_file_location('prior_output_ex', PRIOR / 'experiment.py')
old = importlib.util.module_from_spec(sp)
sp.loader.exec_module(old)
sp = importlib.util.spec_from_file_location('complete_route_policy', R / 'policy.py')
policy = importlib.util.module_from_spec(sp)
sp.loader.exec_module(policy)
from ace_review.context_adaptive import Corpus, EvidenceState, digest, token_count
from ace_review.isolated_review import call_packet

read = old.read
save = old.save
prompt = old.prompt


def prepare():
    for folder in ['packets', 'inputs', 'raw']:
        (R / folder).mkdir(exist_ok=False)
    for world in ['a', 'b']:
        previous = read(PRIOR / 'packets' / f'J04_{world}.json')
        row = next(x for x in read(STATE / 'selected.json') if x['instance_id'] == previous['instance_id'])
        direction = read(STATE / 'labels.json')['J04']['directions'][world]
        base, head = ('original', 'fixed') if direction == 'forward' else ('fixed', 'original')
        versions = {}
        for version, revision in [('base', base), ('head', head)]:
            full = old.ex.old.full_sources(STATE / 'checkouts' / row['instance_id'] / revision, row['repo'])
            versions[version] = {f: full[f] for f in previous['output_corpus_sha256'][version]}
        diff = ''.join(''.join(difflib.unified_diff(versions['base'][f].splitlines(True), versions['head'][f].splitlines(True), fromfile='a/' + f, tofile='b/' + f)) for f in previous['source_selection']['changed_files'])
        corpus = Corpus(versions, {v: {'nodes': {}, 'edges': []} for v in versions}, diff)
        assert corpus.source_hashes == previous['output_corpus_sha256']
        initial = previous['O_audit']['state']
        restored = EvidenceState(corpus)
        restored.lines = {k: set(v) for k, v in initial['source_lines'].items()}
        restored.paths = initial['paths']
        assert restored.render() == previous['O']
        routes = [x['unit_id'] for x in previous['O_audit']['additions']]
        assert len(routes) == 3 and not previous['O_audit']['blocked']
        state, admission = policy.complete_bundle(corpus, initial['source_lines'], initial['paths'], routes, previous['O_audit']['protected_ids'])
        assert admission['admitted'], admission
        packet = {k: previous[k] for k in ['case_id', 'world', 'instance_id', 'repository', 'common', 'workspace_reference', 'source_selection', 'output_corpus_sha256']}
        packet.update(group='seen_fixture_extraction', O=previous['O'], O_audit=previous['O_audit'], O_observations=previous['O_observations'], C=state.render(), C_observations=previous['O_observations'], C_audit={'state': state.audit(), 'jumps': previous['O_audit']['jumps'], 'extra_navigation': previous['O_audit']['extra_navigation'], 'bundle_admission': admission, 'protected_ids': previous['O_audit']['protected_ids'], 'navigation_reused': True})
        save(R / 'packets' / f'J04_{world}.json', packet)
        print(f'Prepared J04_{world}: O={initial["tokens"]}, C={state.tokens}, complete bundle={admission["mandatory_tokens"]}', flush=True)
    save(R / 'labels.json', {'J04': read(STATE / 'labels.json')['J04']})
    save(R / 'schema.json', read(PRIOR / 'schema.json'))
    jobs = []
    for pp in sorted((R / 'packets').glob('*.json')):
        packet = read(pp)
        for repeat in [1, 2, 3]:
            for arm in ['O', 'C']:
                rid = f'{pp.stem}_{arm}_r{repeat}'
                text = prompt(packet, arm)
                inp = {'run_id': rid, 'case_id': 'J04', 'world': packet['world'], 'group': packet['group'], 'arm': arm, 'repeat': repeat, 'prompt': text, 'prompt_sha256': digest(text), 'prompt_reference_tokens': token_count(text)}
                save(R / 'inputs' / f'{rid}.json', inp)
                jobs.append({k: inp[k] for k in ['run_id', 'case_id', 'world', 'group', 'arm', 'repeat']})
    files = [p for p in R.glob('*') if p.is_file()] + [p for folder in ['packets', 'inputs'] for p in (R / folder).glob('*.json')]
    files += [ROOT / p for p in read(PRIOR / 'preparation_seal.json')['files']]
    files += [PRIOR / 'preparation_seal.json', PRIOR / 'result_seal.json'] + [PRIOR / 'packets' / f'J04_{w}.json' for w in ['a', 'b']]
    save(R / 'freeze.json', {'model': 'gpt-5.6-sol', 'effort': 'low', 'logical_reviews': 12, 'physical_calls': 12, 'jobs': jobs, 'shared_identical_inputs': {}, 'repeats': 3, 'source_cap': 1600, 'observation_cap': 400, 'workers': 4, 'retry': False, 'provider_seed_control': False, 'model_calls_before_freeze': 0, 'new_native_queries': 0, 'primary_contrast': 'Fresh O versus C: same native routes/observations, complete source units first within same total cap. Already-seen single fixture PR development, not unseen generalization or complete runtime graph certification.', 'files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})
    print('Frozen 12 fresh physical calls', flush=True)


def run():
    freeze = read(R / 'freeze.json')
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in freeze['files'].items())
    for world in ['a', 'b']:
        manifest = read(STATE / 'workspace_manifests' / f'J04_{world}.json')
        assert all(digest((STATE / 'workspaces' / f'J04_{world}' / p).read_text()) == h for p, h in manifest['source_sha256'].items())
    jobs = list(freeze['jobs'])
    random.Random(20261024).shuffle(jobs)
    def one(job):
        rid = job['run_id']
        assert not (R / 'raw' / f'{rid}.json').exists()
        call_packet(read(R / 'inputs' / f'{rid}.json'), R / 'raw', freeze['model'], read(R / 'schema.json'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, jobs))


if __name__ == '__main__':
    prepare() if sys.argv[1] == 'prepare' else run()
