"""Scoped, budget-preserving source rendering and actual-delivery observation.

Use ``fixed_rendering(product)`` outside ``ad.seed_rendering(inputs, product)``.
The latter retains ownership of snapshot-qualified IDs. Nothing here reads a
repository, changes source accounting, or interprets a review's correctness.
"""
from __future__ import annotations

import contextlib
import html
import re
import sys
import threading
from collections import defaultdict
from collections.abc import Mapping


class DeliveryBudgetError(RuntimeError):
    """Protected AST and one identifiable wrapper per seed cannot fit."""


_LOCK = threading.Lock()
_LINE = re.compile(r"^(\d+): (.*)$")
_OPEN = re.compile(r'^<(file|evidence)\s+(.+?)(/?)>$')
_ATTR = re.compile(r'([A-Za-z_][\w-]*)="([^"\r\n]*)"')


def _runs(numbers):
    runs = []
    for n in sorted(set(numbers)):
        if runs and runs[-1][1] + 1 == n:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return runs


def _protected(snippet):
    # Frozen builders mark every core AST snippet this way, including imports
    # and base-side snippets. Do not infer protection from file/target names.
    return snippet.reason.startswith('component AST ')


def _select(packet, module):
    ordered = list(module._diversity_ordered(packet))
    ordered.sort(key=lambda s: not _protected(s))  # stable within each group
    budget = module.context_budget_chars(packet)
    rows = []
    for snippet in ordered:
        eid = module.snippet_evidence_id(packet.id, snippet.path, snippet.start, snippet.end)
        if any(c in eid for c in ('"', '\n', '\r', '<', '>')):
            raise ValueError('evidence ID cannot be represented safely in a quoted attribute')
        path, reason = html.escape(snippet.path, quote=True), html.escape(snippet.reason, quote=True)
        prefix = (f'<file evidence_id="{eid}" path="{path}" '
                  f'lines="{snippet.start}-{snippet.end}" reason="{reason}"')
        low = module.is_low_signal_context(snippet.reason) and snippet.path not in packet.changed_files
        why = 'structural link only; use read_lines if needed' if low else 'context budget'
        omitted = prefix + f' delivery="omitted" content="omitted: {why}"/>'
        full = prefix + f' delivery="complete">\n{snippet.content}\n</file>'
        rows.append(dict(snippet=snippet, eid=eid, prefix=prefix, omitted=omitted,
                         full=full, low=low, protected=_protected(snippet)))

    # Snapshot remapping adds @head/@base after this function; reserve those
    # five characters without changing the product's total context budget.
    remap_reserve = 5
    minimum = [len(r['full'] if r['protected'] else r['omitted']) + remap_reserve for r in rows]
    if sum(minimum) > budget:
        raise DeliveryBudgetError(f'protected AST and seed IDs need {sum(minimum)} chars; cap={budget}')
    selected, used, remaining_min = [], 0, sum(minimum)
    for row, floor in zip(rows, minimum):
        remaining_min -= floor
        available = budget - used - remaining_min - remap_reserve
        snippet = row['snippet']
        if row['protected'] or (not row['low'] and len(row['full']) <= available):
            content, rendered, stub = snippet.content, row['full'], False
        else:
            content, rendered, stub = '', row['omitted'], True
            if not row['low']:
                start = row['prefix'] + ' delivery="partial">\n'
                end = '\n<omitted reason="context budget; remaining source lines not delivered"/>\n</file>'
                room = available - len(start) - len(end)
                chunks, count = [], 0
                # Whole physical lines only. A single long line is either
                # supplied whole or omitted; no per-snippet character slice.
                for line in re.findall(r'[^\n]*\n|[^\n]+$', snippet.content):
                    if count + len(line) > room:
                        break
                    chunks.append(line)
                    count += len(line)
                if chunks:
                    content = ''.join(chunks)
                    rendered, stub = start + content + end, False
        used += len(rendered) + remap_reserve
        selected.append(module.PromptContextItem(snippet, row['eid'], content, rendered, stub))
    assert used <= budget
    return selected


@contextlib.contextmanager
def fixed_rendering(product):
    """Patch all loaded product aliases; restore even if rendering fails.

    Enter before seed_rendering. Concurrent reviews must use separate processes,
    as required by the existing snapshot/ID patch mechanism.
    """
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError('fixed rendering is already active in this process')
    module = product.prompt_context
    original = module.select_prompt_context
    aliases = [(m, name) for modname, m in list(sys.modules.items())
               if modname.startswith('review_agent') and m is not None
               for name, value in list(vars(m).items()) if value is original]
    if not any(m is module and name == 'select_prompt_context' for m, name in aliases):
        aliases.append((module, 'select_prompt_context'))

    def select(packet):
        return _select(packet, module)
    select.__wrapped__ = original
    try:
        for m, name in aliases:
            setattr(m, name, select)
        yield {'aliases': [f'{getattr(m, "__name__", "prompt_context")}.{name}' for m, name in aliases],
               'scope': 'seed context only; unchanged total context chars and source token accounting'}
    finally:
        for m, name in aliases:
            setattr(m, name, original)
        _LOCK.release()


def _blocks(text):
    """Line-framed product wrappers, not an XML parser for arbitrary code.

    Numbered source containing XML-like strings cannot open/close a wrapper.
    Unterminated trailing lines never certify complete source lines.
    """
    active = None
    pieces = text.split('\n')
    for i, physical in enumerate(pieces):
        terminated = i < len(pieces) - 1
        line = physical[:-1] if terminated and physical.endswith('\r') else physical
        if active and line == f'</{active["tag"]}>':
            active['closed'] = True
            yield active
            active = None
            continue
        opening = _OPEN.fullmatch(line)
        if opening:
            if active:
                yield active
            pairs = _ATTR.findall(opening.group(2))
            attrs = {k: html.unescape(v) for k, v in pairs}
            if len(attrs) != len(pairs):
                active = None
                continue
            active = {'tag': opening.group(1), 'attrs': attrs, 'body': [],
                      'closed': bool(opening.group(3)), 'self_closing': bool(opening.group(3))}
            if active['self_closing']:
                yield active
                active = None
            continue
        if active:
            active['body'].append((line, terminated))
    if active:
        yield active


def _field(ev, name, default=None):
    return ev.get(name, default) if isinstance(ev, Mapping) else getattr(ev, name, default)


def _source_path_matches(block, file):
    attrs = block['attrs']
    if block['tag'] == 'file':
        return attrs.get('path') == file
    if 'path' in attrs and attrs['path'] != file:
        return False
    source = attrs.get('source', '')
    if attrs.get('operation') == 'grep_literal' and source.startswith('grep:'):
        # The exact normalized evidence ID binds a per-file grep result. Its
        # source label deliberately contains the query rather than the path.
        return True
    return source == file or bool(re.fullmatch(re.escape(file) + r':\d+-\d+', source)) or bool(
        re.fullmatch('declaration:' + re.escape(file) + r':\d+-\d+', source))


def observe_delivery(parts, store):
    """Return actual source coverage from one final composed call, purely.

    ``parts`` is the final raw system/shared/prompt/suffix mapping (or composed
    string). ``store`` is EvidenceStore, its by_id mapping, or an Evidence list.
    Exact known IDs and complete ``N: text`` lines are required. Stored range
    claims, metadata locations, and diff hunks never grant source-body coverage.
    """
    if isinstance(parts, str):
        texts = {'composed': parts}
    elif isinstance(parts, Mapping):
        texts = parts.get('raw', parts)
        texts = {k: v for k, v in texts.items() if k in ('system', 'shared', 'prompt', 'suffix') and isinstance(v, str)}
    else:
        raise TypeError('parts must be raw prompt parts or a composed string')
    values = getattr(store, 'by_id', store)
    values = values.values() if isinstance(values, Mapping) else values
    by_id = {_field(ev, 'id'): ev for ev in values}
    blocks = defaultdict(list)
    unknown = set()
    for part, text in texts.items():
        for block in _blocks(text):
            eid = block['attrs'].get('evidence_id' if block['tag'] == 'file' else 'id')
            if eid in by_id:
                block['part'] = part
                blocks[eid].append(block)
            elif eid:
                unknown.add(eid)
    spans, evidence = [], []
    for eid, ev in by_id.items():
        kind = _field(ev, 'content_kind', 'source_lines')
        snapshot, file = _field(ev, 'snapshot'), _field(ev, 'file', _field(ev, 'path'))
        occurrences = blocks[eid]
        row = {'evidence_id': eid, 'file': file, 'snapshot': snapshot,
               'content_kind': kind, 'occurrences': len(occurrences),
               'parts': sorted({b['part'] for b in occurrences})}
        if kind != 'source_lines':
            row.update(status='diff' if kind == 'diff_hunk' else 'metadata',
                       block_present=bool(occurrences), complete=False, visible_lines=[], omitted_lines=[],
                       note='Not source-body coverage; diff is separate public changed-code evidence.')
            evidence.append(row)
            continue
        expected = {int(n): str(t) for n, t in _field(ev, 'lines', ())}
        visible, mismatched, invalid, open_blocks, path_errors = set(), set(), set(), 0, 0
        for block in occurrences:
            if not _source_path_matches(block, file):
                path_errors += 1
                continue
            declared_snapshot = block['attrs'].get('snapshot')
            id_snapshot = eid.rsplit('@', 1)[-1] if '@' in eid else None
            if (declared_snapshot and declared_snapshot != snapshot) or (id_snapshot in ('head', 'base') and id_snapshot != snapshot):
                path_errors += 1
                continue
            if not block['closed']:
                open_blocks += 1
            for line, terminated in block['body']:
                match = _LINE.fullmatch(line)
                if not match:
                    continue
                n, text = int(match.group(1)), match.group(2)
                if n not in expected:
                    invalid.add(n)
                elif not terminated or text != expected[n]:
                    mismatched.add(n)
                else:
                    visible.add(n)
        visible -= mismatched  # conflicting repeated records fail closed
        omitted = set(expected) - visible
        complete = bool(expected) and not omitted
        row.update(status='complete' if complete else 'partial' if visible else 'omitted',
                   block_present=bool(occurrences), complete=complete, expected_lines=sorted(expected),
                   visible_lines=sorted(visible), omitted_lines=sorted(omitted),
                   visible_runs=_runs(visible), omitted_runs=_runs(omitted),
                   mismatched_lines=sorted(mismatched), unexpected_lines=sorted(invalid),
                   unterminated_blocks=open_blocks, path_or_snapshot_mismatches=path_errors,
                   reason=None if complete else 'block_absent' if not occurrences else
                   'source_text_or_final_line_mismatch' if mismatched else
                   'path_or_snapshot_mismatch' if path_errors else 'source_lines_not_delivered')
        evidence.append(row)
        if visible:
            spans.append({'file': file, 'snapshot': snapshot, 'lines': sorted(visible),
                          'runs': _runs(visible), 'evidence_id': eid})
    union = {(s['snapshot'], s['file'], n) for s in spans for n in s['lines']}
    source_rows = [e for e in evidence if e['content_kind'] == 'source_lines']
    return {'schema_version': 1, 'spans': spans, 'evidence': evidence,
            'source_line_union_count': len(union), 'source_evidence_count': len(source_rows),
            'complete_source_evidence_count': sum(e['complete'] for e in source_rows),
            'incomplete_source_evidence_ids': [e['evidence_id'] for e in source_rows if not e['complete']],
            'unknown_evidence_ids': sorted(unknown),
            'scope': 'One actual call; exact source lines only. Diff/metadata excluded from body spans.'}
