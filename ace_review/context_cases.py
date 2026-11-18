"""Fresh context-dependent families; natural change vs synthetic migration explicit."""
import argparse
import ast
import json
import subprocess
from pathlib import Path

from .context_necessity import make_arms
from .counterfactual import block, check_witness, make_diff, make_request, sha, validate_gate, write_new
from .obligation import render_blocks, token_count
from .protocol_cases import replace_method
from .protocol_hypothesis import mask_source

CLICK_ANCHOR = '1aa2d53d63ff68bf14b35931177aac9270e39713'
ANYIO_BASE = 'b0690c59d8fe440ef8c19d5ea5bb76c79be03bd8'
ANYIO_HEAD = '5e125f7a649c166881df94fd5b02b3a6221d1957'
CLIENT = '''import trio


def run_job(job, *args, backend_options=None):
    return trio.run(job, *args, **(backend_options or {}))
'''
MIGRATED_CLIENT = '''import anyio


def run_job(job, *args, backend_options=None):
    return anyio.run(job, *args, backend="trio", backend_options=backend_options)
'''


def pick_prefix(file, source, tokens):
    lines = source.splitlines()
    if not lines:
        raise ValueError('Empty placebo source')
    candidates = [[block(file, lines, 1, end)] for end in range(1, len(lines) + 1)]
    return min(candidates, key=lambda bs: abs(token_count(render_blocks(bs)) - tokens))


def span(file, source, name, include_owner=False):
    nodes = ast.parse(source).body
    parts = name.split('.')
    node = next(n for n in nodes if getattr(n, 'name', None) == parts[0])
    start = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])
    if len(parts) == 2:
        node = next(n for n in node.body if getattr(n, 'name', None) == parts[1])
        if not include_owner:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    return block(file, mask_source(source).splitlines(), start, node.end_lineno)


def prepare_case(family, repo, output, python):
    root = Path(output).resolve(); root.mkdir(parents=True, exist_ok=False)
    def git(*args):
        return subprocess.check_output(['git', '--git-dir=' + str(Path(repo).resolve()), *args]).decode()
    commit = CLICK_ANCHOR if family == 'pager_stream' else ANYIO_BASE
    if family not in ('pager_stream', 'backend_options'):
        raise ValueError('Unknown family')
    package_name = 'click' if family == 'pager_stream' else 'anyio'
    paths = git('ls-tree', '-r', '--name-only', commit).splitlines()
    # The AnyIO root instructions were read before selecting these snapshots.
    instructions = [p for p in paths if Path(p).name == 'AGENTS.md']
    if instructions not in ([], ['AGENTS.md']):
        raise ValueError('Unreviewed nested repository instructions')
    sources = {p: git('show', f'{commit}:{p}') for p in paths if p.startswith(f'src/{package_name}/') and p.endswith('.py')}
    versions, source_commits = {}, {'snapshot': commit}
    if family == 'pager_stream':
        changed_file = 'src/click/_termui_impl.py'
        parent = git('rev-parse', commit + '^').strip()
        before = git('show', f'{parent}:{changed_file}')
        after = sources[changed_file]
        changed_production = git('diff', '--name-only', parent, commit, '--', 'src/').splitlines()
        if changed_production != [changed_file]:
            raise ValueError('Unexpected anchor production changes')
        for variant in ('safe', 'unsafe'):
            for version, text in [('base', before), ('head', after)]:
                if text.count('tempfile.NamedTemporaryFile(mode="wb", delete=False)') != 1:
                    raise ValueError('Tempfile companion anchor mismatch')
                if variant == 'safe':
                    text = text.replace('tempfile.NamedTemporaryFile(mode="wb", delete=False)',
                                        'tempfile.NamedTemporaryFile(mode="w", delete=False)')
                versions[variant + '_' + version] = sources | {changed_file: text}
        source_commits.update(anchor_parent=parent, natural_production_change=True,
                              safe_companion_fix='https://github.com/pallets/click/pull/3739')
        runtime = 'CPython 3.14.6 on macOS. Component scope: built-in tempfile pager path selected with WIN=True and TTY probes true, PAGER=/bin/cat, color=False, standard temporary files, text callers. Subprocess display is replaced by reading its completed temporary file. This is not a native Windows run. No custom pager backends or custom stream types are deployed.'
        target = 'The changed pager branching yields the built-in temporary binary file without a text wrapper, so echo_via_pager writing str fails with TypeError.'
        placebo_file = 'src/click/parser.py'
    else:
        changed_file = 'client.py'
        backend_file = 'src/anyio/_backends/_trio.py'
        donor = git('show', f'{ANYIO_HEAD}:{backend_file}')
        for variant in ('safe', 'unsafe'):
            package = dict(sources)
            if variant == 'safe':
                package[backend_file] = replace_method(package[backend_file], donor, 'TrioBackend', 'run')
            for version, client in [('base', CLIENT), ('head', MIGRATED_CLIENT)]:
                versions[variant + '_' + version] = package | {'client.py': client}
        source_commits.update(companion_head=ANYIO_HEAD, natural_production_change=False,
                              source_fix='https://github.com/agronholm/anyio/pull/1161')
        runtime = 'CPython 3.14.6, Trio 0.32.0, synchronous caller thread outside an event loop. run_job is used with Trio coroutine jobs and backend_options supported by trio.run, including custom clocks. Only the Trio backend is deployed. No network or subprocess workloads.'
        target = 'Migrating run_job to anyio.run loses backend_options in the unchanged TrioBackend.run adapter, so a caller-supplied clock is not installed.'
        placebo_file = 'src/anyio/_core/_fileio.py'
    witness = Path(__file__).parent / f'fixtures/{family}_witness.py'
    cells, hashes, raw_diffs, model_diffs = {}, {}, {}, {}
    for cell, contents in versions.items():
        snapshot = root / 'snapshots' / cell
        for file, text in contents.items():
            write_new(snapshot / file, text, raw=True)
        hashes[cell] = {f: sha(text) for f, text in contents.items()}
        cells[cell] = check_witness(snapshot, witness, python)
    for variant in ('safe', 'unsafe'):
        before, after = [versions[variant + '_' + v][changed_file] for v in ('base', 'head')]
        raw_diffs[variant] = make_diff(before, after, changed_file)
        model_diffs[variant] = make_diff(mask_source(before), mask_source(after), changed_file)
    gate = validate_gate(raw_diffs['safe'], raw_diffs['unsafe'], cells)
    gate['checks']['masked_diff_identical'] = model_diffs['safe'] == model_diffs['unsafe']
    for variant in ('safe', 'unsafe'):
        base, head = hashes[variant + '_base'], hashes[variant + '_head']
        gate['checks'][variant + '_only_anchor_changed'] = set(base) == set(head) and [f for f in base if base[f] != head[f]] == [changed_file]
    gate['eligible'] = all(gate['checks'].values())
    write_new(root / 'execution.json', {'gate': gate, 'cells': cells, 'file_sha256': hashes,
              'source_commits': source_commits, 'family': family, 'safe_variant_is_constructed': True})
    write_new(root / 'anchor.diff', raw_diffs['unsafe'], raw=True)
    write_new(root / 'model.diff', model_diffs['unsafe'], raw=True)
    write_new(root / 'witness.py', witness.read_text(), raw=True)
    for path in paths:
        if path in instructions or ('/' not in path and path.upper().startswith('LICENSE')):
            write_new(root / 'provenance' / path, git('show', f'{commit}:{path}'), raw=True)
    if not gate['eligible']:
        return gate
    fixed_rows, labels = [], {}
    for variant in ('safe', 'unsafe'):
        package = versions[variant + '_head']
        if family == 'pager_stream':
            contexts = [span(changed_file, package[changed_file], name) for name in
                        ('MaybeStripAnsi', '_pager_contextmanager', '_tempfilepager')]
            contexts.append(span('src/click/termui.py', package['src/click/termui.py'], 'echo_via_pager'))
        else:
            file = 'src/anyio/_core/_eventloop.py'
            contexts = [span(file, package[file], name) for name in ('run', 'get_async_backend')]
            file = 'src/anyio/_backends/_trio.py'
            contexts.append(span(file, package[file], 'TrioBackend.run', include_owner=True))
            for file, binding in [('src/anyio/_backends/_trio.py', 'backend_class = TrioBackend'),
                                  ('src/anyio/__init__.py', 'from ._core._eventloop import run as run')]:
                lines = mask_source(package[file]).splitlines()
                line = lines.index(binding) + 1
                contexts.append(block(file, lines, line, line))
        # Stable IDs across variants; IDs carry no semantic or coordinate hints.
        for index, context in enumerate(contexts):
            context['id'] = f'CTX-{index + 1:02}'
        task_id = sha('context-necessity:' + family + ':' + variant)[:16]
        row = make_request(task_id, 'fixed', model_diffs[variant], contexts, token_count(render_blocks(contexts)))
        row['repository'] = 'pallets/click' if family == 'pager_stream' else 'agronholm/anyio + client'
        row['pr']['description'] = runtime
        fixed_rows.append(row)
        labels[task_id] = {'family': family, 'variant': variant, 'target': target, 'source_run': str(root)}
    lengths = [r['context_status']['context_tokens'] for r in fixed_rows]
    placebo = pick_prefix(placebo_file, mask_source(sources[placebo_file]), sum(lengths) / 2)
    placebo_len = token_count(render_blocks(placebo))
    gaps = [abs(placebo_len - length) / length for length in lengths]
    audit = {'related_tokens': lengths, 'irrelevant_tokens': placebo_len, 'relative_length_gaps': gaps,
             'placebo_file': placebo_file, 'source_selection': 'Manual target-informed selection, not retrieval.',
             'family': family}
    write_new(root / 'context_audit.json', audit)
    if max(lengths) > 2000 or max(gaps) > .05:
        raise ValueError('Predeclared source budget or placebo matching failed')
    write_new(root / 'requests.jsonl', [arm for row in fixed_rows for arm in make_arms(row, placebo)], jsonl=True)
    write_new(root / 'private_labels.json', labels)
    return gate


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--family', required=True)
    p.add_argument('--repo', required=True); p.add_argument('--output', required=True); p.add_argument('--python', required=True)
    a = p.parse_args()
    print(json.dumps(prepare_case(a.family, a.repo, a.output, a.python), indent=2))
