"""Paper-inspired retrieval policies, with cumulative evidence accounting.

This is a development implementation, not S2G/CIRAG/QAFD reproduction. Graph
edges are static index associations, not proofs of execution or data flow.
No gold label, witness, target mechanism or world name is accepted by selectors.
"""
from __future__ import annotations

import ast
from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import json

from .bm25 import BM25Index, Document
from .delta_scip import changed_ranges
from .obligation import token_count
from .protocol_hypothesis import mask_source


ARMS = ('D', 'L', 'R', 'S', 'C', 'Q', 'SCQ')
SCHEDULE = (800, 1200, 1600)


def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class Unit:
    id: str
    file: str
    version: str
    start: int
    end: int
    symbol: str
    lines: tuple[str, ...]
    header_end: int
    kind: str

    @property
    def text(self):
        return '\n'.join(self.lines)

    def positions(self, level):
        if level in ('L1', 'L2') or self.kind != 'function':
            return set(range(self.start, self.end + 1))
        # Partial anchors are explicitly marked as such; never presented as a
        # complete body, semantic summary, or evidence of absent behavior.
        return set(range(self.start, self.header_end + 1)) | {self.end}


def source_units(sources, version):
    result = {}
    for file, source in sorted(sources.items()):
        tree = ast.parse(source)
        lines = tuple(mask_source(source).splitlines())

        def emit(node, symbol, start=None, end=None, kind=None):
            start = start or min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])
            end = end or node.end_lineno
            if end < start:
                return
            kind = kind or ('function' if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else 'statement')
            header_end = max(start, node.body[0].lineno - 1) if kind == 'function' else end
            key = 'E-' + digest([version, file, start, end, symbol])[:12]
            result[key] = Unit(key, file, version, start, end, symbol,
                               lines[start - 1:end], header_end, kind)

        def visit(nodes, prefix=''):
            for node in nodes:
                if isinstance(node, ast.ClassDef):
                    emit(node, prefix + node.name + ':class', end=node.body[0].lineno - 1)
                    visit(node.body, prefix + node.name + '.')
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    emit(node, prefix + node.name)
                else:
                    emit(node, prefix + type(node).__name__ + ':' + str(node.lineno))
        visit(tree.body)
    return result


class Corpus:
    def __init__(self, versions, graph_specs, diff):
        self.units = {}
        self.locations = defaultdict(set)
        self.edges = defaultdict(set)
        self.edge_kinds = defaultdict(set)
        self.version_aliases = {}
        self.source_hashes = {v: {f: digest(s) for f, s in sorted(src.items())} for v, src in versions.items()}
        # Identical base/head units share one head representation. Changed base
        # units remain available; this avoids paying twice for unchanged code.
        head = source_units(versions.get('head', {}), 'head')
        self.units.update(head)
        identities = {(u.file, u.start, u.end, u.symbol, u.text): k for k, u in head.items()}
        for version in ('head', 'base'):
            units = head if version == 'head' else source_units(versions.get('base', {}), 'base')
            for key, u in units.items():
                identity = (u.file, u.start, u.end, u.symbol, u.text)
                canonical = identities.get(identity, key)
                if canonical == key:
                    self.units[key] = u
                self.version_aliases[key] = canonical
                for line in range(u.start, u.end + 1):
                    self.locations[version, u.file, line].add(canonical)
        for version, spec in graph_specs.items():
            mapping = {}
            for key, node in spec['nodes'].items():
                candidates = set()
                for line in range(node['start'], node['end'] + 1):
                    candidates.update(self.locations[version, node['file'], line])
                mapping[key] = candidates
            for edge in spec['edges']:
                for a in mapping.get(edge['source'], ()):
                    for b in mapping.get(edge['target'], ()):
                        if a != b:
                            self.edges[a].add(b)
                            self.edges[b].add(a)
                            self.edge_kinds[tuple(sorted((a, b)))].add(edge['kind'])
        self.seeds = set()
        self.diff_visible = defaultdict(set)
        ranges = changed_ranges(diff)
        for version in ('base', 'head'):
            for file, lines in ranges[version].items():
                for line in lines:
                    self.seeds.update(self.locations[version, file, line])
        # All evidence stays in the budget. Diff overlap is recorded rather
        # than silently subtracting the cost of repeated source lines.
        self.index = BM25Index(Document(k, u.start, u.end, u.symbol + '\n' + u.text)
                               for k, u in sorted(self.units.items()))

    def scores(self, query):
        return {d.file: s for s, d in self.index.search(query, top_k=len(self.units))}

    def candidates(self, query):
        scores = self.scores(query)
        lexical = sorted(scores, key=lambda k: (-scores[k], k))[:64]
        distance = {k: 0 for k in sorted(self.seeds)}
        parent = {k: None for k in self.seeds}
        queue = deque(sorted(self.seeds))
        truncated = 0
        while queue:
            key = queue.popleft()
            if distance[key] >= 4:
                continue
            for other in sorted(self.edges[key]):
                if other in distance:
                    continue
                if len(distance) >= 4096:
                    truncated += 1
                    continue
                distance[other] = distance[key] + 1
                parent[other] = key
                queue.append(other)
        return set(lexical) | set(distance), scores, parent, truncated

    def ppr(self, candidates, scores, weighted=False):
        seeds = sorted(self.seeds & candidates)
        if not seeds:
            return {}, {'iterations': 0, 'converged': True, 'no_seeds': True}
        nodes = sorted(candidates)
        restart = {k: (1 / len(seeds) if k in self.seeds else 0) for k in nodes}
        p = dict(restart)
        maximum = max((scores.get(k, 0) for k in nodes), default=0)
        relevance = {k: scores.get(k, 0) / maximum if maximum else 0 for k in nodes}
        neighbors = {}
        for k in nodes:
            ws = {v: .1 + .9 * (relevance[k] + relevance[v]) / 2 if weighted else 1.
                  for v in sorted(self.edges[k] & candidates)}
            total = sum(ws.values())
            neighbors[k] = {v: w / total for v, w in ws.items()} if total else {}
        converged = False
        for iteration in range(1, 51):
            dangling = sum(p[k] for k in nodes if not neighbors[k])
            new = {k: (.15 + .85 * dangling) * restart[k] for k in nodes}
            for k, ns in neighbors.items():
                for v, weight in ns.items():
                    new[v] += .85 * p[k] * weight
            delta = sum(abs(new[k] - p[k]) for k in nodes)
            p = new
            if delta < 1e-6:
                converged = True
                break
        return p, {'iterations': iteration, 'converged': converged, 'residual_l1': delta, 'no_seeds': False}

    def order(self, query, weighted):
        candidates, scores, parent, truncated = self.candidates(query)
        ppr, audit = self.ppr(candidates, scores, weighted)
        connected = [k for k in candidates if k in parent]
        # A quarter of selection slots are lexical fallbacks, shared by all
        # graph arms. No gold endpoint is promoted.
        graph_order = sorted(connected, key=lambda k: (k in self.seeds, -ppr.get(k, 0), -scores.get(k, 0), k))
        lexical = sorted(candidates, key=lambda k: (-scores.get(k, 0), k))
        ordered, seen = [], set()
        gi = li = 0
        while gi < len(graph_order) or li < len(lexical):
            for _ in range(3):
                if gi < len(graph_order):
                    k = graph_order[gi]; gi += 1
                    if k not in seen:
                        ordered.append(k); seen.add(k)
            if li < len(lexical):
                k = lexical[li]; li += 1
                if k not in seen:
                    ordered.append(k); seen.add(k)
        paths = {}
        for k in ordered:
            path, at = [], k
            while at is not None:
                path.append(at)
                at = parent.get(at)
            paths[k] = list(reversed(path))
        return ordered, paths, dict(audit, candidate_count=len(candidates), candidate_ids=sorted(candidates),
                                   graph_cap_skips=truncated, query=query,
                                   ppr_scores=ppr, lexical_scores=scores)


class EvidenceState:
    def __init__(self, corpus):
        self.corpus = corpus
        self.lines = {}
        self.paths = {}

    def render(self, lines=None, paths=None):
        lines = self.lines if lines is None else lines
        paths = self.paths if paths is None else paths
        output = []
        for key in sorted(lines, key=lambda k: (self.corpus.units[k].version, self.corpus.units[k].file,
                                                self.corpus.units[k].start, k)):
            unit = self.corpus.units[key]
            full = lines[key] == set(range(unit.start, unit.end + 1))
            output.append(f'[{key}] {unit.version} {unit.file}:{unit.start}-{unit.end} '
                          + ('whole AST unit' if full else 'PARTIAL ANCHORS; omitted code unknown'))
            previous = None
            for line in sorted(lines[key]):
                if previous is not None and line != previous + 1:
                    output.append('... omitted lines ...')
                output.append(f'{line}: {unit.lines[line - unit.start]}')
                previous = line
        for key, nodes in sorted(paths.items()):
            if all(n in lines for n in nodes):
                output.append(f'[{key}] Static index candidate path (undirected association; execution unresolved): '
                              + ' <-> '.join(nodes))
        return '\n'.join(output)

    @property
    def tokens(self):
        return token_count(self.render())

    def add(self, nodes, level, cap):
        nodes = list(dict.fromkeys(nodes))
        updated = {k: set(v) for k, v in self.lines.items()}
        for k in nodes:
            updated.setdefault(k, set()).update(self.corpus.units[k].positions(level))
        path_id = 'P-' + digest(nodes)[:12]
        paths = dict(self.paths)
        if len(nodes) > 1:
            paths[path_id] = nodes
        text = self.render(updated, paths)
        if token_count(text) > cap:
            return False
        changed = updated != self.lines or paths != self.paths
        self.lines, self.paths = updated, paths
        return changed

    def audit(self):
        return {'tokens': self.tokens, 'source_lines': {k: sorted(v) for k, v in sorted(self.lines.items())},
                'paths': self.paths, 'packet_sha256': digest(self.render())}


def validate_controller(response, state, questions):
    errors = []
    qids = {q['id'] for q in questions}
    got = [q['question_id'] for q in response['questions']]
    if set(got) != qids or len(got) != len(qids):
        errors.append('question_ids_mismatch')
    anchors = set(state.lines) | {'DIFF'}
    for question in response['questions']:
        references = list(question['evidence_ids'])
        for gap in question['gaps']:
            references.extend(gap['anchor_ids'])
        for expansion in question['expansion_requests']:
            references.extend(expansion['anchor_ids'])
            if expansion['path_id'] not in state.paths and expansion['path_id'] not in state.lines:
                errors.append('unknown_path:' + expansion['path_id'])
        errors.extend('unknown_anchor:' + k for k in references if k not in anchors)
    return sorted(set(errors))


def retrieve_round(corpus, state, questions, diff_query, arm, round_index, controller=None):
    if arm not in ARMS:
        raise ValueError('unknown arm')
    cap = 1600 if arm == 'L' else SCHEDULE[round_index]
    adaptive_query, adaptive_size, weighted = arm in ('S', 'SCQ'), arm in ('C', 'SCQ'), arm in ('Q', 'SCQ')
    audits, blocked = [], []
    if arm == 'D':
        return {'round': round_index, 'cap': 0, 'state': state.audit(), 'queries': []}
    updates = {q['question_id']: q for q in controller['questions']} if controller else {}
    if adaptive_size and controller:
        for question in controller['questions']:
            for request in question['expansion_requests']:
                nodes = state.paths.get(request['path_id'], [request['path_id']])
                nodes = [k for k in nodes if k in corpus.units]
                if request['target_level'] == 'L2':
                    # Scope expansion includes all one-hop neighbors. This is
                    # deliberately conservative, atomic, and may not fit.
                    nodes = sorted(set(nodes) | {v for k in nodes for v in corpus.edges[k]})
                if not state.add(nodes, 'L1', cap):
                    blocked.append({'requested': request, 'reason': 'no_novel_source_or_budget_blocked'})
    for question in questions:
        query = question['question'] + ' ' + ' '.join(question['symbols']) + ' ' + diff_query
        update = updates.get(question['id'])
        if adaptive_query and update and update['gaps']:
            query = ' '.join(g['retrieval_query'] + ' ' + g['needed_fact'] for g in update['gaps'])
        if arm == 'L':
            scores = corpus.scores(query)
            order = sorted(scores, key=lambda k: (-scores[k], k))
            paths = {k: [k] for k in order}
            audit = {'query': query, 'candidate_ids': order, 'candidate_count': len(order)}
        else:
            order, paths, audit = corpus.order(query, weighted)
        accepted = []
        for key in order:
            level = 'L0' if adaptive_size else 'L1'
            path = paths[key]
            if state.add(path, level, cap):
                accepted.append(key)
                if arm != 'L' and len(accepted) >= 2:
                    break
        audits.append(dict(audit, question_id=question['id'], accepted_endpoints=accepted))
    assert state.tokens <= cap
    return {'round': round_index, 'cap': cap, 'state': state.audit(), 'queries': audits, 'blocked_expansions': blocked}


QUESTION_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['questions'],
    'properties': {'questions': {'type': 'array', 'minItems': 1, 'maxItems': 3,
        'items': {'type': 'object', 'additionalProperties': False,
            'required': ['id', 'question', 'symbols'], 'properties': {
                'id': {'type': 'string'}, 'question': {'type': 'string'},
                'symbols': {'type': 'array', 'maxItems': 6, 'items': {'type': 'string'}}}}}}}

FINAL_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['status', 'issues', 'unresolved_questions'], 'properties': {
        'status': {'type': 'string', 'enum': ['reported_issues', 'no_supported_issue', 'insufficient_evidence']},
        'issues': {'type': 'array', 'maxItems': 5, 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['file', 'line', 'claim', 'failure_scenario', 'evidence_ids'], 'properties': {
                'file': {'type': 'string'}, 'line': {'type': 'integer', 'minimum': 1},
                'claim': {'type': 'string'}, 'failure_scenario': {'type': 'string'},
                'evidence_ids': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}}}}},
        'unresolved_questions': {'type': 'array', 'maxItems': 5, 'items': {'type': 'string'}}}}

PROPOSAL_PROMPT = '''Using only this patch and deployment scope, produce one to three neutral
questions whose answers in unchanged repository source would help establish or
refute a correctness regression. These are retrieval questions, not defect claims.
Consider actual producers, argument bindings, guards and implementation choices.
Do not guess source identifiers absent from the patch. IDs must be Q1, Q2, Q3 in
order. No tools. Return the requested JSON.'''

CONTROLLER_PROMPT = '''You control evidence retrieval, not the final defect verdict.
For every supplied question, judge whether the actually visible code suffices to
support or refute the concern. Lexical overlap is not sufficient. Static graph
associations do not prove runtime dispatch. PARTIAL ANCHORS omit code: never infer
that missing code does not exist. If insufficient, list precise missing facts,
possible falsifiers and search queries. For expansion, use a visible P-* path ID
or an E-* unit ID as path_id, and cite its visible E-* anchors. L1 requests a whole
AST function/statement; L2 requests its entire static one-hop neighborhood too,
which can exceed budget. Cite only visible E-* IDs or DIFF. A sufficient question
must cite evidence and have no gaps or expansion requests. No tools. JSON only.'''

FINAL_PROMPT = '''Review the patch for concrete introduced behavioral defects in the stated
deployment scope. Use only the supplied patch and repository evidence, not a
remembered upstream implementation. Questions are neutral retrieval hypotheses,
not evidence. Partial anchors omit code and static graph associations do not
prove runtime dispatch. Report up to five supported issues, with the exact cause,
conditions and observable consequence; cite DIFF and/or visible E-* evidence.
Do not report speculative possibilities, style issues or pre-existing defects.
If evidence cannot decide a relevant concern, use insufficient_evidence; absence
of evidence is not proof of safety. reported_issues requires a nonempty issues
list. Other statuses require an empty issues list. No tools. JSON only.'''
