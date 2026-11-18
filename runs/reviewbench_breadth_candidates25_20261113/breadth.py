"""Expand a fixed source budget across changed files without using evaluator labels."""

from collections import defaultdict, deque
import re

from adaptive import source_index
from experiment import PREV, ROOT, _diff_rows, load, split_diff, tokens


READ_LIMIT = 6500


def public_entries(packet):
    _, entries = source_index(packet)
    archived = load(PREV/'public'/f'{packet["index"]:02d}.json')
    assert archived['key']==packet['key']
    assert all(archived['pr'][field]==packet['pr'][field] for field in ('base','head','repo','pr_number'))
    known = {entry['text'] for entry in entries.values()}
    for file in split_diff(archived['case']['diff']):
        path = file['head'] if file['head'] not in (None,'/dev/null') else file['base']
        if not path or path=='/dev/null':
            continue
        for hunk in file['hunks']:
            block = file['header']+hunk
            if block in known:
                continue
            identifier = f'X{len(entries)+1:04d}'
            entries[identifier] = dict(path=path,kind='hunk',label=hunk.splitlines()[0],text=block)
            known.add(block)
    return entries


def evidence_packet(packet, retrieval):
    """Extend validated rows and merge input only with full selected diff hunks."""
    extra = [i for i in retrieval['targets'] if i.startswith('X')]
    if not extra:
        return packet
    entries = public_entries(packet)
    rows = {side:{path:set(nums) for path,nums in paths.items()} for side,paths in packet['rows'].items()}
    blocks = []
    for identifier in extra:
        entry = entries[identifier]
        blocks.append(f'<<< Lookup {identifier} >>>\n'+entry['text'])
        for side in ('BASE','HEAD'):
            rows[side].setdefault(entry['path'],set()).update(_diff_rows(entry['text'],side,entry['path']))
    return dict(packet, rows=rows, public_source=packet['public_source']+'\nAdditional exact diff blocks:\n'+'\n\n'.join(blocks))


def expand(packet, prior, old_trace, existing):
    entries = public_entries(packet)
    initial = set(old_trace['sampled_hunks'])
    read = {r['target'] for q in old_trace['question_round_1']+old_trace['question_round_2']
            for r in q['target_results'] if r['result'] in ('delivered','shared_read','already_visible')}
    protected = {row['file'] for row in existing}
    blocks, receipts, admitted = [], [], set()
    remaining = READ_LIMIT

    def add(identifier):
        nonlocal remaining
        if identifier in admitted:
            return True
        text = f'<<< Lookup {identifier} >>>\n{entries[identifier]["text"]}'
        price = tokens(text)+2
        if price>remaining:
            return False
        admitted.add(identifier)
        blocks.append(text)
        remaining -= price
        return True

    # Preserve the previously selected material, in original delivery order.
    for identifier in re.findall(r'^<<< Lookup ([HB]\d+) >>>$',prior['source'],re.M):
        assert add(identifier)
    receipts.extend({k:v for k,v in receipt.items() if k in ('id','parent_id','question','previous_gap','selected')}
                    for receipt in prior['receipts'])
    # Cover changes outside the fallback, prioritizing files that received less review.
    # Earlier lookups were not all delivered to the additive reviewer.
    unseen = {i:e for i,e in entries.items() if e['kind']=='hunk' and i not in admitted}
    prior_paths = {entries[i]['path'] for i in admitted|initial|read if i in entries}
    grouped = defaultdict(deque)
    for identifier, entry in sorted(unseen.items(),key=lambda item:(item[0] in initial|read,item[0])):
        grouped[entry['path']].append(identifier)
    paths = sorted(grouped,key=lambda path:(path in protected,path in prior_paths,path))
    serial = 0
    while any(grouped.values()):
        for path in paths:
            if not grouped[path]:
                continue
            identifier = grouped[path].popleft()
            if not add(identifier):
                continue
            serial += 1
            receipts.append(dict(id=f'XQ.{serial}',parent_id='',
                question='Does this changed block create a functional regression in a contract, a boundary case, a state transition, or a test assertion?',
                previous_gap='Changed hunk outside the previous follow-up selection.',selected=[identifier]))
    source = '\n\n'.join(blocks)
    assert tokens(source)<=READ_LIMIT-remaining
    assert set(prior['targets'])<=admitted
    return dict(receipts=receipts,targets=sorted(admitted),source=source,tokens=READ_LIMIT-remaining,
                extra_blocks=serial,outside_packet=sum(i.startswith('X') for i in admitted))


def expanded_source(packet):
    return load(ROOT/'retrieval'/f'{packet["index"]:02d}.json')
