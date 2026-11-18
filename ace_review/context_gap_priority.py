"""Exploratory S policy repair: one lexical slot before one graph path.

No labels, witnesses or manually named target endpoints enter this policy.
The initial packet is unchanged; only additional retrieval rounds are modified.
"""
import copy

from .context_adaptive import digest


def bound_controller_schema(schema, state, questions):
    """Constrain citations to IDs already visible, without adding source facts."""
    result = copy.deepcopy(schema)
    fields = result['properties']['questions']['items']['properties']
    anchors = sorted(set(state.lines) | {'DIFF'})
    fields['question_id']['enum'] = [q['id'] for q in questions]
    fields['evidence_ids']['items']['enum'] = anchors
    fields['gaps']['items']['properties']['anchor_ids']['items']['enum'] = anchors
    expansion = fields['expansion_requests']
    expansion['items']['properties']['anchor_ids']['items']['enum'] = anchors
    paths = sorted(set(state.paths) | set(state.lines))
    if paths:
        expansion['items']['properties']['path_id']['enum'] = paths
    else:
        expansion['maxItems'] = 0
    return result


def priority_step(corpus, state, questions, diff_query, controller, cap):
    updates = {q['question_id']: q for q in controller['questions']}
    audit = []
    for q in questions:
        update = updates.get(q['id'])
        if update and update['gaps']:
            query = ' '.join(g['retrieval_query'] + ' ' + g['needed_fact'] for g in update['gaps'])
        else:
            query = q['question'] + ' ' + ' '.join(q['symbols']) + ' ' + diff_query
        scores = corpus.scores(query)
        chosen = []
        for key in sorted(scores, key=lambda k: (-scores[k], k))[:64]:
            if corpus.units[key].positions('L1') <= state.lines.get(key, set()):
                continue
            if state.add([key], 'L1', cap):
                chosen.append({'slot': 'lexical', 'unit': key})
                break
        audit.append({'question_id': q['id'], 'query': query, 'selected': chosen})
    # Reserve every question's lexical acquisition before graph fill can spend
    # the remaining budget. Otherwise early questions can starve later gaps.
    for record in audit:
        order, paths, ranking = corpus.order(record['query'], False)
        for key in order:
            path = paths[key]
            if all(corpus.units[k].positions('L1') <= state.lines.get(k, set()) for k in path):
                continue
            if state.add(path, 'L1', cap):
                record['selected'].append({'slot': 'graph', 'endpoint': key, 'path': path})
                break
        record['candidate_count'] = ranking['candidate_count']
    assert state.tokens <= cap
    return {'cap': cap, 'state': state.audit(), 'queries': audit, 'packet_sha256': digest(state.render())}
