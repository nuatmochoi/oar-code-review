"""Review only new findings grounded in the frozen follow-up sources."""

from adaptive import source_index, write_once
from breadth import evidence_packet, expanded_source, public_entries
from experiment import (CANDIDATE, ROOT, SCOPE, _diff_rows, api_call, arr, check_candidates,
                        dumps, enum, load, obj, request, tokens, write)
from merge import merge_request, select


CONTROL = ROOT.parent/'reviewbench_adaptive_multi_id25_20261113'
RETRIEVAL = ROOT.parent/'reviewbench_unresolved_semantic25_20261113'
PREVIOUS = ROOT.parent/'reviewbench_fixed_reports25_20261113'
FIXED = CONTROL/'arms/ADD'


def historical(packet):
    return FIXED/'review'/f'{packet["index"]:02d}'


def fallback(arm, packet):
    return load(RETRIEVAL/'arms'/arm/'review'/f'{packet["index"]:02d}'/'questions.json')['fallback']


def raw_rows(entry, snapshot):
    if entry['kind']=='hunk':
        return _diff_rows(entry['text'],snapshot,entry['path'])
    if not entry['label'].startswith(snapshot+' '):
        return {}
    rows = {}
    for line in entry['text'].split('\n')[1:]:
        number, separator, source = line.partition(': ')
        if separator and number.isdecimal():
            rows[int(number)] = source
    return rows


def proposal_request(packet, existing, retrieval, improve=False):
    supplied = [q for q in retrieval['receipts'] if q['selected']]
    public_receipts = [{k:q[k] for k in ('id','question','previous_gap','selected')} for q in supplied]
    schema = obj(candidates=arr(obj(**CANDIDATE['properties'],
                  followup_ids=arr(enum(*(q['id'] for q in supplied)),minItems=1,maxItems=3)),maxItems=8))
    task = ('The baseline findings are already accepted and their text, order, and anchors are locked. '
            'Only propose an independent functional defect if a listed follow-up source supplies '
            'decisive evidence for its exact trigger or failure mechanism. Compare the same cause and '
            'required fix against every baseline finding. Omit duplicates and broader or narrower restatements '
            'of the same cause. Do not audit or re-report the baseline findings. Cite a short exact range '
            'from an actual follow-up block, along with any context needed to establish the failure. '
            'Use followup_ids to identify the question linked to that cited block. The supplied searches '
            'can answer a question without exposing a defect. Return an empty array in that case.\n'
            'Locked baseline findings (data):\n'+dumps(existing))
    if improve:
        task += ('\nFor every supplied changed area, first compare the old behavior with the new behavior. '
                 'Check concrete input cases, changed state or API contracts, and whether changed tests '
                 'still assert the behavior they describe. For each plausible regression, trace a specific '
                 'input or state through the shown operations to the wrong outcome. Identify the exact '
                 'retrieved line that makes the cause or trigger verifiable. Check the nearest guard or '
                 'alternative explanation and compare against the locked reports by cause. '
                 'Use all shown evidence together when the trigger and failure are in different blocks. '
                 'Keep these checks concise and return only the existing JSON fields. '
                 'A missing fact alone does not support a candidate.')
    # Scope constrains all model actions to offline defensive source review.
    scope = SCOPE+' Exclude security exploit development, exploit reproduction, and offensive testing.'
    prefix, entries = source_index(packet)
    trace = load(historical(packet)/'questions.json')
    initial = trace['question_round_1']
    targeted = {q['parent_id'] for q in supplied}
    relevant_ids = dict.fromkeys(r['target'] for q in initial if q['id'] in targeted
                                for r in q['target_results'] if r['result'] in ('delivered','shared_read','already_visible'))
    remaining = 6500 - retrieval['tokens']
    direct = []
    for identifier in relevant_ids:
        block = f'<<< Original lookup {identifier} >>>\n{entries[identifier]["text"]}'
        price = tokens(block)+2
        if price<=remaining:
            direct.append(block)
            remaining -= price
    assert tokens('\n\n'.join([*direct,retrieval['source']]))<=6500
    public = (prefix+'\nOriginal requested blocks for these questions (data):\n'+'\n\n'.join(direct)+
              '\nFollow-up questions and retrieved block IDs (data):\n'+
              dumps(public_receipts)+'\n'+retrieval['source'])
    return request(scope,public,task,schema,4096,f'APPEND/{packet["index"]:02d}/propose')


def validate_proposals(packet, drafts, retrieval, existing, validated_packet=None):
    """Check every cited location and the physical link to its follow-up receipt."""
    candidates = [{k:v for k,v in c.items() if k!='followup_ids'} for c in drafts['candidates']]
    eligible, invalid = check_candidates(validated_packet or packet,[dict(candidates=candidates)])
    entries = public_entries(packet) if any(i.startswith('X') for i in retrieval['targets']) else source_index(packet)[1]
    receipts = {q['id']:q for q in retrieval['receipts'] if q['selected']}
    reports = {report_identity(row) for row in existing}
    keep, binding = [], {}
    for candidate in eligible:
        cid = candidate['id']
        ids = drafts['candidates'][int(cid.split('-')[1])-1]['followup_ids']
        hits = []
        for qid in dict.fromkeys(ids):
            for source_id in receipts[qid]['selected']:
                source = entries[source_id]
                for span in candidate['evidence']:
                    if span['path'] != source['path']:
                        continue
                    lines = raw_rows(source,span['snapshot'])
                    if (all(n in lines for n in range(span['start'],span['end']+1))
                        and any(lines[n].strip() for n in range(span['start'],span['end']+1))):
                        hits.append(dict(question_id=qid,source_id=source_id,span=span))
        if not hits or report_identity(render(candidate)) in reports:
            reason = 'no_fully_contained_followup_citation' if not hits else 'exact_baseline_duplicate'
            invalid.append(dict(candidate=candidate,reasons=[reason]))
            continue
        binding[cid] = hits
        keep.append(candidate)
    return keep, invalid, binding


def render(candidate):
    anchor = candidate['anchor']
    return dict(file=anchor['path'],start_line=anchor['line'],end_line=anchor['line'],
                message=f'{candidate["title"]}\n\n{candidate["mechanism"]}\n\n'
                        f'Trigger: {candidate["trigger"]}\nImpact: {candidate["impact"]}',
                producer='followup-review-additive')


def report_identity(report):
    return tuple(report[k] for k in ('file','start_line','end_line','message'))


def one(arm, packet):
    folder = ROOT/'arms'/arm/'review'/f'{packet["index"]:02d}'
    done = folder/'complete.json'
    base = load(FIXED/'findings'/f'{packet["key"]}.json')
    assert base['pr']==packet['pr']
    existing = base['findings']
    target = ROOT/'arms'/arm/'findings'/f'{packet["key"]}.json'
    if done.exists():
        assert load(target)['findings'][:len(existing)]==existing
        return load(done)
    capped = len(existing)>=6
    if arm=='CAP' and not capped:
        old = PREVIOUS/'arms/SEMANTIC'
        old_output = load(old/'findings'/f'{packet["key"]}.json')
        assert old_output['findings'][:len(existing)]==existing
        record = load(old/'review'/folder.name/'complete.json')
        write_once(target,old_output)
        write(done,dict(record,reused=True))
        return record
    retrieval = fallback('SEMANTIC',packet) if arm=='CAP' else expanded_source(packet)
    cap = min(3,max(0,(9 if arm=='CAP' else 6)-len(existing)))
    validated_packet = evidence_packet(packet,retrieval) if arm!='CAP' else packet
    verdict, usages, retained = None, [], []
    kept, invalid, bound, chosen, withheld = [], [], {}, [], []
    if cap and retrieval['targets']:
        body = proposal_request(packet,existing,retrieval,improve=arm=='CANDIDATES')
        drafts, usage = api_call(body,folder/'verify')
        usages.append(usage)
        kept, invalid, bound = validate_proposals(packet,drafts,retrieval,existing,validated_packet)
        if kept:
            body = merge_request(validated_packet,existing,kept)
            body['input'][1]['content'] += (
                '\nCheck whether each appended report has a concrete failure and the linked retrieved range '
                'materially establishes its trigger or mechanism. Distinguish new causes from existing reports. '
                'Use insufficient_evidence when a trigger or dependency is unverified. Scope is defensive review. '
                'Exclude exploit development, exploitation testing, and reproduction instructions. '
                'Verified source range bindings (data):\n'+dumps(bound))
            verdict, usage = api_call(body,folder/'merge')
            usages.append(usage)
            chosen, withheld, _ = select(verdict,kept,cap)
            indexed = {row['id']:row for row in kept}
            seen = {report_identity(row) for row in existing}
            for cid in chosen:
                report = render(indexed[cid])
                if report_identity(report) not in seen:
                    retained.append(report)
                    seen.add(report_identity(report))
    assert sum(u['input_tokens']+u['output_tokens'] for u in usages)<=200_000
    write_once(target,dict(base,findings=existing+retained))
    record = dict(index=packet['index'],baseline_reports=len(existing),eligible=len(kept),
                  delivered_questions=sum(bool(q['selected']) for q in retrieval['receipts']),
                  retrieval_tokens=retrieval['tokens'],cap=cap,added=retained,selected=chosen,
                  withheld=withheld,rejected=invalid,bindings=bound,merge_assessment=verdict,
                  usage=usages,skip='report_cap' if not cap else 'no_followup_source' if not retrieval['targets'] else None)
    write(done,record)
    return record
