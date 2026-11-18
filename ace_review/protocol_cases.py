"""Build a second, explicitly synthetic-client/real-library counterfactual family."""
import argparse
import ast
import json
import subprocess
from pathlib import Path

from .counterfactual import block, check_witness, make_diff, make_request, sha, validate_gate, write_new
from .counterfactual_followup import RUNTIME
from .obligation import render_blocks, token_count
from .protocol_hypothesis import mask_source

CACHE_BASE = '247293c810c021c1318de644adeb3666d41ffa4c'
CACHE_HEAD = '11d8d6786ef60692e65e4f891fc9eb15e43037e9'
BASE_FILE = 'requests_cache/backends/base.py'
KEY_FILE = 'requests_cache/cache_keys.py'
CLIENT = '''from requests_cache.backends import BaseCache
from requests_cache.cache_keys import create_key


def namespaced_key(*args, **kwargs):
    return 'tenant-a:' + create_key(*args, **kwargs)


def make_cache():
    return BaseCache(key_fn=create_key)
'''


def method_node(source, cls, method):
    owner = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == cls)
    return next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == method)


def replace_method(source, donor, cls, method):
    old, new = method_node(source, cls, method), method_node(donor, cls, method)
    lines, other = source.splitlines(keepends=True), donor.splitlines(keepends=True)
    return ''.join(lines[:old.lineno - 1] + other[new.lineno - 1:new.end_lineno] + lines[old.end_lineno:])


def prepare_cache(repo, output, python):
    root = Path(output).resolve(); root.mkdir(parents=True, exist_ok=False)
    def git(*args):
        return subprocess.check_output(['git', '--git-dir=' + str(Path(repo).resolve()), *args]).decode()
    files = git('ls-tree', '-r', '--name-only', CACHE_HEAD).splitlines()
    if any(Path(f).name == 'AGENTS.md' for f in files):
        raise ValueError('Repository instructions need inspection first')
    sources = {f: git('show', f'{CACHE_HEAD}:{f}') for f in files if f.startswith('requests_cache/') and f.endswith('.py')}
    old_base, old_keys = git('show', f'{CACHE_BASE}:{BASE_FILE}'), git('show', f'{CACHE_BASE}:{KEY_FILE}')
    node = next(n for n in ast.parse(old_keys).body if isinstance(n, ast.FunctionDef) and n.name == 'url_to_key')
    helper = '\n'.join(old_keys.splitlines()[node.lineno - 1:node.end_lineno]) + '\n'
    # Backport the unchanged legacy helper/import into BOTH contexts, eliminating
    # missing-name failures. Only delete_url differs across their libraries.
    sources[KEY_FILE] += '\n\n' + helper
    sources[BASE_FILE] = sources[BASE_FILE].replace('from ..cache_keys import create_key, remove_ignored_params',
                                                  'from ..cache_keys import create_key, remove_ignored_params, url_to_key')
    results, hashes, heads, diffs = {}, {}, {}, {}
    witness = Path(__file__).parent / 'fixtures/cache_namespace.py'
    for variant in ('safe', 'unsafe'):
        package = dict(sources)
        if variant == 'unsafe':
            package[BASE_FILE] = replace_method(package[BASE_FILE], old_base, 'BaseCache', 'delete_url')
        heads[variant] = package
        for version in ('base', 'head'):
            client = CLIENT if version == 'base' else CLIENT.replace('key_fn=create_key', 'key_fn=namespaced_key')
            contents = package | {'client.py': client}
            cell = variant + '_' + version; snapshot = root / 'snapshots' / cell
            snapshot.mkdir(parents=True)
            for file, text in contents.items():
                write_new(snapshot / file, text, raw=True)
            hashes[cell] = {file: sha(text) for file, text in contents.items()}
            results[cell] = check_witness(snapshot, witness, python)
        diffs[variant] = make_diff(CLIENT, CLIENT.replace('key_fn=create_key', 'key_fn=namespaced_key'), 'client.py')
    gate = validate_gate(diffs['safe'], diffs['unsafe'], results)
    for variant in ('safe', 'unsafe'):
        changed = [f for f in hashes[variant + '_base'] if hashes[variant + '_base'][f] != hashes[variant + '_head'][f]]
        gate['checks'][variant + '_only_client_changed'] = changed == ['client.py']
    gate['eligible'] = all(gate['checks'].values())
    write_new(root / 'execution.json', {'gate': gate, 'cells': results, 'file_sha256': hashes,
              'library_base': CACHE_BASE, 'library_head': CACHE_HEAD, 'anchor_is_synthetic_client': True,
              'source_pr': 'https://github.com/requests-cache/requests-cache/pull/381'})
    write_new(root / 'anchor.diff', diffs['safe'], raw=True)
    write_new(root / 'witness.py', witness.read_text(), raw=True)
    licenses = [f for f in files if f.upper().startswith('LICENSE') and '/' not in f]
    for name in licenses:
        write_new(root / name, git('show', CACHE_HEAD + ':' + name), raw=True)
    return gate


def build_cases(requests_root, cache_root, output):
    root = Path(output); root.mkdir(parents=True, exist_ok=False)
    rows, labels, oracles, audits = [], {}, {}, []
    requests_root, cache_root = Path(requests_root), Path(cache_root)
    for family, run in [('exception_pickle', requests_root), ('cache_namespace', cache_root)]:
        execution = json.loads((run / 'execution.json').read_text())
        if not execution['gate']['eligible']:
            raise ValueError('Invalid four-cell witness: ' + family)
        for variant in ('safe', 'unsafe'):
            snapshot = run / 'snapshots' / (variant + '_head')
            task_id = sha(f'{family}:{variant}')[:16]
            contexts = []
            if family == 'exception_pickle':
                from .counterfactual import select_context
                sources = {str(f.relative_to(snapshot)): f.read_text() for f in snapshot.rglob('*.py')}
                diff = (run / 'anchor.diff').read_text()
                selected, _ = select_context(sources, diff, 'definition')
                repo, runtime = 'psf/requests', RUNTIME
                target = 'The new exception type may fail to round-trip through pickle because the inherited reduction loses constructor state required to reconstruct it.'
                companion = 'requests/exceptions.py'
            else:
                diff = (run / 'anchor.diff').read_text()
                sources = {BASE_FILE: (snapshot / BASE_FILE).read_text(), KEY_FILE: (snapshot / KEY_FILE).read_text(),
                           'client.py': (snapshot / 'client.py').read_text()}
                selected = [block('client.py', sources['client.py'].splitlines(), 1, 6)]
                # Fixed, manually specified evidence for a hypothesis-generation
                # diagnostic, not a claim of automatic retrieval performance.
                for method in ('__init__', 'save_response', 'create_key', 'delete', 'delete_url'):
                    n = method_node(sources[BASE_FILE], 'BaseCache', method)
                    selected.append(block(BASE_FILE, sources[BASE_FILE].splitlines(), n.lineno, n.end_lineno))
                key_nodes = [n for n in ast.parse(sources[KEY_FILE]).body if isinstance(n, ast.FunctionDef) and n.name in ('create_key', 'url_to_key')]
                for n in key_nodes:
                    selected.append(block(KEY_FILE, sources[KEY_FILE].splitlines(), n.lineno, n.end_lineno))
                repo = 'requests-cache/requests-cache + synthetic client'
                py = execution['cells'][variant + '_head']['python'].split()[0]
                runtime = f'Deployment scope: CPython {py}, requests 2.26.0, an in-memory BaseCache, GET requests, no redirects. Only this runtime is supported. No network is used.'
                target = 'Enabling the namespaced key callback may leave previously stored responses accessible after delete_url, if deletion bypasses the configured key function and computes a different key.'
                companion = BASE_FILE
            for original in selected:
                masked = mask_source(sources[original['file']]).splitlines()
                b = original | {'content': '\n'.join(masked[original['start_line'] - 1:original['end_line']])}
                contexts.append(b)
                audits.append({'task_id': task_id, 'family': family, 'variant': variant, 'id': b['id'],
                               'file': b['file'], 'start': b['start_line'], 'end': b['end_line'],
                               'original_sha256': sha(original['content']), 'masked_sha256': sha(b['content']),
                               'source_snapshot': str(snapshot.resolve())})
            if token_count(render_blocks(contexts)) > 2000:
                raise ValueError('Fixed evidence exceeds predeclared budget')
            row = make_request(task_id, 'fixed_source', diff, contexts, token_count(render_blocks(contexts)))
            row['repository'] = repo; row['pr']['description'] = runtime
            rows.append(row)
            labels[task_id] = {'family': family, 'variant': variant, 'target': target, 'source_run': str(run.resolve())}
            oracles[task_id] = {'hypotheses': [{'id': 'H1', 'claim': target, 'protocol': 'reconstruction' if family == 'exception_pickle' else 'cache invalidation',
                'companion': companion, 'failure_scenario': 'Exercise the described operation after the patch.',
                'refutation': 'The supplied companion implementation preserves the required behavior.', 'evidence_ids': []}]}
    write_new(root / 'requests.jsonl', rows, jsonl=True)
    write_new(root / 'private_labels.json', labels)
    write_new(root / 'oracle_hypotheses.json', oracles)
    write_new(root / 'mask_audit.json', audits)
    return {'families': 2, 'contexts': len(rows), 'source_spans': len(audits)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--python', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_cache(args.repo, args.output, args.python), indent=2))
