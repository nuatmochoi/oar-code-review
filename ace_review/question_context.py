"""Question-conditioned retrieval; scores are relevance proxies, not truth values."""
import ast
import copy
import textwrap

from .auto_context import retrieve
from .bm25 import BM25Index, Document
from .counterfactual import block
from .obligation import render_blocks, token_count
from .selector_ablation import retrieve_selector

ARMS = ('diff_recheck', 'reserve', 'claim_search', 'question_search')
OPERATIONS = ('forwarding', 'representation', 'guard', 'resource', 'other')
PROPOSE = '''Review the supplied production-source patch and runtime. Generate at
most three concrete correctness hypotheses. For each, state the claim and failure
scenario, then at most two questions about external source facts whose answers
would support or refute it. Questions must be neutral and test both explanations.
Give source identifiers in symbols only when supported by the supplied patch;
use an empty list if the location is unknown. Do not invent an implementation or
assume a remembered upstream version. support_if and refute_if are hypothetical
outcomes, never established facts. operation describes the requested observation:
forwarding (argument/configuration use), representation (produced or consumed
value/type), guard (conditions), resource (lifetime), or other.
An empty hypotheses list is allowed. Use no tools. Return the required JSON.'''


def proposal_schema():
    question = {'type': 'object', 'additionalProperties': False,
                'required': ['question', 'symbols', 'operation', 'support_if', 'refute_if'],
                'properties': {
                    'question': {'type': 'string', 'maxLength': 500},
                    'symbols': {'type': 'array', 'maxItems': 8,
                                'items': {'type': 'string', 'maxLength': 120}},
                    'operation': {'type': 'string', 'enum': list(OPERATIONS)},
                    'support_if': {'type': 'string', 'maxLength': 500},
                    'refute_if': {'type': 'string', 'maxLength': 500}}}
    hypothesis = {'type': 'object', 'additionalProperties': False,
                  'required': ['claim', 'failure_scenario', 'questions'],
                  'properties': {'claim': {'type': 'string', 'maxLength': 600},
                                 'failure_scenario': {'type': 'string', 'maxLength': 800},
                                 'questions': {'type': 'array', 'maxItems': 2, 'items': question}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['hypotheses'],
            'properties': {'hypotheses': {'type': 'array', 'maxItems': 3, 'items': hypothesis}}}


def operation_features(content):
    """Syntactic affordances only; no claim that a path executes or values flow."""
    try:
        nodes = list(ast.walk(ast.parse(textwrap.dedent(content))))
    except (SyntaxError, IndentationError):
        return set()
    found = set()
    if any(isinstance(n, ast.arg) for n in nodes) and any(isinstance(n, ast.Call) for n in nodes):
        found.add('forwarding')
    if any(isinstance(n, (ast.Return, ast.Yield, ast.YieldFrom, ast.Call)) for n in nodes):
        found.add('representation')
    if any(isinstance(n, (ast.If, ast.Assert, ast.Compare)) for n in nodes):
        found.add('guard')
    if any(isinstance(n, (ast.With, ast.AsyncWith, ast.Try)) for n in nodes):
        found.add('resource')
    return found


def canonical_blocks(corpus, keys):
    """Same source order/IDs in every arm, independent of retrieval schedule."""
    ordered = sorted(keys, key=lambda k: (corpus[k].file, corpus[k].start, k))
    result = []
    for number, key in enumerate(ordered, 1):
        chunk = corpus[key]
        item = block(chunk.file, chunk.content.splitlines())
        item.update(id=f'CTX-{number:02}', start_line=chunk.start, end_line=chunk.end)
        result.append(item)
    return result


def _pack_order(corpus, order, budget):
    selected, skipped = [], []
    for key in order:
        if token_count(render_blocks(canonical_blocks(corpus, selected + [key]))) <= budget:
            selected.append(key)
        else:
            skipped.append(key)
    return selected, skipped


def question_scores(corpus, candidates, questions):
    index = BM25Index(Document(k, 0, 0, corpus[k].content) for k in candidates)
    features = {k: operation_features(corpus[k].content) for k in candidates}
    matrices = []
    for question in questions:
        # Both alternative outcomes enter symmetrically, never as labels.
        query = ' '.join([question['question'], *question['symbols'],
                          question['support_if'], question['refute_if']])
        scores = {doc.file: score for score, doc in index.search(query, top_k=len(candidates))}
        for key in scores:
            if question['operation'] in features[key]:
                scores[key] *= 1.25
        largest = max(scores.values(), default=1)
        matrices.append({key: score / largest for key, score in scores.items()})
    return matrices


def _cover_questions(corpus, candidates, matrices, budget):
    remaining, selected, skipped, schedule = set(candidates), [], [], []
    covered = [0.0] * len(matrices)
    current_tokens = 0
    while remaining:
        choices = []
        for key in sorted(remaining):
            blocks = canonical_blocks(corpus, selected + [key])
            total = token_count(render_blocks(blocks))
            if total > budget:
                skipped.append(key)
                continue
            gain = sum(max(0, matrix.get(key, 0) - seen)
                       for matrix, seen in zip(matrices, covered))
            if gain > 0:
                choices.append((-gain / max(1, total - current_tokens),
                                corpus[key].file, corpus[key].start, key, total, gain))
        remaining.difference_update(skipped)
        if not choices:
            break
        _, _, _, key, current_tokens, gain = min(choices)
        selected.append(key)
        remaining.remove(key)
        covered = [max(seen, matrix.get(key, 0)) for matrix, seen in zip(matrices, covered)]
        schedule.append({'key': key, 'marginal_proxy_gain': gain,
                         'total_source_tokens': current_tokens})
    return selected, sorted(set(skipped)), schedule, covered


def select(base, head, diff, proposal, arm, budget=800):
    if arm not in ARMS or type(budget) is not int or budget < 0:
        raise ValueError('Invalid arm/budget')
    _, paired = retrieve(base, head, diff, 'scip_delta', budget)
    candidates = paired['ranked_keys']
    questions = [copy.deepcopy(q) for h in proposal['hypotheses'] for q in h['questions']]
    schedule, covered, skipped = [], [], []
    if arm == 'diff_recheck':
        keys = []
    elif arm == 'reserve':
        _, audit = retrieve_selector(base, head, diff, 'selector_reserve', budget, min(400, budget))
        keys, skipped = _pack_order(head.chunks, audit['selected_keys'], budget)
    elif arm == 'claim_search':
        index = BM25Index(Document(k, 0, 0, head.chunks[k].content) for k in candidates)
        query = ' '.join(h['claim'] + ' ' + h['failure_scenario'] for h in proposal['hypotheses'])
        ranked = [doc.file for _, doc in index.search(query, top_k=len(candidates))]
        ranked += [k for k in candidates if k not in set(ranked)]
        keys, skipped = _pack_order(head.chunks, ranked, budget)
    elif questions:
        matrices = question_scores(head.chunks, candidates, questions)
        keys, skipped, schedule, covered = _cover_questions(head.chunks, candidates, matrices, budget)
        # If marginal query coverage is exhausted, fill using common paired order.
        for key in candidates:
            if key not in keys and token_count(render_blocks(canonical_blocks(head.chunks, keys + [key]))) <= budget:
                keys.append(key)
    else:
        keys, skipped = _pack_order(head.chunks, candidates, budget)
    blocks = canonical_blocks(head.chunks, keys)
    return blocks, {'arm': arm, 'candidate_keys': candidates, 'selection_order': keys,
                    'source_tokens': token_count(render_blocks(blocks)), 'budget': budget,
                    'questions': questions, 'greedy_schedule': schedule,
                    'proxy_coverage': covered, 'budget_skipped_keys': skipped,
                    'source_order': 'file/start/key', 'truth_or_sufficiency_estimated': False}
