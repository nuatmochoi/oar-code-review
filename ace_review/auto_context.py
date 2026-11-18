"""Bounded Python source retrieval using actual SCIP definition/use evidence.

AST supplies chunk boundaries only. The graph is not a sound call/data-flow
graph; paired retrieval is a heuristic over two snapshots, not minimality proof.
No label, witness, manual span or prior model response is an input to retrieval.
"""
from __future__ import annotations

import ast
import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .bm25 import BM25Index, Document
from .counterfactual import block, write_new
from .delta_scip import _span, changed_ranges
from .obligation import render_blocks, token_count
from .protocol_hypothesis import mask_source

ARMS = ('diff', 'bm25', 'scip_head', 'scip_delta')


def safe_path(value):
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Unsafe source path: ' + value)
    return value


@dataclass(frozen=True)
class Chunk:
    key: str
    file: str
    start: int
    end: int
    content: str
    owner: str


def chunks(sources, max_lines=60):
    if max_lines < 1:
        raise ValueError('Positive chunk length required')
    result = {}
    for file, source in sorted(sources.items()):
        safe_path(file)
        lines = mask_source(source).splitlines()
        counts = defaultdict(int)

        def emit(label, start, end):
            counts[label] += 1
            owner = f'{file}::{label}::{counts[label]}'
            for part, lo in enumerate(range(start, end + 1, max_lines)):
                hi = min(end, lo + max_lines - 1)
                content = '\n'.join(lines[lo - 1:hi])
                if content.strip():
                    key = f'{owner}::{part}'
                    result[key] = Chunk(key, file, lo, hi, content, owner)

        def visit(nodes, prefix=''):
            for node in nodes:
                start = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])
                if isinstance(node, ast.ClassDef):
                    label = prefix + node.name
                    emit(label + ':header', start, node.body[0].lineno - 1)
                    visit(node.body, label + '.')
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    emit(prefix + node.name, start, node.end_lineno)
                else:
                    identity = hashlib.sha256(ast.dump(node).encode()).hexdigest()[:16]
                    emit(prefix + type(node).__name__ + ':' + identity, start, node.end_lineno)
        visit(ast.parse(source).body)
    return result


def changed_query(diff):
    return '\n'.join(line[1:] for line in diff.splitlines()
                     if line.startswith(('+', '-')) and not line.startswith(('+++', '---')))


class Graph:
    def __init__(self, sources, raw):
        self.chunks = chunks(sources)
        self.edges = {k: set() for k in self.chunks}
        self.by_line = defaultdict(set)
        owners = defaultdict(list)
        for key, c in self.chunks.items():
            owners[c.owner].append(key)
            for line in range(c.start, c.end + 1):
                self.by_line[c.file, line].add(key)
        for group in owners.values():
            for key in group:
                self.edges[key].update(set(group) - {key})
        definitions, uses, relations = defaultdict(set), [], []
        self.symbols_at_line = defaultdict(set)
        document_files, occurrence_count = set(), 0
        for doc in raw.get('documents', []):
            file = safe_path(doc['relativePath'])
            if file not in sources:
                continue
            document_files.add(file)
            def scoped(symbol):
                return file + ':' + symbol if symbol.startswith('local ') else symbol
            for o in doc.get('occurrences', []):
                span = _span(o)
                if span is None or not o.get('symbol'):
                    continue
                occurrence_count += 1
                symbol = scoped(o['symbol'])
                locations = set()
                for line in range(span[0], span[1] + 1):
                    locations.update(self.by_line[file, line])
                    self.symbols_at_line[file, line].add(symbol)
                if o.get('symbolRoles', 0) & 1:
                    definitions[symbol].update(locations)
                else:
                    uses.append((symbol, locations))
            for info in doc.get('symbols', []):
                for relation in info.get('relationships', []):
                    if any(relation.get(k) for k in ('isImplementation', 'isDefinition', 'isTypeDefinition', 'isReference')):
                        relations.append((scoped(info['symbol']), scoped(relation['symbol'])))
        self.definitions = definitions
        def connect(left, right):
            for a in left:
                for b in right:
                    if a != b:
                        self.edges[a].add(b)
                        self.edges[b].add(a)
        for symbol, locations in uses:
            connect(locations, definitions.get(symbol, set()))
        for left, right in relations:
            connect(definitions.get(left, set()), definitions.get(right, set()))
        self.audit = dict(source_files=len(sources), indexed_source_files=len(document_files),
                          missing_documents=sorted(set(sources) - document_files),
                          chunks=len(self.chunks), occurrences=occurrence_count,
                          reference_occurrences=len(uses),
                          repo_resolved_reference_occurrences=sum(bool(definitions.get(s)) for s, _ in uses),
                          undirected_edges=sum(map(len, self.edges.values())) // 2)

    def seeds(self, changes):
        seeds = set()
        for file, lines in changes.items():
            for line in lines:
                seeds.update(self.by_line[file, line])
                for symbol in self.symbols_at_line[file, line]:
                    seeds.update(self.definitions.get(symbol, set()))
        return seeds

    def walk(self, seeds, hops=2):
        distance = {key: 0 for key in sorted(seeds)}
        queue = deque(sorted(seeds))
        while queue:
            key = queue.popleft()
            if distance[key] >= hops:
                continue
            for other in sorted(self.edges[key]):
                if other not in distance:
                    distance[other] = distance[key] + 1
                    queue.append(other)
        return distance


def retrieve(base, head, diff, arm, budget=800):
    if arm not in ARMS or budget < 0:
        raise ValueError('Invalid arm or budget')
    query = changed_query(diff)
    corpus = head.chunks
    index = BM25Index(Document(k, 0, 0, c.content) for k, c in corpus.items())
    scores = {d.file: s for s, d in index.search(query, top_k=len(corpus))}
    changes = changed_ranges(diff)
    head_seeds, base_seeds = head.seeds(changes['head']), base.seeds(changes['base'])
    hdist, bdist = head.walk(head_seeds), base.walk(base_seeds)
    distance = dict(hdist)
    if arm == 'scip_delta':
        for key, d in bdist.items():
            if key in corpus:
                distance[key] = min(distance.get(key, d), d)
    changed = {key for key in distance if key not in base.chunks or
               base.chunks[key].content != corpus[key].content or base.edges[key] != head.edges[key]}
    if arm == 'diff':
        order = []
    elif arm == 'bm25':
        order = sorted(scores, key=lambda k: (-scores[k], corpus[k].file, corpus[k].start, k))
    else:
        def rank(k):
            c = corpus[k]
            outside = bool(set(range(c.start, c.end + 1)) - changes['head'].get(c.file, set()))
            return (-(arm == 'scip_delta' and k in changed), distance[k], -outside,
                    -scores.get(k, 0), c.file, c.start, k)
        order = sorted(distance, key=rank)
    selected, keys, skipped = [], [], []
    for key in order:
        c = corpus[key]
        candidate = block(c.file, c.content.splitlines())
        candidate.update(id=f'CTX-{len(selected) + 1:02}', start_line=c.start, end_line=c.end)
        if token_count(render_blocks(selected + [candidate])) <= budget:
            selected.append(candidate); keys.append(key)
        else:
            skipped.append(key)
    audit = dict(arm=arm, budget=budget, source_tokens=token_count(render_blocks(selected)),
                 query=query, max_hops=2, head_seeds=sorted(head_seeds), base_seeds=sorted(base_seeds),
                 reachable_head=len(hdist), reachable_base=len(bdist), candidates=len(order),
                 ranked_keys=order, selected_keys=keys, budget_skipped_keys=skipped,
                 changed_reachable_keys=sorted(changed),
                 selected_spans=[dict(file=b['file'], start=b['start_line'], end=b['end_line']) for b in selected])
    return selected, audit


def stage_snapshot(source, output, project, runtime):
    source, output, runtime = Path(source), Path(output), Path(runtime).resolve()
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for file in sorted(source.rglob('*.py')):
        if file.is_symlink():
            raise ValueError('Symlink in source corpus')
        name = str(file.relative_to(source))
        content = file.read_text()
        hashes[name] = hashlib.sha256(content.encode()).hexdigest()
        write_new(output / name, content, raw=True)
    config = dict(include=['src', '*.py'], extraPaths=['src'], pythonVersion='3.11',
                  venvPath=str(runtime.parent), venv=runtime.name)
    write_new(output / 'pyrightconfig.json', config)
    # Project metadata is generated indexing configuration, never model evidence.
    write_new(output / 'pyproject.toml', f'[project]\nname = "{project}"\nversion = "0.0.0"\n', raw=True)
    return dict(source_sha256=hashes, pyrightconfig=config)
