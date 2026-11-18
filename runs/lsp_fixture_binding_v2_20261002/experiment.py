"""Named-fixture binding development: fresh C, size-matched M, binding D."""
import ast
import concurrent.futures
import difflib
import hashlib
import importlib.util
import random
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parent
ROOT = R.parents[1]
PRIOR = ROOT / 'runs/lsp_atomic_routes_20261002'
STATE = ROOT / 'runs/lsp_state_holdout_20261002'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'runs/lsp_ablation_fresh_20261001'))
sp = importlib.util.spec_from_file_location('binding_old_ex', PRIOR / 'experiment.py')
old = importlib.util.module_from_spec(sp)
sp.loader.exec_module(old)
native_policy = old.old.policy
sp = importlib.util.spec_from_file_location('binding_policy', R / 'policy.py')
policy = importlib.util.module_from_spec(sp)
sp.loader.exec_module(policy)
from ace_review.context_adaptive import Corpus, EvidenceState, digest, token_count
from ace_review.isolated_review import call_packet
read = old.read
save = old.save
prompt = old.prompt
CAP = 2600


def full_sources(root):
    result = old.old.ex.old.full_sources(root, 'pytest-dev/pytest')
    # Prior staging excluded the public pytest package, hiding fixture's export.
    for p in sorted((root / 'src/pytest').rglob('*.py')):
        if '__pycache__' not in p.parts:
            result[str(p.relative_to(root / 'src'))] = p.read_text()
    return result


def stage(sources, stem):
    root = R / 'workspaces' / stem
    root.mkdir(parents=True, exist_ok=False)
    for file, text in sources.items():
        dest = root / file
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    config = read(STATE / 'workspace_manifests' / f'{stem}.json')['pyrightconfig']
    save(root / 'pyrightconfig.json', config)
    save(R / 'workspace_manifests' / f'{stem}.json', {'source_sha256': {f: digest(t) for f, t in sources.items()}, 'pyrightconfig': config, 'production_public_package_added': True})
    return root


def prepare_one(world):
    stem = f'J04_{world}'
    previous = read(PRIOR / 'packets' / f'{stem}.json')
    direction = read(STATE / 'labels.json')['J04']['directions'][world]
    base, head = ('original', 'fixed') if direction == 'forward' else ('fixed', 'original')
    checkout = STATE / 'checkouts' / previous['instance_id']
    all_sources = {v: full_sources(checkout / rev) for v, rev in [('base', base), ('head', head)]}
    files = set(previous['output_corpus_sha256']['head']) | set(policy.ROUTES) | {'pytest/__init__.py'}
    versions = {v: {f: s[f] for f in sorted(files)} for v, s in all_sources.items()}
    diff = ''.join(''.join(difflib.unified_diff(versions['base'][f].splitlines(True), versions['head'][f].splitlines(True), fromfile='a/' + f, tofile='b/' + f)) for f in previous['source_selection']['changed_files'])
    corpus = Corpus(versions, {v: {'nodes': {}, 'edges': []} for v in versions}, diff)
    resolver = native_policy.ref.old.Bindings(corpus, versions['head'])
    root = stage(all_sources['head'], stem)
    client = native_policy.Client(root)
    backend = native_policy.ref.LSPBackend(resolver, client)
    navigation = []
    selections = {}
    statements = []
    try:
        for file, symbols in policy.ROUTES.items():
            selections[file] = set()
            for symbol in symbols:
                node = policy.declarations(versions['head'][file])[symbol]
                text = versions['head'][file].splitlines()[node.lineno - 1]
                match = re.search(r'\b(?:class|def)\s+(' + re.escape(node.name) + r')\b', text)
                char = len(text[:match.start(1)].encode('utf-16-le')) // 2
                raw = client.definition(file, node.lineno - 1, char)
                targets, excluded = backend.map_result(raw)
                assert (file, symbol) in targets, (file, symbol, targets, excluded)
                lines, ledger = policy.select(versions['head'][file], symbol)
                selections[file] |= lines
                statements.extend(dict(x, file=file, symbol=symbol) for x in ledger)
                navigation.append({'method': 'definition', 'file': file, 'symbol': symbol, 'line': node.lineno, 'utf16_character': char, 'raw_result': raw, 'targets': targets, 'excluded': excluded, 'error': None})
        # Actual reference at the pytest.fixture decorator, not only declaration queries.
        file = '_pytest/unittest.py'
        node = policy.declarations(versions['head'][file])['_make_xunit_fixture']
        nested = next(n for n in node.body if isinstance(n, ast.FunctionDef))
        decorator = next(d for d in nested.decorator_list if isinstance(d, ast.Call))
        row = versions['head'][file].splitlines()[decorator.lineno - 1]
        char = len(row[:row.index('fixture')].encode('utf-16-le')) // 2
        raw = client.definition(file, decorator.lineno - 1, char)
        targets, excluded = backend.map_result(raw)
        assert ('_pytest/fixtures.py', 'fixture') in targets, (targets, excluded)
        navigation.append({'method': 'definition', 'file': file, 'symbol': 'pytest.fixture decorator reference', 'line': decorator.lineno, 'utf16_character': char, 'raw_result': raw, 'targets': targets, 'excluded': excluded, 'error': None})
    finally:
        client.close()
        save(R / 'lsp_traces' / f'{stem}_D.json', {'trace': client.trace, 'initialization': client.initialization, 'stderr': client.errors})
    alias = next(n for n in ast.parse(versions['head']['pytest/__init__.py']).body if isinstance(n, ast.ImportFrom) and n.module == '_pytest.fixtures' and any(a.name == 'fixture' for a in n.names))
    selections['pytest/__init__.py'] = set(range(alias.lineno, alias.end_lineno + 1))
    statements.append({'file': 'pytest/__init__.py', 'symbol': 'public fixture export', 'kind': 'ImportFrom', 'start': alias.lineno, 'end': alias.end_lineno, 'text': ast.get_source_segment(versions['head']['pytest/__init__.py'], alias)})
    source = policy.render(versions['head'], selections)
    assert token_count(source) <= CAP, token_count(source)
    c_state = EvidenceState(corpus)
    c_state.lines = {k: set(v) for k, v in previous['C_audit']['state']['source_lines'].items()}
    c_state.paths = previous['C_audit']['state']['paths']
    assert c_state.render() == previous['C']
    m_state = EvidenceState(corpus)
    m_state.lines = {k: set(v) for k, v in c_state.lines.items()}
    m_state.paths = dict(c_state.paths)
    # Exclude all selected binding declarations and their class-field regions from filler.
    forbidden = {f: set() for f in selections}
    for f, names in policy.ROUTES.items():
        decls = policy.declarations(versions['head'][f])
        for name in names:
            n = decls[name]
            forbidden[f].update(range(min([n.lineno] + [d.lineno for d in n.decorator_list]), n.end_lineno + 1))
    forbidden['pytest/__init__.py'].update(selections['pytest/__init__.py'])
    target = token_count(source)
    added = []
    scores = corpus.scores(read(STATE / 'scopes.json')[previous['instance_id']]['scope'])
    candidates = sorted((k for k, u in corpus.units.items() if u.version == 'head' and k not in c_state.lines and not set(range(u.start, u.end + 1)) & forbidden.get(u.file, set())), key=lambda k: (-scores.get(k, 0), k))
    for key in candidates:
        u = corpus.units[key]
        m_state.lines[key] = set(range(u.start, u.end + 1))
        if m_state.tokens > target:
            m_state.lines.pop(key)
        else:
            added.append(key)
    assert abs(m_state.tokens - target) / target <= .02, (m_state.tokens, target)
    packet = {k: previous[k] for k in ['case_id', 'world', 'instance_id', 'repository', 'common', 'source_selection']}
    packet.update(group='seen_fixture_binding', workspace_reference=str(root.relative_to(ROOT)), source_sha256=corpus.source_hashes)
    packet.update(C=previous['C'], C_observations=previous['C_observations'], C_audit=previous['C_audit'])
    packet.update(M=m_state.render(), M_observations=previous['C_observations'], M_audit={'state': m_state.audit(), 'filler_ids': added, 'forbidden_binding_ranges': {f: sorted(s) for f, s in forbidden.items()}, 'filler_rule': 'Head production units ranked by public scope; exclude every required binding declaration region; preserve C exactly as source lines; accept whole units only up to D actual token count.'})
    packet.update(D=source, D_observations=previous['C_observations'], D_audit={'state': {'tokens': token_count(source), 'source_lines': {f: sorted(s) for f, s in selections.items()}, 'packet_sha256': digest(source)}, 'new_native_navigation': navigation, 'statements': statements, 'automatic_route_selection': False, 'runtime_certified': False})
    save(R / 'packets' / f'{stem}.json', packet)
    print(f'Prepared {stem}: C={token_count(packet["C"])}, M={m_state.tokens}, D={token_count(source)}; native queries={len(navigation)}', flush=True)


def prepare():
    for folder in ['packets', 'inputs', 'raw', 'lsp_traces', 'workspace_manifests', 'workspaces']:
        (R / folder).mkdir(exist_ok=False)
    for world in ['a', 'b']:
        prepare_one(world)
    save(R / 'labels.json', read(PRIOR / 'labels.json'))
    save(R / 'schema.json', read(PRIOR / 'schema.json'))
    jobs = []
    for pp in sorted((R / 'packets').glob('*.json')):
        p = read(pp)
        for repeat in [1, 2, 3]:
            for arm in ['C', 'M', 'D']:
                rid = f'{pp.stem}_{arm}_r{repeat}'
                text = prompt(p, arm)
                inp = {'run_id': rid, 'case_id': 'J04', 'world': p['world'], 'group': p['group'], 'arm': arm, 'repeat': repeat, 'prompt': text, 'prompt_sha256': digest(text), 'prompt_reference_tokens': token_count(text)}
                save(R / 'inputs' / f'{rid}.json', inp)
                jobs.append({k: inp[k] for k in ['run_id', 'case_id', 'world', 'group', 'arm', 'repeat']})
    files = [p for p in R.glob('*') if p.is_file() and p.name != 'candidate_source.txt'] + [p for folder in ['packets', 'inputs', 'lsp_traces', 'workspace_manifests'] for p in (R / folder).glob('*.json')]
    files += [ROOT / p for p in read(PRIOR / 'freeze.json')['files']] + [PRIOR / 'freeze.json', PRIOR / 'result_seal.json']
    files += [ROOT / 'runs/lsp_fixture_binding_20261002/result_seal.json']
    save(R / 'freeze.json', {'model': 'gpt-5.6-sol', 'effort': 'low', 'logical_reviews': 18, 'physical_calls': 18, 'jobs': jobs, 'shared_identical_inputs': {}, 'repeats': 3, 'source_cap': CAP, 'observation_cap': 400, 'workers': 4, 'retry': False, 'provider_seed_control': False, 'model_calls_before_freeze': 0, 'new_native_queries': 24, 'primary_contrast': 'Corrected enclosing-class context after v1 supplied-source defect; all model calls fresh. D versus M: binding route versus approximately size-matched unrelated production source. C unchanged-source secondary comparator. Seen single PR, human-selected AST statements, native declaration and decorator-reference verification. Same static observations, all fresh model calls. Cap raised to2600 before inputs frozen.', 'files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})


def run():
    f = read(R / 'freeze.json')
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in f['files'].items())
    for m in (R / 'workspace_manifests').glob('*.json'):
        assert all(digest((R / 'workspaces' / m.stem / p).read_text()) == h for p, h in read(m)['source_sha256'].items())
    jobs = list(f['jobs'])
    random.Random(20261025).shuffle(jobs)
    def one(j):
        rid = j['run_id']
        assert not (R / 'raw' / f'{rid}.json').exists()
        call_packet(read(R / 'inputs' / f'{rid}.json'), R / 'raw', f['model'], read(R / 'schema.json'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, jobs))


def freeze_prepared():
    """Resume only packaging after a missing prior seal; do not regenerate inputs."""
    assert not (R / 'freeze.json').exists()
    assert not list((R / 'raw').glob('*.json'))
    packets = [read(p) for p in sorted((R / 'packets').glob('*.json'))]
    assert len(packets) == 2
    jobs = []
    for pp in sorted((R / 'packets').glob('*.json')):
        p = read(pp)
        assert all(s in p['D'] for s in ['class FixtureDef(', 'class FixtureManager:', 'class UnitTestCase('])
        for repeat in [1, 2, 3]:
            for arm in ['C', 'M', 'D']:
                inp = read(R / 'inputs' / f'{pp.stem}_{arm}_r{repeat}.json')
                assert inp['prompt'] == prompt(p, arm)
                assert inp['prompt_sha256'] == digest(inp['prompt'])
                jobs.append({k: inp[k] for k in ['run_id', 'case_id', 'world', 'group', 'arm', 'repeat']})
    files = [p for p in R.glob('*') if p.is_file()] + [p for folder in ['packets', 'inputs', 'lsp_traces', 'workspace_manifests'] for p in (R / folder).glob('*.json')]
    files += [ROOT / p for p in read(PRIOR / 'freeze.json')['files']] + [PRIOR / 'freeze.json', PRIOR / 'result_seal.json', ROOT / 'runs/lsp_fixture_binding_20261002/result_seal.json']
    save(R / 'freeze.json', {'model': 'gpt-5.6-sol', 'effort': 'low', 'logical_reviews': 18, 'physical_calls': 18, 'jobs': jobs, 'shared_identical_inputs': {}, 'repeats': 3, 'source_cap': CAP, 'observation_cap': 400, 'workers': 4, 'retry': False, 'provider_seed_control': False, 'model_calls_before_freeze': 0, 'new_native_queries': 24, 'packaging_resumed_without_input_or_navigation_regeneration': True, 'primary_contrast': 'Corrected class ownership context; fresh D versus approximately token-matched M, secondary unchanged C. Already-seen human-selected route; no automatic/native-runtime/generalization claim.', 'files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})


if __name__ == '__main__':
    if sys.argv[1] == 'prepare': prepare()
    elif sys.argv[1] == 'freeze-prepared': freeze_prepared()
    else: run()
