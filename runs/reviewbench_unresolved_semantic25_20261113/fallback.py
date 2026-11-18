"""Use the unused second-round budget for already unresolved first-round questions."""

from collections import defaultdict
from itertools import combinations

from adaptive import READ_CAP, resolve_targets
from encoder import document, terms
from experiment import tokens


SHORTLIST = 12
PER_QUESTION = 2
AVAILABLE = {'delivered','shared_read','already_visible'}


def route_score(routes, selected, anchors):
    selected = set(selected)
    available = selected | anchors
    best = 0, None
    for route in routes:
        caller = set(route['caller_ids'])
        implementation = set(route['implementation_ids'])
        failure = {i for branch in route['failure_branches'] for i in branch['ids']}
        if not (caller & available and implementation & available):
            continue
        if not (selected & (caller|implementation|failure)):
            continue
        # One whole source block may contain both a caller and its implementation.
        # It still must carry the actual AST/LSP source coordinates.
        bonus = .16 + .12*bool(failure & available)
        if bonus > best[0]:
            best = bonus, route
    return best


def collect(first_trace, second, entries, already_visible, model, vectors, graph, arm):
    questions = [q for q in first_trace if q['review_status']=='needs_evidence']
    if not questions:
        return dict(receipts=[],source='',tokens=0,targets=[],remaining=READ_CAP-second['reference_tokens'])
    identifiers = list(entries)
    texts = {i:f'<<< Lookup {i} >>>\n{entries[i]["text"]}' for i in identifiers}
    costs = {i:tokens(text)+2 for i,text in texts.items()}
    words = {i:terms(document(entries[i])) for i in identifiers}
    queries = [q['question']+' '+q['review_reason'] for q in questions]
    similarities = model.encode(['Represent this sentence for searching relevant passages: '+q
                                 for q in queries]) @ vectors.T
    direct = set(already_visible)|set(second['targets'])
    remaining = READ_CAP-second['reference_tokens']
    start = remaining
    collected, sources, receipts = set(), [], []
    for question, query, similarities_row in zip(questions,queries,similarities,strict=True):
        anchored = {i for i,valid in resolve_targets(question['targets'],entries).items() if valid and i in direct}
        query_words = terms(query)
        scores = {i:.8*float(cosine)+.2*len(words[i]&query_words)/max(1,len(query_words))
                  for i,cosine in zip(identifiers,similarities_row,strict=True) if i not in direct}
        ranked = sorted(scores,key=lambda i:(-scores[i],i))[:SHORTLIST]
        best = (0,0.,0),(),None
        semantic = (0,0.,0),()
        for size in range(1,PER_QUESTION+1):
            for group in combinations(ranked,size):
                price = sum(costs[i] for i in group if i not in collected)
                if price>remaining:
                    continue
                similarity = sum(scores[i] for i in group)/size
                base = size,similarity,-price
                if base>semantic[0]:
                    semantic = base,group
                bonus, route = route_score(graph['routes'],group,anchored) if arm=='CONNECTED' else (0,None)
                rank = size,similarity+bonus,-price
                if rank>best[0]:
                    best = rank,group,route
        rank, chosen, route = best
        for identifier in chosen:
            if identifier not in collected:
                sources.append(texts[identifier])
                remaining -= costs[identifier]
                collected.add(identifier)
        receipts.append(dict(id=question['id'].replace('Q1.','F1.'),parent_id=question['id'],
                             question=question['question'],previous_gap=question['review_reason'],
                             selected=list(chosen),semantic_choice=list(semantic[1]),
                             shortlisted=[dict(id=i,score=round(scores[i],6)) for i in ranked],
                             static_route=route,status='delivered' if chosen else 'no_admissible_source'))
    source = '\n\n'.join(sources)
    joint = '\n\n'.join(s for s in (second['source'],source) if s)
    assert remaining>=0 and tokens(joint)<=READ_CAP
    return dict(receipts=receipts,source=source,tokens=start-remaining,targets=sorted(collected),remaining=remaining)


def dispositions(output, receipts):
    matches = defaultdict(list)
    for item in output['question_review']:
        matches[item['id']].append(item)
    return [dict(receipt,review_status=matches[receipt['id']][0]['status']
                 if len(matches[receipt['id']])==1 and receipt['selected'] else 'unreviewed',
                 review_reason=matches[receipt['id']][0]['reason']
                 if len(matches[receipt['id']])==1 and receipt['selected'] else '')
            for receipt in receipts]
