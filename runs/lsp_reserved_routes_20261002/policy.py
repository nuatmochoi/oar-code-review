"""Case-independent native definition/reference traversal with atomic source packages."""
import ast
import builtins
import heapq
import re
from pathlib import Path
from urllib.parse import unquote, urlparse
from ace_review.context_adaptive import digest, token_count
from ace_review.protocol_hypothesis import mask_source

CAP = 2600
MAX_QUERIES = 12
MAX_DEPTH = 3


def index(sources):
    defs = {}
    parents = {}
    for file, source in sorted(sources.items()):
        def visit(nodes, prefix='', owners=()):
            for node in nodes:
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = prefix + node.name
                    defs[file, name] = node
                    parents[file, name] = owners
                    if isinstance(node, ast.ClassDef):
                        visit(node.body, name + '.', owners + (node,))
        visit(ast.parse(source).body)
    return defs, parents


def package(sources, defs, parents, target):
    file, symbol = target
    node = defs[target]
    lines = set()
    def header(n):
        start = min([n.lineno] + [d.lineno for d in n.decorator_list])
        lines.update(range(start, n.body[0].lineno))
    for owner in parents[target]: header(owner)
    if isinstance(node, ast.ClassDef):
        header(node)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name not in {'__init__', '__call__'}: continue
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
            elif isinstance(child, ast.ClassDef): continue
            else: start = child.lineno
            lines.update(range(start, child.end_lineno + 1))
    else:
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        lines.update(range(start, node.end_lineno + 1))
    # Preserve imports for names actually used by the selected declaration.
    used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    for imp in ast.parse(sources[file]).body:
        if isinstance(imp, (ast.Import, ast.ImportFrom)) and any((a.asname or a.name.split('.')[0]) in used for a in imp.names):
            lines.update(range(imp.lineno, imp.end_lineno + 1))
    return {file: lines}


def render(sources, lines):
    chunks = []
    for file, selected in sorted(lines.items()):
        if not selected: continue
        key = 'E-' + digest(['automatic-native-package', file])[:12]
        chunks.append(f'[{key}] head {file}:{min(selected)}-{max(selected)} PARTIAL FILE; admitted function bodies and owner headers are complete; omitted code unknown')
        rows = mask_source(sources[file]).splitlines()
        previous = None
        for line in sorted(selected):
            if previous is not None and previous + 1 != line: chunks.append('... omitted lines ...')
            chunks.append(f'{line}: {rows[line - 1]}')
            previous = line
    return '\n'.join(chunks)


def position(source, node):
    row = source.splitlines()[node.lineno - 1]
    # AST offsets are UTF8 bytes; LSP offsets are UTF16 code units.
    prefix = row.encode('utf-8')[:node.col_offset].decode('utf-8')
    if isinstance(node, ast.Attribute):
        row = source.splitlines()[node.end_lineno - 1]
        prefix = row.encode('utf-8')[:node.end_col_offset].decode('utf-8')[:-len(node.attr)]
        return node.end_lineno - 1, len(prefix.encode('utf-16-le')) // 2
    return node.lineno - 1, len(prefix.encode('utf-16-le')) // 2


def packet(sources, diff, scope, resolver, native, client, edited_lines):
    defs, parents = index(sources)
    edits = edited_lines(diff)
    roots = []
    for target, node in defs.items():
        if isinstance(node, ast.ClassDef): continue
        if any(node.lineno <= line <= node.end_lineno for line in edits['head'].get(target[0], set())):
            roots.append(target)
    # Scope only ranks changed roots; no gold paths, case IDs or witness inputs.
    words = set(re.findall(r'[A-Za-z_]\w*', scope.lower()))
    def score(t):
        return len(words & set(re.findall(r'[A-Za-z_]\w*', (t[1] + ' ' + ast.unparse(defs[t])).lower())))
    roots.sort(key=lambda t: (-score(t), t[0], defs[t].lineno, t[1]))
    state = {}
    admitted = []
    admitted_set = set()
    skipped = []
    blocked = []
    queue = []
    queued = set()
    queried = set()
    queries = []
    sequence = 0
    def admit(target, origin):
        if target not in defs: return False
        if target in admitted_set: return True
        addition = package(sources, defs, parents, target)
        trial = {f: set(v) for f, v in state.items()}
        for f, lines in addition.items(): trial.setdefault(f, set()).update(lines)
        tokens = token_count(render(sources, trial))
        if tokens > CAP:
            blocked.append({'target': target, 'origin': origin, 'reason': 'Whole source package exceeds total cap; no truncated substitute', 'trial_tokens': tokens})
            return False
        state.clear(); state.update(trial)
        admitted_set.add(target)
        admitted.append({'target': target, 'origin': origin, 'lines': {f: sorted(v) for f, v in addition.items()}})
        return True
    def enqueue(target, depth, origin):
        nonlocal sequence
        if target in queued or target not in defs or depth > MAX_DEPTH: return
        queued.add(target)
        file, symbol = target
        node = defs[target]
        row = sources[file].splitlines()[node.lineno - 1]
        match = re.search(r'\b(?:def|class)\s+(' + re.escape(node.name) + r')\b', row)
        col = len(row[:match.start(1)].encode('utf-16-le')) // 2
        # Root critical calls/references precede a reserved follow-up lane;
        # unmodified root plumbing waits until newly admitted code is explored.
        def push(method, callee, kind, changed=False):
            nonlocal sequence
            if callee is None: line, char, label = node.lineno - 1, col, symbol
            else:
                line, char = position(sources[file], callee)
                label = ast.unparse(callee)
            lane = (0 if changed else 1 if method == 'references' else 3) if depth == 0 else (4 if method == 'references' else 2)
            heapq.heappush(queue, ((lane, kind, depth, line), sequence, method, file, line, char, label, origin)); sequence += 1
        push('references', None, 2)
        sites = []; seen_symbols = set()
        return_calls = {id(c) for ret in ast.walk(node) if isinstance(ret, ast.Return) and ret.value is not None for c in ast.walk(ret.value) if isinstance(c, ast.Call)}
        for call in ast.walk(node):
            if not isinstance(call, ast.Call) or call.lineno not in state.get(file, set()): continue
            callee = call.func
            if not isinstance(callee, (ast.Name, ast.Attribute)): continue
            label = ast.unparse(callee)
            if isinstance(callee, ast.Name) and callee.id in vars(builtins):
                skipped.append({'file': file, 'line': call.lineno, 'symbol': label, 'reason': 'Python builtin-name heuristic; shadowing possible'})
                continue
            if isinstance(callee, ast.Attribute) and isinstance(callee.value, (ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set)):
                skipped.append({'file': file, 'line': call.lineno, 'symbol': label, 'reason': 'Literal-receiver operation'})
                continue
            changed = any(call.lineno <= line <= call.end_lineno for line in edits['head'].get(file, set()))
            sites.append((not changed, 0 if id(call) in return_calls else 1 if isinstance(callee, ast.Name) else 2, callee))
        for not_changed, kind, callee in sorted(sites, key=lambda x: (x[0], x[1], x[2].lineno, x[2].col_offset)):
            label = ast.unparse(callee)
            if label in seen_symbols:
                skipped.append({'file': file, 'line': callee.lineno, 'symbol': label, 'reason': 'Repeated syntactic callee within declaration; receiver runtime may differ'})
                continue
            seen_symbols.add(label); push('definition', callee, kind, not not_changed)
        # Return-field lookup can expose property consumers without manual names.
        for ret in ast.walk(node):
            if not isinstance(ret, ast.Return) or ret.lineno not in state.get(file, set()): continue
            for attr in ast.walk(ret.value) if ret.value is not None else []:
                if not isinstance(attr, ast.Attribute): continue
                label = ast.unparse(attr)
                if label not in seen_symbols:
                    seen_symbols.add(label); push('definition', attr, 3)
    for target in roots:
        if admit(target, 'changed_head_root'): enqueue(target, 0, 'changed_head_root')
    while queue and len(queries) < MAX_QUERIES:
        priority, _, method, file, line, char, symbol, origin = heapq.heappop(queue)
        site = (method, file, line, char)
        if site in queried: continue
        queried.add(site)
        targets = []; excluded = []; raw = None; error = None
        try:
            raw = getattr(client, method)(file, line, char)
            if method == 'definition': targets, excluded = native.map_result(raw)
            else:
                for loc in raw or []:
                    uri = loc.get('uri', loc.get('targetUri'))
                    rng = loc.get('range', loc.get('targetSelectionRange'))
                    try: ref_file = str(Path(unquote(urlparse(uri).path)).relative_to(client.root))
                    except (ValueError, TypeError): excluded.append({'uri': uri, 'reason': 'outside workspace'}); continue
                    ref_line = rng['start']['line'] + 1
                    owners = [(t, n) for t, n in defs.items() if t[0] == ref_file and n.lineno <= ref_line <= n.end_lineno]
                    if owners:
                        target = min(owners, key=lambda x: (x[1].end_lineno - x[1].lineno, x[0]))[0]
                        targets.append(target)
                    else: excluded.append({'file': ref_file, 'line': ref_line, 'reason': 'native reference has no enclosing indexed declaration'})
            targets = list(dict.fromkeys(tuple(t) for t in targets))
        except Exception as exc: error = repr(exc)
        added = []
        for target in targets:
            if admit(target, 'native_' + method):
                added.append(target); enqueue(target, priority[2] + 1, 'native_' + method)
        queries.append({'method': method, 'file': file, 'line': line + 1, 'utf16_character': char, 'symbol': symbol, 'origin': origin, 'depth': priority[2], 'navigation_lane': priority[0], 'raw_result': raw, 'targets': targets, 'excluded': excluded, 'error': error, 'admitted_targets': added})
    source = render(sources, state)
    obs = []
    for i, q in enumerate(queries, 1):
        line = f'[E-AUTO-NAV-{i:02d}] {q["method"]} {q["file"]}:{q["line"]} -> ' + ('; '.join(f'{f}::{s}' for f, s in q['targets'][:3]) or 'unresolved') + '. Static candidates; execution/arguments unknown.'
        if token_count('\n'.join(obs + [line])) <= 330: obs.append(line)
    tail = f'[E-AUTO-COVERAGE] Source packages blocked={len(blocked)}; pending navigation={len(queue)}. Missing connections and omitted code unknown; no complete runtime-path certification.'
    if token_count('\n'.join(obs + [tail])) <= 400: obs.append(tail)
    return source, '\n'.join(obs), {'state': {'tokens': token_count(source), 'source_lines': {f: sorted(v) for f, v in state.items()}, 'packet_sha256': digest(source)}, 'roots': roots, 'admissions': admitted, 'blocked': blocked, 'jumps': queries, 'pending_navigation': len(queue), 'skipped_sites': skipped, 'pending_queries': [{'method': q[2], 'symbol': q[6], 'lane': q[0][0], 'depth': q[0][2]} for q in sorted(queue)], 'scheduling': 'root changed=0, root references=1, admitted definitions=2 (return calls first), other root sites=3, admitted references=4; depth cap3; syntax duplicates and builtin/literal heuristics skipped', 'source_cap': CAP, 'query_cap': MAX_QUERIES, 'runtime_certified': False, 'automatic_case_independent': True}
