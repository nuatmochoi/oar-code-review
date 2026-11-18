"""Review the same frozen source through two rounds of evidence questions."""
import json
import re
from collections import defaultdict

from experiment import (ROOT, PREV, SCOPE, CANDIDATE, STR, arr, enum, obj,
                        split_diff, tokens, dumps, load, write, request, api_call,
                        check_candidates)
from merge import merge_request, select
from retrieval import READ_CAP, ROLE_WORDS, lookup_bundles, compact_receipts

PHASE_OUTPUT = 4096
TOKEN_CAP = 200000
QUESTION_LIMIT = 10
SCOPES = ('contract', 'error_recovery', 'state_lifecycle', 'compatibility',
          'configuration', 'tests', 'other')
ROLE = obj(role=enum(*ROLE_WORDS),need=STR,hints=arr(STR,maxItems=3))
QUESTION = obj(perspective=enum(*SCOPES), roles=arr(ROLE,minItems=1,maxItems=4), question=STR)
QUESTIONS = arr(QUESTION,maxItems=QUESTION_LIMIT)
QUESTION_REVIEW = obj(id=STR,status=enum('answered','needs_evidence','not_applicable'),
                      missing_roles=arr(enum(*ROLE_WORDS),maxItems=4),reason=STR)
CANDIDATE_WITH_QUESTION = obj(**CANDIDATE['properties'], question_ids=arr(STR,maxItems=3))
FIRST = obj(questions=QUESTIONS)
SECOND = obj(question_review=arr(QUESTION_REVIEW,maxItems=QUESTION_LIMIT),
             candidates=arr(CANDIDATE_WITH_QUESTION,maxItems=8), next_questions=QUESTIONS)
LAST = obj(question_review=arr(QUESTION_REVIEW,maxItems=QUESTION_LIMIT),
           verdicts=arr(obj(id=STR,state=enum('supported','revised','refuted','unresolved','duplicate'),reason=STR,
                            question_ids=arr(STR,maxItems=3)),maxItems=8),
           revisions=arr(obj(id=STR,candidate=CANDIDATE_WITH_QUESTION),maxItems=8),
           candidates=arr(CANDIDATE_WITH_QUESTION,maxItems=8))


def source_index(packet):
    """Read exact hunks and numbered source blocks from the shared immutable packet."""
    prefix, raw = packet['public_source'].split('\n=== PUBLIC DIFF ===\n',1)
    diff, source = raw.rsplit('\n=== PUBLIC SOURCE EXCERPTS ===\n',1)
    entries = {}
    for file in split_diff(diff):
        path = file['head'] if file['head'] not in (None,'/dev/null') else file['base']
        for hunk in file['hunks']:
            identifier = f'H{len(entries)+1:03d}'
            head = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))?',hunk)
            assert head is not None
            start, count = int(head[1]), int(head[2] or 1)
            entries[identifier] = dict(path=path,kind='hunk',label=hunk.splitlines()[0],
                                       text=file['header']+hunk,head_start=start,head_count=count)
    for index, match in enumerate(re.finditer(r'(?m)^<<< (HEAD|BASE) (".*") lines (\d+)-(\d+) >>>$',source)):
        identifier = f'B{index+1:03d}'
        end = source.find('\n\n<<< ', match.end())
        text = source[match.start():end if end >= 0 else len(source)]
        entries[identifier] = dict(path=json.loads(match[2]),kind='body',
                                   label=f'{match[1]} lines {match[3]}–{match[4]}',text=text)
    assert sum(e['kind']=='hunk' for e in entries.values()) == packet['coverage']['supplied_hunks']
    assert sum(e['kind']=='body' for e in entries.values()) == packet['coverage']['supplied_bodies']
    return prefix, entries


def preview(entries):
    snippets, included = [], []
    used, paths = 0, set()
    for identifier, entry in entries.items():
        if entry['kind']!='hunk' or entry['path'] in paths:
            continue
        block = f'{identifier}\n{entry["text"]}'
        cost = tokens(block)+2
        if used+cost > 5500:
            continue
        paths.add(entry['path'])
        used += cost
        snippets.append(block)
        included.append(identifier)
    return '\n\n'.join(snippets), included


LOOKUP_ID = re.compile(r'(?<![\w./\\-])[HB][0-9]{3,}(?![\w./\\-])')


def resolve_targets(values, entries):
    """Recognize explicit inventory IDs, including IDs accompanied by path text."""
    resolved = {}
    for value in values:
        if value in entries:
            resolved[value] = True
            continue
        matches = LOOKUP_ID.findall(value)
        for identifier in matches or [value]:
            resolved[identifier] = identifier in entries
    return resolved


def lookup(questions, entries, round_number, already_visible=()):
    """Read each in-scope target once and account for partial answers per question."""
    remaining, delivered = READ_CAP, set()
    receipts, blocks = [], []
    already_visible = set(already_visible)
    for index, question in enumerate(questions,1):
        target_results = []
        for target, valid in resolve_targets(question['targets'],entries).items():
            if not valid:
                outcome = 'unknown_target'
            elif target in already_visible:
                outcome = 'already_visible'
            elif target in delivered:
                outcome = 'shared_read'
            else:
                block = f'<<< Lookup {target} >>>\n{entries[target]["text"]}'
                price = tokens(block)+2
                if price > remaining:
                    outcome = 'budget_deferred'
                else:
                    outcome = 'delivered'
                    remaining -= price
                    blocks.append(block)
                    delivered.add(target)
            target_results.append(dict(target=target,result=outcome))
        receipts.append(dict(id=f'Q{round_number}.{index}',**question,target_results=target_results))
    return dict(receipts=receipts,source='\n\n'.join(blocks),targets=sorted(delivered),
                reference_tokens=READ_CAP-remaining)


def summarize_questions(output, receipts):
    by_id = {}
    for disposition in output['question_review']:
        by_id.setdefault(disposition['id'],[]).append(disposition)
    ledger = []
    for receipt in receipts:
        values = by_id.get(receipt['id'],[])
        status = values[0]['status'] if len(values)==1 else 'unreviewed'
        if any(r['result'] in ('no_candidate','budget_deferred') for r in receipt['roles']):
            status = 'needs_evidence'
        missing = values[0]['missing_roles'] if len(values)==1 else []
        if status=='answered' and missing:
            status = 'needs_evidence'
        ledger.append(dict(**receipt,review_status=status,
                           missing_roles=missing,review_reason=values[0]['reason'] if len(values)==1 else ''))
    return ledger


ROLE_INSTRUCTIONS = (
    'For each question specify only the evidence roles needed to decide it, up to four. '
    'Use change, caller, implementation, failure, configuration, test, counterevidence or other. '
    'For each role write a precise need with the relevant symbol or condition and give up to three '
    'exact inventory IDs as hints if known. Hints are optional; the retriever ranks the PR source. '
    'For a cross-file claim distinguish the caller from the implementation. Add a failure branch or '
    'counterevidence role if it could change the conclusion. Do not manufacture a role for a simple claim.')


def review_packet(packet, folder, encoder, vectors):
    """Execute plan → read → update → read → validate, using three model calls."""
    prefix, entries = source_index(packet)
    initial, sampled = preview(entries)
    index = packet['index']
    inventory = [[identifier, entry['path'], entry['label']] for identifier,entry in entries.items()]
    shell = prefix+'\nLookup inventory [ID, file, range]:\n'+dumps(inventory)
    visible = shell+'\nInitial exact diff examples:\n'+initial
    plan_task = ('Plan a source review. Ask up to ten concise, falsifiable questions over distinct '
                 'changed areas and applicable perspectives. '+ROLE_INSTRUCTIONS+' '
                 'Prefer concrete contract, failure, lifecycle, configuration or test questions prompted '
                 'by this PR. Ask to read an unfamiliar changed hunk when its role is uncertain. '
                 'Order by priority, with breadth across files. A question need not presume a defect.')
    planned, usage1 = api_call(request(SCOPE,visible,plan_task,FIRST,PHASE_OUTPUT,f'ADAPT/{index:02d}/plan'),folder/'plan')
    first = lookup_bundles(planned['questions'],entries,1,encoder,vectors,resolve_targets)
    seen = set(sampled)|set(first['targets'])
    untouched = [identifier for identifier,e in entries.items() if e['kind']=='hunk' and identifier not in seen]
    next_input = (visible+'\nFirst lookups:\n'+dumps(compact_receipts(first['receipts']))+'\n'+first['source']+
                  '\nChanged hunk IDs whose exact diff has not been supplied in the sample or lookups:\n'+dumps(untouched))
    next_task = ('Judge each Q1 question using the source. Mark answered only when each necessary role '
                 'is satisfied and the cited code resolves the question. A similar-looking block can '
                 'be unrelated. Record missing_roles and the reason if evidence is insufficient. '
                 'Report up to eight concrete new candidates with source citations, feasible trigger, '
                 'failure mechanism and question IDs. Then ask up to ten fresh questions. '
                 'Reserve room for uninspected changed hunks in the supplied list, in addition to '
                 'missing facts that decide whether a candidate holds. '+ROLE_INSTRUCTIONS+' '
                 'Prefer new evidence; ask for a comparison or disconfirming guard if it could change the result.')
    revised, usage2 = api_call(request(SCOPE,next_input,next_task,SECOND,PHASE_OUTPUT,f'ADAPT/{index:02d}/update'),folder/'update')
    second = lookup_bundles(revised['next_questions'],entries,2,encoder,vectors,resolve_targets,already_visible=seen)
    first_candidates = [dict(id=f'P1-{i}',**candidate) for i,candidate in enumerate(revised['candidates'],1)]
    final_task = ('Review Q2 and record missing_roles if retrieved source does not answer it. '
                  'Give one verdict per P1 candidate and list the question IDs that determined it. '
                  'Mark supported when the original holds, revised when the evidence requires a corrected '
                  'mechanism or narrower trigger, refuted when code disproves it, unresolved when decisive '
                  'evidence is missing, or duplicate. For each revised verdict return exactly one corrected '
                  'candidate in revisions with the same P1 ID and actual source citations. Report up to '
                  'eight additional concrete defects, using the second lookup or other supplied changed '
                  'areas and citing actual ranges, feasible trigger and consequence. Use question_ids '
                  'for the specific questions that led to the new candidate. Do not duplicate P1.')
    final_source = (packet['public_source']+'\nFirst-round question dispositions:\n'+
                    dumps(compact_receipts(summarize_questions(revised,first['receipts'])))+'\n'+first['source']+
                    '\nPreviously shown sample IDs (source in the common packet):\n'+dumps(sampled)+
                    '\nSecond-round lookup receipts:\n'+dumps(compact_receipts(second['receipts']))+'\n'+second['source']+
                    '\nEarlier candidates (data):\n'+dumps(first_candidates))
    final, usage3 = api_call(request(SCOPE,final_source,final_task,LAST,PHASE_OUTPUT,f'ADAPT/{index:02d}/verify'),folder/'verify')
    usages = [usage1,usage2,usage3]
    actual = sum(u['input_tokens']+u['output_tokens'] for u in usages)
    assert actual <= TOKEN_CAP
    indexed = {c['id']:c for c in first_candidates}
    verdicts = defaultdict(list)
    for verdict in final['verdicts']:
        verdicts[verdict['id']].append(verdict['state'])
    kept_ids = {cid for cid in indexed if verdicts[cid]==['supported']}
    revisions = defaultdict(list)
    for item in final['revisions']:
        revisions[item['id']].append(item['candidate'])
    accepted_revisions = {cid:items[0] for cid,items in revisions.items()
                          if cid in indexed and verdicts[cid]==['revised'] and len(items)==1}
    kept_ids.update(accepted_revisions)
    # Keep P1/P2 identities stable even when a preceding candidate is rejected.
    pairs = [[dict(accepted_revisions.get(f'P1-{i}',c)) for i,c in enumerate(revised['candidates'],1)],
             [dict(c) for c in final['candidates']]]
    links = {f'P{phase}-{i}':c.pop('question_ids') for phase,items in enumerate(pairs,1)
             for i,c in enumerate(items,1)}
    eligible, invalid = check_candidates(packet,[dict(candidates=items) for items in pairs])
    approved = [c for c in eligible if c['id'] in kept_ids or c['id'].startswith('P2-')]
    for verdict in final['verdicts']:
        cid = verdict['id']
        if cid in links:
            links[cid] = list(dict.fromkeys(links[cid]+verdict['question_ids']))
    first_trace = summarize_questions(revised,first['receipts'])
    second_trace = summarize_questions(final,second['receipts'])
    fresh_hunks = sorted(set(untouched)&set(second['targets']))
    record = dict(sampled_hunks=sampled,question_round_1=first_trace,question_round_2=second_trace,
                  lookup1_hunks=sorted(i for i in first['targets'] if entries[i]['kind']=='hunk'),
                  lookup2_hunks=sorted(i for i in second['targets'] if entries[i]['kind']=='hunk'),
                  newly_read_hunks=fresh_hunks,candidate_question_links=links,
                  candidate_ledger=approved,mechanically_ineligible=invalid,prior_candidate_verdicts=final['verdicts'],
                  initial_candidates=first_candidates,accepted_revisions=accepted_revisions,
                  lookup_source_tokens=first['reference_tokens']+second['reference_tokens'],
                  actual_tokens=actual,usage=usages)
    return record,entries


def combine(packet, base, folder, approved, cap):
    existing = base['findings']
    if approved and cap:
        result, usage = api_call(merge_request(packet,existing,approved),folder/'merge')
        chosen, withheld, unranked = select(result,approved,cap)
    else:
        result, usage, chosen, withheld, unranked = None, None, [], [], []
    by_id = {c['id']:c for c in approved}
    added = []
    for identifier in chosen:
        finding = by_id[identifier]
        anchor = finding['anchor']
        added.append(dict(file=anchor['path'],start_line=anchor['line'],end_line=anchor['line'],
                          message=f'{finding["title"]}\n\n{finding["mechanism"]}\n\nTrigger: {finding["trigger"]}\nImpact: {finding["impact"]}',
                          producer='independent-review-additive'))
    path = ROOT/'arms/ADD/findings'/f'{packet["key"]}.json'
    payload = dict(base,findings=existing+added)
    if path.exists(): assert load(path)==payload
    else: write(path,payload)
    return dict(baseline_reports_preserved=len(existing),added=chosen,withheld_by_cap=withheld,
                unranked_positive_tail=unranked,merge_usage=usage,merge_assessment=result)
