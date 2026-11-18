"""Admit a preselected route bundle as whole AST units or leave it unresolved."""
from ace_review.context_adaptive import EvidenceState


def complete_bundle(corpus, initial_lines, paths, route_ids, protected_ids, cap=1600):
    route_ids = sorted(set(route_ids))
    protected_ids = set(protected_ids)
    state = EvidenceState(corpus)
    state.paths = dict(paths)
    state.lines = {k: set(initial_lines[k]) for k in protected_ids}
    for key in route_ids:
        unit = corpus.units[key]
        state.lines[key] = set(range(unit.start, unit.end + 1))
    mandatory_tokens = state.tokens
    if mandatory_tokens > cap:
        return None, {
            'admitted': False, 'reason': 'Complete route bundle exceeds source cap',
            'mandatory_tokens': mandatory_tokens, 'cap': cap,
            'route_ids': route_ids, 'omitted_connection_is_unknown': True,
        }
    retained = []
    evicted = []
    for key in sorted(set(initial_lines) - set(state.lines)):
        state.lines[key] = set(initial_lines[key])
        if state.tokens > cap:
            state.lines.pop(key)
            evicted.append(key)
        else:
            retained.append(key)
    assert state.tokens <= cap
    assert all(state.lines[k] == set(range(corpus.units[k].start, corpus.units[k].end + 1)) for k in route_ids)
    assert all(set(initial_lines[k]).issubset(state.lines[k]) for k in protected_ids)
    return state, {
        'admitted': True, 'mandatory_tokens': mandatory_tokens, 'cap': cap,
        'route_ids': route_ids, 'protected_ids': sorted(protected_ids),
        'retained_other_ids': retained, 'evicted_ids': evicted,
        'rule': 'Complete preselected route bundle first; deterministic retention of other source when it fits. No per-unit truncation. No autonomous route discovery or runtime binding proof.',
    }
