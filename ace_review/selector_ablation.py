"""Gold-blind 2x2 evidence-selection ablation over a paired SCIP graph.

The selector varies only diff-visible seed deduplication and an exact-two-hop
unchanged-source reservation. It never accepts labels, target symbols, or model
outputs. Source chunks and graph reachability come from ``auto_context``.
"""
from __future__ import annotations

from .auto_context import Graph, changed_query, retrieve
from .bm25 import BM25Index, Document
from .counterfactual import block
from .delta_scip import changed_ranges
from .obligation import render_blocks, token_count

ARMS = ('selector_current', 'selector_dedup', 'selector_reserve', 'selector_both')

_FACTORS = {
    'selector_current': {'dedup_seed': False, 'reserve_boundary': False},
    'selector_dedup': {'dedup_seed': True, 'reserve_boundary': False},
    'selector_reserve': {'dedup_seed': False, 'reserve_boundary': True},
    'selector_both': {'dedup_seed': True, 'reserve_boundary': True},
}


def arm_factors(arm):
    """Return a fresh factor mapping for one predeclared experimental arm."""
    if arm not in _FACTORS:
        raise ValueError('Invalid selector arm')
    return dict(_FACTORS[arm])


def _source_block(chunk, position):
    candidate = block(chunk.file, chunk.content.splitlines())
    candidate.update(id=f'CTX-{position:02}', start_line=chunk.start, end_line=chunk.end)
    return candidate


def _pack(corpus, order, budget, selected=None, selected_keys=None):
    blocks = list(selected or [])
    keys = list(selected_keys or [])
    skipped = []
    for key in order:
        candidate = _source_block(corpus[key], len(blocks) + 1)
        if token_count(render_blocks(blocks + [candidate])) <= budget:
            blocks.append(candidate)
            keys.append(key)
        else:
            skipped.append(key)
    return blocks, keys, skipped


def _paired_distances(base, head, diff):
    changes = changed_ranges(diff)
    head_seeds = head.seeds(changes['head'])
    base_seeds = base.seeds(changes['base'])
    head_distance = head.walk(head_seeds, hops=2)
    base_distance = base.walk(base_seeds, hops=2)
    paired = dict(head_distance)
    for key, distance in base_distance.items():
        if key in head.chunks:
            paired[key] = min(paired.get(key, distance), distance)
    return head_seeds, base_seeds, paired


def _boundary_order(base, head, diff, paired, all_seeds):
    candidates = [
        key for key, distance in paired.items()
        if distance == 2 and key not in all_seeds and key in base.chunks
        and base.chunks[key].content == head.chunks[key].content
    ]
    index = BM25Index(Document(key, 0, 0, chunk.content)
                      for key, chunk in head.chunks.items())
    scores = {document.file: score for score, document in
              index.search(changed_query(diff), top_k=len(head.chunks))}
    return sorted(candidates, key=lambda key: (
        -scores.get(key, 0), head.chunks[key].file, head.chunks[key].start,
        head.chunks[key].end, key)), scores


def retrieve_selector(base: Graph, head: Graph, diff: str, arm: str,
                      budget: int = 800, reserve_budget: int = 400):
    """Select source evidence under one frozen 2x2 selector condition."""
    factors = arm_factors(arm)
    if budget < 0 or reserve_budget < 0:
        raise ValueError('Budgets must be non-negative')
    if factors['reserve_boundary'] and reserve_budget > budget:
        raise ValueError('Reserved budget exceeds total budget')

    baseline_blocks, baseline_audit = retrieve(base, head, diff, 'scip_delta', budget)
    head_seeds, base_seeds, paired = _paired_distances(base, head, diff)
    base_order = list(baseline_audit['ranked_keys'])

    changes = changed_ranges(diff)
    diff_visible = set()
    for file, lines in changes['head'].items():
        for line in lines:
            diff_visible.update(head.by_line[file, line])
    excluded = diff_visible if factors['dedup_seed'] else set()
    order = [key for key in base_order if key not in excluded]
    reserve_order, bm25_scores = _boundary_order(
        base, head, diff, paired, set(head_seeds) | set(base_seeds))
    reserve_order = [key for key in reserve_order if key not in excluded]

    if not factors['dedup_seed'] and not factors['reserve_boundary']:
        selected, selected_keys = baseline_blocks, list(baseline_audit['selected_keys'])
        skipped = list(baseline_audit['budget_skipped_keys'])
        reserved, reserve_skipped = [], []
        reserved_tokens = 0
    else:
        selected, selected_keys, reserve_skipped = [], [], []
        reserved = []
        if factors['reserve_boundary']:
            selected, selected_keys, reserve_skipped = _pack(
                head.chunks, reserve_order, reserve_budget)
            reserved = list(selected_keys)
        reserved_tokens = token_count(render_blocks(selected))
        fill_order = [key for key in order if key not in set(selected_keys)]
        selected, selected_keys, skipped = _pack(
            head.chunks, fill_order, budget, selected, selected_keys)

    audit = dict(baseline_audit)
    audit.update(
        arm=arm,
        underlying_arm='scip_delta',
        factors=factors,
        budget=budget,
        reserve_budget=reserve_budget if factors['reserve_boundary'] else 0,
        ranked_keys=order,
        base_ranked_keys=base_order,
        selected_keys=selected_keys,
        budget_skipped_keys=skipped,
        seed_dedup_excluded_keys=sorted(excluded),
        diff_visible_seed_keys=sorted(diff_visible),
        reserve_candidate_keys=reserve_order,
        reserved_selected_keys=reserved,
        reserve_budget_skipped_keys=reserve_skipped,
        reserved_source_tokens=reserved_tokens,
        source_tokens=token_count(render_blocks(selected)),
        selected_spans=[dict(file=item['file'], start=item['start_line'], end=item['end_line'])
                        for item in selected],
        paired_distance={key: paired[key] for key in sorted(paired)},
        boundary_bm25_scores={key: bm25_scores.get(key, 0) for key in reserve_order},
    )
    return selected, audit
