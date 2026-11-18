"""C policy: controller-managed bookkeeping between the model JSON and the unchanged strict ``oar_core.Ledger`` (V3).

The model assesses claims, required facts, evidence support, challenges and interpretation changes, and writes the
reason/basis of every change. The controller owns only mechanical identifiers and records:

* Evidence handles. Only RENDERER-OWNED element headers are rewritten: a line that starts with the exact header the
  product renderer emits for an evidence element provided to that call (``<file evidence_id="ID" path="FILE"`` for a
  seed/diff file, ``<evidence id="ID" operation="OP" source="SRC">`` for a lookup result), each element once. Its id
  value becomes a stable short handle ``E<n>`` (assigned in order of first exposure). Code and tool text are never
  rewritten, even when a source line spells an evidence tag or a canonical id. A handle exists only for an element that
  was actually rendered; one handle = one canonical id (never crosses snapshots or files). Every substitution is
  position-recorded so each returned handle can be traced to its element header. No catalog is added.
* Canonicalisation of the response. Handles become canonical ids before the product parser and the ledger see the
  payload. Anything else in an evidence field (an unseen handle, a long id, a path) is NOT repaired: it becomes
  ``refused-handle:<raw>``, which no store contains, so the ledger and the citation audit reject it visibly.
* State ids. Candidates/obligations/routes/challenges/notes have kind-prefixed ids (C/O/R/X/U + number). Existing ids
  are kept; a new item keeps its label when it is well formed and free, otherwise gets the next free id; references
  inside the same response follow the same map. Ambiguous references become non-resolving sentinels (the ledger rejects
  them). Duplicate entries are preserved for the unchanged ledger's duplicate-id rejection; they are never merged.
* Change records. The controller computes the actual differences against the accepted state with the core's own merge
  and diff, and writes one V2 change record per difference (kind, affected id). The reason, basis, superseded_by and
  claim effects are copied from the item the model changed - never generated. A difference whose item carries no
  reason gets no record, so the unchanged core rejects the whole update (``undocumented_change``), the previous state
  stays, and the feedback names the missing reason.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Optional

import oar_core as oc

HANDLE_PREFIX = 'E'
HANDLE_RE = re.compile(r'^E[1-9]\d*$')
REFUSED = 'refused-handle:'
HEADER_START = re.compile(r'^(<file evidence_id="|<evidence id=")([^"]*)"')      # anchored at a line start
KIND_PREFIX = {'candidates': 'C', 'obligations': 'O', 'routes': 'R', 'challenges': 'X', 'unresolved': 'U'}
LABEL_RE = {kind: re.compile(rf'^{p}[1-9]\d{{0,2}}$') for kind, p in KIND_PREFIX.items()}
BASES = sorted({*oc.REGRESSION_BASES, *oc.REMOVAL_BASES, *oc.ROUTE_REMOVAL_BASES, *oc.WITHDRAW_BASES,
                *oc.CHALLENGE_REVISION_BASES})


class ControllerError(RuntimeError):
    pass


# ============================================================ evidence handles
class HandleTable:
    """canonical evidence id <-> E<n>. Assigned only for ids rendered in a model input; never reassigned."""

    def __init__(self):
        self.by_id: dict = {}
        self.by_handle: dict = {}
        self.first_stage: dict = {}
        self.stages: dict = {}         # stage -> {'handles': [...], 'occurrences': {handle: [(part, offset)]}}

    def handle_for(self, canonical: str, stage: str) -> str:
        if canonical not in self.by_id:
            handle = f'{HANDLE_PREFIX}{len(self.by_id) + 1}'
            if handle in self.by_handle:                  # cannot happen (monotone numbering); fail closed if it does
                raise ControllerError(f'handle collision {handle}')
            self.by_id[canonical], self.by_handle[handle] = handle, canonical
            self.first_stage[handle] = stage
        return self.by_id[canonical]

    def expose(self, stage: str, parts: dict, headers: dict, order=('system', 'shared', 'prompt', 'suffix')) -> dict:
        """Rewrite the id of every renderer-owned element header to its handle.

        ``headers``: canonical id -> the exact header prefix the renderer emits for that element in this call (only
        elements provided to this call). A line that starts like an element header is renderer-owned iff its id is in
        ``headers``, the line starts with exactly that prefix, and the element was not seen before in this call. Any
        other line-start header (unknown id, different attributes, repeated element) raises ControllerError: the input
        is not what this controller can map losslessly, so the review fails closed. Lines within an element's body
        are opaque, including tag-looking source/tool literals. No handle is allocated until every header has been
        validated; a failed exposure cannot make an unexposed element citable.
        """
        if not isinstance(headers, dict):
            raise ControllerError(f'{stage}: renderer headers must be an id-to-header mapping')
        spans: dict = {}
        rendered: set = set()
        for name in order:
            text = parts[name]
            spans[name] = []
            cursor, closing = 0, None
            for line in text.splitlines(keepends=True):
                if closing is not None:
                    if line.rstrip('\r\n') == closing:
                        closing = None
                    cursor += len(line)
                    continue
                m = HEADER_START.match(line)
                if m is None:
                    cursor += len(line)
                    continue
                canonical = m.group(2)
                expected = headers.get(canonical)
                if expected is None:
                    raise ControllerError(f'{stage}: rendered element {canonical!r} was not provided to this call')
                if not isinstance(expected, str) or not expected or not line.startswith(expected):
                    raise ControllerError(f'{stage}: element header for {canonical!r} differs from the renderer header')
                if canonical in rendered:
                    raise ControllerError(f'{stage}: element {canonical!r} rendered twice')
                rendered.add(canonical)
                spans[name].append((cursor + m.start(2), cursor + m.end(2), canonical))
                if not line.rstrip('\r\n').endswith('/>'):
                    closing = '</file>' if m.group(1).startswith('<file') else '</evidence>'
                cursor += len(line)
            if closing is not None:
                raise ControllerError(f'{stage}: renderer element has no {closing} terminator in {name}')
        out, occurrences, seen = {}, {}, []
        for name in order:
            text = parts[name]
            pieces, cursor, delta = [], 0, 0
            for start, end, canonical in spans[name]:
                handle = self.handle_for(canonical, stage)
                pieces.extend((text[cursor:start], handle))
                occurrences.setdefault(handle, []).append((name, start + delta))
                delta += len(handle) - (end - start)
                cursor = end
                seen.append(handle)
            pieces.append(text[cursor:])
            out[name] = ''.join(pieces)
        self.stages[stage] = {'handles': seen, 'occurrences': occurrences}
        return out

    def valid(self, stage: str) -> dict:
        """handle -> canonical id for the handles that occur in this stage's input."""
        return {h: self.by_handle[h] for h in self.stages.get(stage, {}).get('handles', [])}

    def to_handle(self, canonical: str) -> Optional[str]:
        return self.by_id.get(canonical)

    def map_text(self, text: str) -> str:
        """Canonical ids inside controller-written text (errors) -> handles; longest id first, exact substrings only."""
        if not self.by_id:
            return text
        pattern = re.compile('|'.join(re.escape(i) for i in sorted(self.by_id, key=len, reverse=True)))
        return pattern.sub(lambda m: self.by_id[m.group(0)], text)

    def export(self) -> dict:
        return {'handles': [{'handle': h, 'canonical_id': i, 'first_stage': self.first_stage[h]}
                            for h, i in sorted(self.by_handle.items(), key=lambda kv: int(kv[0][1:]))],
                'stages': {s: {'handles': v['handles'],
                               'occurrences': {h: [list(o) for o in occ] for h, occ in v['occurrences'].items()}}
                           for s, v in self.stages.items()}}


def trace_handles(table: HandleTable, stage: str, parts: dict, handles) -> list:
    """Final audit: each returned handle -> canonical id + its actual position(s) in that stage's model input."""
    out = []
    for h in handles:
        row = {'handle': h, 'canonical_id': table.by_handle.get(h), 'first_stage': table.first_stage.get(h),
               'occurrences': [], 'traced': False}
        for part, offset in table.stages.get(stage, {}).get('occurrences', {}).get(h, []):
            text = parts.get(part, '')
            ok = text[offset:offset + len(h)] == h and text[offset + len(h):offset + len(h) + 1] == '"'
            row['occurrences'].append({'part': part, 'offset': offset, 'matches_text': ok})
        row['traced'] = bool(row['occurrences']) and all(o['matches_text'] for o in row['occurrences'])
        out.append(row)
    return out


# ============================================================ response canonicalisation
EVIDENCE_PATHS = (('findings', 'evidence'), ('review.observations', 'evidence'))


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _str(value) -> str:
    return value.strip() if isinstance(value, str) else ''


class Canonicalizer:
    """One response -> (payload the product/ledger see, report). Pure function of (raw, valid handles, ledger)."""

    def __init__(self, ledger: oc.Ledger, valid_handles: dict):
        self.ledger, self.valid = ledger, valid_handles
        self.refusals: list = []
        self.resolved: list = []
        self.label_maps: dict = {}
        self.ambiguous: list = []
        self.records: list = []
        self.missing: list = []

    # -- evidence
    def evidence(self, value, path) -> Any:
        if isinstance(value, str) and value in self.valid:
            self.resolved.append({'path': path, 'handle': value, 'canonical_id': self.valid[value]})
            return self.valid[value]
        reason = ('handle_not_in_this_input' if isinstance(value, str) and HANDLE_RE.match(value)
                  else 'not_a_handle')
        self.refusals.append({'path': path, 'value': value, 'reason': reason})
        return f'{REFUSED}{value}'

    def evidence_list(self, values, path) -> list:
        if not isinstance(values, list):
            return values
        return [self.evidence(v, f'{path}[{i}]') for i, v in enumerate(values)]

    # -- ids
    @staticmethod
    def _assign(raw_labels, existing, prefix, kind) -> dict:
        """label -> canonical id: existing ids keep, well-formed free labels keep, others get the next free number."""
        taken = set(existing)
        mapping: dict = {}
        for label in raw_labels:
            if not isinstance(label, str) or label in mapping:
                continue
            if label in existing:
                mapping[label] = label
            elif LABEL_RE[kind].match(label) and label not in taken:
                mapping[label] = label
                taken.add(label)
        n = 1
        for label in raw_labels:
            if not isinstance(label, str) or label in mapping:
                continue
            while f'{prefix}{n}' in taken:
                n += 1
            mapping[label] = f'{prefix}{n}'
            taken.add(mapping[label])
        return mapping

    def run(self, payload: Any) -> tuple:
        if not isinstance(payload, dict):
            return payload, {'note': 'payload is not an object; passed unchanged'}
        out = copy.deepcopy(payload)
        for i, f in enumerate(_list(out.get('findings'))):
            if isinstance(f, dict):
                for j, ev in enumerate(_list(f.get('evidence'))):
                    if isinstance(ev, dict) and 'evidence_id' in ev:
                        ev['evidence_id'] = self.evidence(ev['evidence_id'], f'$.findings[{i}].evidence[{j}]')
        review = out.get('review') if isinstance(out.get('review'), dict) else {}
        for i, o in enumerate(_list(review.get('observations'))):
            if isinstance(o, dict):
                for j, ev in enumerate(_list(o.get('evidence'))):
                    if isinstance(ev, dict) and 'evidence_id' in ev:
                        ev['evidence_id'] = self.evidence(ev['evidence_id'], f'$.review.observations[{i}].evidence[{j}]')
        assessment = review.get('context_assessment') if isinstance(review.get('context_assessment'), dict) else {}
        for key in ('used_context', 'dismissed_context'):
            for i, row in enumerate(_list(assessment.get(key))):
                if isinstance(row, dict) and 'evidence_id' in row:
                    row['evidence_id'] = self.evidence(row['evidence_id'], f'$.review.context_assessment.{key}[{i}]')
        raw_cands = [c for c in _list(out.get('candidates')) if isinstance(c, dict)]
        cand_map = self._assign([c.get('id') for c in raw_cands], set(self.ledger.candidates), 'C', 'candidates')
        self.label_maps['candidates'] = {k: v for k, v in cand_map.items() if k != v}
        core = []
        item_maps = {}
        for i, cand in enumerate(_list(out.get('candidates'))):
            if not isinstance(cand, dict):
                core.append(cand)
                continue
            cid = cand_map.get(cand.get('id'), cand.get('id'))
            item, maps = self._candidate(cand, cid, f'$.candidates[{i}]')
            item_maps.setdefault(cid, maps)
            core.append(item)
        out['candidates'] = core
        for f in _list(out.get('findings')):
            if isinstance(f, dict) and isinstance(f.get('candidate_id'), str):
                f['candidate_id'] = cand_map.get(f['candidate_id'], f['candidate_id'])
        for r in _list(out.get('context_requests')):
            if not isinstance(r, dict):
                continue
            cid = cand_map.get(r.get('candidate_id'), r.get('candidate_id'))
            r['candidate_id'] = cid
            maps = item_maps.get(cid, {})
            if r.get('obligation_id'):
                r['obligation_id'] = maps.get('obligations', {}).get(r['obligation_id'], r['obligation_id'])
            if r.get('challenge_id'):
                r['challenge_id'] = maps.get('challenges', {}).get(r['challenge_id'], r['challenge_id'])
        report = {'evidence_resolved': self.resolved, 'evidence_refused': self.refusals, 'label_maps': self.label_maps,
                  'ambiguous_references': self.ambiguous, 'generated_records': self.records,
                  'changes_without_reason': self.missing}
        return out, report

    # -- one candidate
    def _candidate(self, cand: dict, cid: str, path: str) -> tuple:
        old = self.ledger.candidates.get(cid)
        maps = {}
        for kind in ('obligations', 'routes', 'challenges', 'unresolved'):
            existing = set(getattr(old, 'notes' if kind == 'unresolved' else kind) if old else ())
            labels = [x.get('id') for x in _list(cand.get(kind)) if isinstance(x, dict)]
            maps[kind] = self._assign(labels, existing, KIND_PREFIX[kind], kind)
            changed = {k: v for k, v in maps[kind].items() if k != v}
            if changed:
                self.label_maps[f'{cid}.{kind}'] = changed
        ob_ids = set(maps['obligations'].values()) | (set(old.obligations) if old else set())
        route_ids = set(maps['routes'].values()) | (set(old.routes) if old else set())
        sources: dict = {}                     # (kind, canonical id) -> the raw response entry that set it

        def ob_ref(x):
            return maps['obligations'].get(x, x)

        def target_ref(x, where):
            if x in (None, ''):
                return None
            as_ob = maps['obligations'].get(x, x if x in ob_ids else None)
            as_route = maps['routes'].get(x, x if x in route_ids else None)
            if as_ob is not None and as_route is not None and as_ob != as_route:
                self.ambiguous.append({'path': where, 'value': x, 'reason': 'names both an obligation and a route'})
                return f'ambiguous-label:{x}'               # never guessed: the ledger reports bad_challenge_target
            return as_ob or as_route or x

        item = {'id': cid, 'claim': cand.get('claim'), 'scope': cand.get('scope'), 'origin': cand.get('origin'),
                'origin_evidence_ids': self.evidence_list(cand.get('origin_evidence_ids', []), f'{path}.origin_evidence_ids')}
        obligations = []
        for j, o in enumerate(_list(cand.get('obligations'))):
            if not isinstance(o, dict):
                obligations.append(o)
                continue
            row = {k: o[k] for k in ('predicate', 'required', 'requires', 'fact_kind', 'snapshot', 'status') if k in o}
            row['id'] = maps['obligations'].get(o.get('id'), o.get('id'))
            row['evidence_ids'] = self.evidence_list(o.get('evidence_ids', []), f'{path}.obligations[{j}].evidence_ids')
            depends = o.get('depends_on', [])
            row['depends_on'] = [ob_ref(d) for d in depends] if isinstance(depends, list) else depends
            sources.setdefault(('obligations', row['id']), o)
            obligations.append(row)
        item['obligations'] = obligations
        routes = []
        for r in _list(cand.get('routes')):
            if not isinstance(r, dict):
                routes.append(r)
                continue
            ids = r.get('obligation_ids')
            row = {'id': maps['routes'].get(r.get('id'), r.get('id')),
                   'obligation_ids': [ob_ref(x) for x in ids] if isinstance(ids, list) else ids}
            sources.setdefault(('routes', row['id']), r)
            routes.append(row)
        item['routes'] = routes
        challenges = []
        for j, c in enumerate(_list(cand.get('challenges'))):
            if not isinstance(c, dict):
                challenges.append(c)
                continue
            row = {k: c[k] for k in ('text', 'material', 'state') if k in c}
            row['id'] = maps['challenges'].get(c.get('id'), c.get('id'))
            row['evidence_ids'] = self.evidence_list(c.get('evidence_ids', []), f'{path}.challenges[{j}].evidence_ids')
            row['target'] = target_ref(c.get('target'), f'{path}.challenges[{j}].target')
            sources.setdefault(('challenges', row['id']), c)
            challenges.append(row)
        item['challenges'] = challenges
        item['unresolved'] = [{'id': maps['unresolved'].get(n.get('id'), n.get('id')), 'text': n.get('text')}
                              if isinstance(n, dict) else n for n in _list(cand.get('unresolved'))]
        removals = {'remove_obligations': [], 'remove_routes': [], 'resolve_unresolved': []}
        withdraw = False
        for rm in _list(cand.get('remove')):
            rid = rm.get('id') if isinstance(rm, dict) else None
            if rid in (cid, cand.get('id')) and isinstance(rid, str):
                withdraw = True                     # the candidate's own id: withdrawal (it stays, verdict withdrawn)
                sources.setdefault(('remove', cid), rm)
                continue
            if not isinstance(rid, str) or not rid:
                removals['remove_obligations'].append(f'{REFUSED}{rid}')      # visible, rejected as unknown
                continue
            # by id letter; anything else (a challenge, another candidate) is passed as an obligation removal and
            # rejected by the ledger as unknown
            key = {'R': 'remove_routes', 'U': 'resolve_unresolved'}.get(rid[0], 'remove_obligations')
            removals[key].append(rid)
            sources.setdefault(('remove', rid), rm)
        item.update(removals)
        item['withdraw'] = withdraw
        item['changes'] = self._records(old, item, cand, sources, maps, path) if old is not None else []
        return item, maps

    # -- change records from actual differences
    def _records(self, old, item, cand, sources, maps, path) -> list:
        scratch: list = []
        probe = {k: v for k, v in item.items() if k != 'changes'}
        try:
            new, _ = self.ledger._merge(old, probe, path, scratch)
        except Exception:                       # malformed: the ledger reports it on the real update
            return []
        if new is None or scratch:
            return []
        records = []
        for kind, target, a, b, accepted in self.ledger._diffs(old, new):
            source, where = self._source_of(kind, target, cand, sources)
            reason = _str((source or {}).get('reason'))
            basis = _str((source or {}).get('basis')) or None
            entry = {'candidate': item['id'], 'diff': kind, 'target': target, 'source': where}
            if not reason:
                entry['missing'] = 'reason'
                self.missing.append({'candidate': item['id'], 'diff': kind, 'target': target, 'field': where})
                self.records.append(entry)
                continue                        # no record: the core rejects the update as undocumented
            rec = {'kind': self._kind(kind, accepted, basis), 'affected_ids': [target], 'reason': reason}
            if basis:
                rec['basis'] = basis
            superseded = _str((source or {}).get('superseded_by'))
            if kind == 'route_removed' and superseded:
                rec['superseded_by'] = maps['routes'].get(superseded, superseded)
            for src, dst in (('effect_if_true', 'claim_effect_if_true'), ('effect_if_false', 'claim_effect_if_false')):
                if _str((source or {}).get(src)):
                    rec[dst] = _str(source[src])
            records.append(rec)
            entry['record'] = rec
            self.records.append(entry)
        return records

    @staticmethod
    def _source_of(kind, target, cand, sources):
        if kind in ('claim', 'scope'):
            return cand, 'candidate'
        if kind in ('obligation_removed', 'route_removed', 'note_resolved', 'withdrawn'):
            return sources.get(('remove', target)), f'remove[{target}]'
        if kind.startswith('obligation_') or kind in ('required', 'status', 'evidence'):
            return sources.get(('obligations', target)), f'obligations[{target}]'
        if kind.startswith('route_'):
            return sources.get(('routes', target)), f'routes[{target}]'
        if kind.startswith('challenge_'):
            return sources.get(('challenges', target)), f'challenges[{target}]'
        return None, kind

    @staticmethod
    def _kind(diff_kind, accepted, basis) -> str:
        if basis == 'interpretation_error' and 'interpretation_correction' in accepted:
            return 'interpretation_correction'
        if diff_kind == 'evidence':
            return 'evidence_changed'
        if diff_kind == 'challenge_changed':
            return 'challenge_resolved'
        return accepted[0]


def canonicalize(payload: Any, valid_handles: dict, ledger: oc.Ledger) -> tuple:
    """(payload with canonical ids + core-form candidates incl. generated change records, report)."""
    try:
        return Canonicalizer(ledger, valid_handles).run(payload)
    except Exception as exc:                    # never crash the review: the raw payload is kept and the failure shown
        return copy.deepcopy(payload), {'controller_failure': f'{type(exc).__name__}: {exc}'[:300]}


# ============================================================ prompt-facing state and feedback
def handle_state_text(text: str, table: HandleTable) -> str:
    """Ledger render (JSON) -> same JSON with evidence ids shown as handles. Ids without a handle stay visible as such."""
    rows = json.loads(text)
    for cand in rows:
        for ob in cand.get('obligations', []):
            ob['evidence_ids'] = [table.to_handle(e) or f'no-handle:{e}' for e in ob.get('evidence_ids', [])]
        for ch in cand.get('challenges', []):
            ch['evidence_ids'] = [table.to_handle(e) or f'no-handle:{e}' for e in ch.get('evidence_ids', [])]
    return json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def feedback_errors(errors: list, report: Optional[dict], table: HandleTable) -> list:
    """Ledger rejection errors in the C vocabulary (no change records exist for the model); ids shown as handles."""
    out = []
    missing = {(m['candidate'], m['target']): m for m in (report or {}).get('changes_without_reason', [])}
    for e in errors:
        e = dict(e)
        if e.get('code') == 'undocumented_change':
            cid = re.search(r'\[(.*?)\]', e.get('path', ''))
            target = re.match(r'(\S+) on (\S+) needs', e.get('message', ''))
            key = (cid.group(1) if cid else None, target.group(2) if target else None)
            m = missing.get(key)
            e = {'code': 'missing_reason', 'path': e.get('path'),
                 'message': (f'{target.group(1) if target else "change"} on {key[1]} has no `reason` in '
                             f'{m["field"] if m else "the changed item"}; the change was not accepted')}
        elif e.get('code') == 'missing_effect':
            e['message'] = e['message'].replace('claim_effect_if_true', 'effect_if_true').replace(
                'must state', 'needs').replace('(how the claim differs if the fact is true/false)', 'on that item')
        e['message'] = table.map_text(str(e.get('message', '')))
        e['path'] = table.map_text(str(e.get('path', '')))
        out.append(e)
    return out


# ============================================================ C schema and prompt text
def _obj(props: dict) -> dict:
    return {'type': 'object', 'additionalProperties': False, 'required': list(props), 'properties': props}


def _arr(item) -> dict:
    return {'type': 'array', 'items': item}


def _enum(values, nullable=False) -> dict:
    return {'type': ['string', 'null'] if nullable else 'string', 'enum': [*values, None] if nullable else list(values)}


S, B_, NS = {'type': 'string'}, {'type': 'boolean'}, {'type': ['string', 'null']}
STRS = _arr(S)
BASIS = _enum(['', *BASES])
C_OBLIGATION = _obj({'id': S, 'predicate': S, 'required': B_, 'requires': _enum(oc.REQUIRES),
                     'fact_kind': _enum(oc.FACT_KINDS, True), 'snapshot': _enum(['head', 'base'], True),
                     'status': _enum(oc.STATUSES), 'evidence_ids': STRS, 'depends_on': STRS,
                     'reason': S, 'basis': BASIS, 'effect_if_true': S, 'effect_if_false': S})
C_CANDIDATE = _obj({
    'id': S, 'claim': S, 'scope': S, 'origin': S, 'origin_evidence_ids': STRS, 'reason': S, 'basis': BASIS,
    'obligations': _arr(C_OBLIGATION),
    'routes': _arr(_obj({'id': S, 'obligation_ids': STRS, 'reason': S})),
    'challenges': _arr(_obj({'id': S, 'text': S, 'material': B_, 'state': _enum(oc.CHALLENGE_STATES),
                             'evidence_ids': STRS, 'target': NS, 'reason': S, 'basis': BASIS})),
    'unresolved': _arr(_obj({'id': S, 'text': S})),
    'remove': _arr(_obj({'id': S, 'basis': BASIS, 'reason': S, 'superseded_by': S,
                         'effect_if_true': S, 'effect_if_false': S}))})


def c_response_schema(base_schema: dict) -> dict:
    """The V2 schema with only ``candidates`` replaced (review/findings/context_requests are byte-identical)."""
    schema = copy.deepcopy(base_schema)
    schema['properties']['candidates'] = _arr(C_CANDIDATE)
    return schema


def c_contract(common_contract: str) -> str:
    """V2 common contract with exactly two lines changed (evidence citation and the candidate format)."""
    old_cite = '- Cite only evidence ids that appear in this prompt (`context:...`, `diff:...`, `agentic:...`). Never invent ids.'
    new_cite = ('- Every evidence element in this prompt carries a short handle (`E1`, `E2`, ...) as its `evidence_id`/`id`. '
                'Cite exactly those handles, everywhere an evidence id is asked for. Anything else (a handle that is not in '
                'this prompt, a path, a line range, a long id) is refused and counts as an invalid citation.')
    old_tail = 'Use empty lists for what does not apply and `changes: []` unless you changed an existing candidate.'
    new_tail = ('Use empty lists for what does not apply. There is no change log to write: give each item its current '
                'values, and the review runner works out what changed (see the state rules).')
    if common_contract.count(old_cite) != 1 or common_contract.count(old_tail) != 1:
        raise ControllerError('common contract text changed; C contract cannot be derived exactly')
    return common_contract.replace(old_cite, new_cite).replace(old_tail, new_tail)


C_STATE_RULES = """<oar_state_rules>
Your candidate state is kept and checked between responses (limits: 3 candidates; 5 obligations, 3 routes, 3 challenges, 3 notes per candidate). The review runner does the bookkeeping: it assigns ids, compares your response with the accepted state, and writes the change log. You give the assessment and the reasons.
- Ids: candidates C<n>, obligations O<n>, routes R<n>, challenges X<n>, notes U<n>. Use the ids shown in the state for existing items. For a new item use a new label with the right letter; if it is taken or malformed the runner gives it the next free id and rewrites your references to that label inside the same response. Cite evidence only by its E handle.
- The state below is what has been accepted so far. Candidates, obligations, routes, challenges and notes you do not mention are kept unchanged; omission never withdraws, resolves or removes anything. To change an item, list it with all its current values.
- Every status other than `unknown` must cite evidence handles; any handle you newly cite must be present in the current prompt. Source-bearing obligations (`requires` = value or body) need source lines, not position-only results.
- When you change an existing item, or add an obligation, route or challenge to an existing candidate, write why in that item's `reason` (a change of claim or scope: the candidate's `reason` and `basis`). A change with no reason is not accepted. Also set `basis` where the change weakens or reverses something: supported -> unknown needs interpretation_error | new_premise | evidence_invalidated; making a required obligation optional or removing one needs not_necessary_for_claim | interpretation_error | scope_revised (a contradicted or conflicted one cannot be removed as not_necessary_for_claim); reopening a contradicted/conflicted obligation without new evidence, or closing a pending challenge without evidence, needs interpretation_error; changing a challenge's text, target or materiality needs interpretation_error | irrelevant_to_claim | scope_revised; withdrawing needs refuted_in_scope | interpretation_error | duplicate. Adding or removing an obligation, or changing `required`, also needs `effect_if_true` and `effect_if_false` (how the claim differs if the fact is true / false); so does revising an obligation's predicate, requires, fact_kind, snapshot or depends_on unless the basis is interpretation_error. Correcting an earlier misreading needs no new source: use basis interpretation_error and say in `reason` what was misread. Leave `reason`/`basis`/effects empty on items you did not change.
- Removing: list the id in `remove` with `basis`, `reason` (and the effects for an obligation): obligations O<n>, routes R<n> (basis contradicted | interpretation_error | scope_revised | superseded, with `superseded_by` naming the replacing route), resolved notes U<n>. Listing the candidate's own id withdraws the candidate (it stays in the state as withdrawn). Challenges are never removed; close them with `state` and evidence or basis.
- A route is dead only if one of its required obligations is contradicted. An alternative route that remains open keeps the candidate alive. Unrelated unknowns do not remove support that is already established.
- An update that fails these checks is rejected as a whole, the previous state stays in force, and the reasons are shown to you next time.
- Reportability: a finding in `findings` is reportable only if its `candidate_id` names a candidate in the state whose verdict is `supported` after your update in that same response, i.e. at least one route whose every required obligation is `supported` (dependencies included) and which no pending material challenge targets (a challenge without target counts against every route), and every evidence handle it cites appears in this prompt. A finding that does not meet this is recorded as non-reportable and the review ends as an invalid final report. If you cannot reach it, return no findings.
</oar_state_rules>"""
