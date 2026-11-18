"""Map archived static call bindings and parsed failure branches onto exact public rows."""

from collections import defaultdict
from pathlib import Path
import re
import sys

from experiment import ROOT, PREV, _diff_rows, load, physical_rows

sys.path.insert(0, str(ROOT/'syntax_dependencies'))
from tree_sitter_language_pack import get_parser


LANGUAGE = {'.py':'python','.go':'go','.js':'javascript','.jsx':'javascript','.mjs':'javascript',
            '.ts':'typescript','.tsx':'tsx','.java':'java','.rb':'ruby','.php':'php','.rs':'rust',
            '.c':'c','.cpp':'cpp','.cs':'csharp','.kt':'kotlin','.sh':'bash'}
FAILURE = {'raise_statement','throw_statement','catch_clause','catch_block','except_clause',
           'except_group_clause','rescue','rescue_modifier','throw_expression'}
GO_ERROR = re.compile(rb'\b(?:err|[a-zA-Z_]+Err)\s*!=\s*nil\b|\bnil\s*!=\s*(?:err|[a-zA-Z_]+Err)\b')


def block_rows(entry, snapshot):
    if entry['kind']=='hunk':
        return _diff_rows(entry['text'],snapshot,entry['path'])
    if not entry['label'].startswith(snapshot+' '):
        return {}
    rows = {}
    for line in entry['text'].split('\n')[1:]:
        matched = re.match(r'^(\d+): (.*)$',line)
        if matched:
            rows[int(matched[1])] = matched[2]
    return rows


def branches(body, parser):
    raw = body['content'].encode()
    root = parser.parse(raw).root_node
    intervals = []
    stack = [root]
    while stack:
        node = stack.pop()
        condition = node.child_by_field_name('condition') if node.type=='if_statement' else None
        is_go_error = (Path(body['path']).suffix=='.go' and condition is not None
                       and GO_ERROR.search(raw[condition.start_byte:condition.end_byte]))
        if not node.has_error and (node.type in FAILURE or is_go_error):
            start = body['start_line']+node.start_point.row
            end = body['start_line']+node.end_point.row-int(node.end_point.column==0)
            intervals.append(dict(kind=node.type,start=start,end=end))
        stack.extend(node.named_children)
    return intervals


def build(packet, entries):
    directory = PREV/'native'/f'{packet["index"]:02d}'
    receipts = []
    units = {}
    for arm in ('common','FLAT','PATH'):
        raw = load(directory/arm/'raw_queries.json')
        receipts.extend(raw if isinstance(raw,list) else raw['records'])
        units.update({item['id']:item for item in load(directory/arm/'context.json')['evidence']
                      if item['kind']=='implementation_body'})
    blocks = {side:{identifier:block_rows(entry,side) for identifier,entry in entries.items()}
              for side in ('HEAD','BASE')}
    by_path = defaultdict(list)
    for identifier, entry in entries.items():
        by_path[entry['path']].append(identifier)
    result = {}
    unresolved = defaultdict(int)
    for item in receipts:
        answer = item['answer']
        binding = answer.get('callee_binding') or {}
        if not binding.get('actual_static_callsite_bound') or binding.get('effective_operation') not in ('definition','implementation'):
            continue
        options = binding.get('LSP_targets') or []
        if len(options)!=1 or binding.get('multiple_definition_candidates'):
            unresolved['ambiguous_definition'] += 1
            continue
        target = options[0]
        anchor = binding['actual_anchor']
        side = anchor['snapshot']
        call_ids = [identifier for identifier in by_path[anchor['file']]
                    if blocks[side][identifier].get(anchor['line'],'').strip()]
        if not call_ids:
            unresolved['caller_outside_pool'] += 1
            continue
        for unit_id in binding.get('target_body_ids',[]):
            unit = units.get(unit_id)
            if (not unit or not unit.get('mechanical_checks',{}).get('complete_unit') or unit['snapshot']!=side
                or unit['path']!=target['path'] or not unit['start_line']<=target['line']<=unit['end_line']):
                unresolved['no_bound_implementation_body'] += 1
                continue
            rows, issues = physical_rows(unit)
            if issues:
                unresolved['invalid_body_rows'] += 1
                continue
            impl_ids = [identifier for identifier in by_path[unit['path']]
                        if all(blocks[side][identifier].get(line)==value for line,value in rows.items())]
            if not impl_ids:
                unresolved['implementation_outside_pool'] += 1
                continue
            language = LANGUAGE.get(Path(unit['path']).suffix)
            intervals = branches(unit,get_parser(language)) if language else []
            for branch in intervals:
                branch['ids'] = [identifier for identifier in by_path[unit['path']]
                                 if all(blocks[side][identifier].get(line)==rows.get(line) and line in rows
                                        for line in range(branch['start'],branch['end']+1))]
            key = side,anchor['file'],anchor['line'],anchor['column'],unit_id
            result[key] = dict(snapshot=side, caller=anchor, symbol=binding.get('requested_symbol',''),
                               definition=target, implementation_body=unit_id, caller_ids=call_ids,
                               implementation_ids=impl_ids, failure_branches=intervals)
    return dict(routes=list(result.values()),unresolved=dict(unresolved))
