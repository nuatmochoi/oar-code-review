"""OAR core: candidate ledger, request ranking, and evidence accounting.

Independent of the product package. Pure Python plus tiktoken (o200k_base).

What the mechanical validator guarantees (and what it does not)
---------------------------------------------------------------
Guaranteed mechanically: payload shape, id uniqueness/existence, that cited
evidence ids exist in the store, were actually provided to the model in this
round, and belong to the snapshot the obligation expects; that evidence
*kinds* can in principle carry the fact an obligation asks for (a member list
cannot carry an attribute value; a static edge is not a runtime binding); that
every change to claim/scope/obligations/routes/challenges has a change record
with a reason; that nothing is silently truncated or silently dropped.

NOT guaranteed: that an obligation marked ``supported`` is semantically true.
Every status is a model evaluation (``assessed_by == 'model'``). The validator
never proves semantic sufficiency, runtime behaviour, or absence of defects.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

ASSESSMENT_BASIS = 'model_evaluation; mechanical validator checks ids/snapshot/lines/kinds/format only'

STATUSES = ('unknown', 'supported', 'contradicted', 'conflicted')
VERDICTS = ('supported', 'refuted_in_scope', 'unresolved', 'withdrawn')
REQUIRES = ('name', 'value', 'body', 'runtime')
PROVENANCES = ('historical_seed', 'native_lsp', 'ast_fallback', 'read_lines',
               'grep_literal', 'related_tests', 'runtime_probe', 'public_diff')
CONTENT_KINDS = ('source_lines', 'member_listing', 'reference_edge',
                 'definition_location', 'ast_outline', 'diff_hunk')
RUNTIME_VALIDITIES = ('static_source', 'runtime_observed', 'unknown')
CHALLENGE_STATES = ('pending', 'resolved', 'rejected')
FACT_KINDS = ('declaration', 'callers', 'value', 'import_alias', 'inheritance',
              'test_contract', 'stdlib')

CHANGE_KINDS = (
    'claim_revised', 'scope_revised', 'interpretation_correction',
    'obligation_added', 'obligation_removed', 'obligation_revised', 'required_changed',
    'status_change', 'evidence_changed', 'route_added', 'route_modified', 'route_removed',
    'challenge_added', 'challenge_resolved', 'challenge_revised', 'unresolved_resolved',
    'candidate_withdrawn')
EFFECT_KINDS = ('obligation_added', 'obligation_removed', 'obligation_revised', 'required_changed')
REGRESSION_BASES = ('interpretation_error', 'new_premise', 'evidence_invalidated')
REMOVAL_BASES = ('not_necessary_for_claim', 'interpretation_error', 'scope_revised')
ROUTE_REMOVAL_BASES = ('contradicted', 'interpretation_error', 'scope_revised', 'superseded')
WITHDRAW_BASES = ('refuted_in_scope', 'interpretation_error', 'duplicate')
CHALLENGE_REVISION_BASES = ('interpretation_error', 'irrelevant_to_claim', 'scope_revised')
LIST_FIELDS = ('origin_evidence_ids', 'obligations', 'routes', 'challenges', 'unresolved', 'changes',
               'remove_obligations', 'remove_routes', 'resolve_unresolved')
KEYED_LISTS = ('obligations', 'routes', 'challenges', 'unresolved')
ID_LISTS = ('origin_evidence_ids', 'remove_obligations', 'remove_routes', 'resolve_unresolved')


# ---------------------------------------------------------------- tokens
_ENCODING = None


def count_reference_tokens(text: str) -> int:
    """Reference token count: tiktoken o200k_base. NOT the model's billed usage."""
    global _ENCODING
    if _ENCODING is None:
        import tiktoken
        _ENCODING = tiktoken.get_encoding('o200k_base')
    return len(_ENCODING.encode(text, disallowed_special=()))


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _sha(value: Any) -> str:
    text = value if isinstance(value, str) else _canon(value)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


# ---------------------------------------------------------------- evidence
class EvidenceConflict(ValueError):
    """Same id or same snapshot/file/line with different content."""


class SeedParseError(ValueError):
    pass


@dataclass(frozen=True)
class Evidence:
    """One provided piece of evidence.

    source_lines kind: ``lines`` holds (line_number, text) pairs (possibly several
    non-contiguous runs, as in historical seeds). Other kinds (member listings,
    reference edges, definition locations) carry ``text`` and are never raw source.
    ``provenance`` says where it came from; ``runtime_validity`` says whether it
    certifies runtime behaviour (only a runtime probe can).
    """
    id: str
    snapshot: str
    file: str
    lines: tuple = ()
    text: str = ''
    provenance: str = 'read_lines'
    content_kind: str = 'source_lines'
    runtime_validity: str = 'static_source'
    backend: Optional[str] = None
    anchor_line: Optional[int] = None
    # Revisions this evidence represents when that is more than ``snapshot`` alone. Only a public
    # unified diff (provenance public_diff + content_kind diff_hunk) carries both sides of a change;
    # every other evidence represents exactly its own ``snapshot``.
    snapshots: tuple = ()

    def __post_init__(self):
        if not self.id or not self.snapshot or not self.file:
            raise ValueError('evidence needs id, snapshot, file')
        if self.provenance not in PROVENANCES:
            raise ValueError(f'bad provenance {self.provenance!r}')
        if self.content_kind not in CONTENT_KINDS:
            raise ValueError(f'bad content_kind {self.content_kind!r}')
        if self.runtime_validity not in RUNTIME_VALIDITIES:
            raise ValueError(f'bad runtime_validity {self.runtime_validity!r}')
        if self.runtime_validity == 'runtime_observed' and self.provenance != 'runtime_probe':
            raise ValueError('only a runtime_probe can be runtime_observed; '
                             'static/LSP/AST evidence is not a runtime binding')
        if self.snapshots:
            if self.provenance != 'public_diff' or self.content_kind != 'diff_hunk':
                raise ValueError('only public_diff diff_hunk evidence may represent several snapshots')
            if (not set(self.snapshots) <= {'base', 'head'} or len(set(self.snapshots)) != len(self.snapshots)):
                raise ValueError(f'bad snapshots {self.snapshots!r}')
            object.__setattr__(self, 'snapshots', tuple(s for s in ('base', 'head') if s in self.snapshots))
            if self.snapshot != snapshot_tag(self.snapshots):
                raise ValueError(f'snapshot {self.snapshot!r} must be {snapshot_tag(self.snapshots)!r} for {self.snapshots!r}')
        lines = tuple((int(n), str(t)) for n, t in self.lines)
        numbers = [n for n, _ in lines]
        if self.content_kind == 'source_lines':
            if not lines:
                raise ValueError('source_lines evidence needs lines')
            if self.text:
                raise ValueError('source_lines evidence derives text from lines')
            if numbers != sorted(set(numbers)) or numbers[0] < 1:
                raise ValueError('lines must be unique, ascending, >= 1')
        else:
            if lines:
                raise ValueError(f'{self.content_kind} evidence must not carry raw source lines')
            if not self.text:
                raise ValueError(f'{self.content_kind} evidence needs text')
        object.__setattr__(self, 'lines', lines)

    @property
    def snapshot_set(self) -> tuple:
        """The revisions this evidence may support an obligation about."""
        return self.snapshots or (self.snapshot,)

    def represents(self, snapshot: str) -> bool:
        return snapshot in self.snapshot_set

    @property
    def native(self) -> bool:
        return self.provenance == 'native_lsp'

    @property
    def bears_source(self) -> bool:
        # a diff hunk is the supplied changed code (text, not part of the raw-source union accounting)
        return self.content_kind in ('source_lines', 'diff_hunk')

    @property
    def start_line(self) -> Optional[int]:
        return self.lines[0][0] if self.lines else self.anchor_line

    @property
    def end_line(self) -> Optional[int]:
        return self.lines[-1][0] if self.lines else self.anchor_line

    def runs(self) -> list:
        out: list = []
        for n, _ in self.lines:
            if out and out[-1][1] == n - 1:
                out[-1][1] = n
            else:
                out.append([n, n])
        return [tuple(r) for r in out]

    def fingerprint(self) -> str:
        parts = [self.snapshot, self.file, list(self.lines), self.text, self.content_kind,
                 self.provenance, self.runtime_validity]
        if self.snapshots:                      # appended only when set: other fingerprints are unchanged
            parts.append(list(self.snapshots))
        return _sha(parts)

    def normalized(self) -> dict:
        return {'id': self.id, 'snapshot': self.snapshot, 'snapshots': list(self.snapshot_set), 'file': self.file,
                'start_line': self.start_line, 'end_line': self.end_line,
                'runs': [list(r) for r in self.runs()], 'line_count': len(self.lines),
                'content_kind': self.content_kind, 'provenance': self.provenance,
                'runtime_validity': self.runtime_validity, 'native': self.native,
                'historical': self.provenance == 'historical_seed',
                'backend': self.backend, 'content_sha256': self.fingerprint()}


def snapshot_tag(snapshots) -> str:
    """'head' / 'base' for one revision, 'base+head' for a unified diff that shows both."""
    return '+'.join(s for s in ('base', 'head') if s in snapshots)


_HEADER = re.compile(r'^\[(E-[A-Za-z0-9_.\-]+)\]\s+(head|base)\s+(.+?);')
_NUMBERED = re.compile(r'^(\d+):(?: (.*))?$')
_OMITTED = re.compile(r'^\.\.\. omitted \.\.\.\s*$')


def parse_seed_source(text: str, *, provenance: str = 'historical_seed') -> list:
    """Parse historical seed text: ``[E-head-xxx] head path;`` + ``N: code`` lines.

    ``... omitted ...`` separates runs. Any other non-blank line inside a block is an
    error (never silently ignored). Returns Evidence objects, historical provenance.
    """
    out: list = []
    current: Optional[dict] = None

    def close():
        if current is not None:
            if not current['lines']:
                raise SeedParseError(f"block {current['id']} has no numbered lines")
            out.append(Evidence(id=current['id'], snapshot=current['snapshot'],
                                file=current['file'], lines=tuple(sorted(current['lines'].items())),
                                provenance=provenance))
    for raw in text.splitlines():
        header = _HEADER.match(raw)
        if header:
            close()
            current = {'id': header.group(1), 'snapshot': header.group(2),
                       'file': header.group(3).strip(), 'lines': {}}
            continue
        if current is None:
            if raw.strip():
                raise SeedParseError(f'text before first evidence header: {raw[:60]!r}')
            continue
        if _OMITTED.match(raw) or not raw.strip() and not _NUMBERED.match(raw):
            continue
        numbered = _NUMBERED.match(raw)
        if not numbered:
            raise SeedParseError(f"unparseable line in {current['id']}: {raw[:60]!r}")
        number, code = int(numbered.group(1)), numbered.group(2) or ''
        if number in current['lines'] and current['lines'][number] != code:
            raise EvidenceConflict(f"{current['id']} line {number} repeated with different text")
        current['lines'][number] = code
    close()
    ids = [e.id for e in out]
    if len(ids) != len(set(ids)):
        raise SeedParseError('duplicate evidence block ids in seed')
    return out


def normalized_entries(evidence: Iterable[Evidence]) -> list:
    """Parser-independent entries (id/snapshot/file/runs/hash) for audit and adapters."""
    return [e.normalized() for e in evidence]


class EvidenceStore:
    """Evidence by id plus a snapshot+file+line union with consistency checking."""

    def __init__(self):
        self.by_id: dict = {}
        self._lines: dict = {}      # (snapshot, file) -> {line: text}

    def __contains__(self, evidence_id):
        return evidence_id in self.by_id

    def get(self, evidence_id) -> Evidence:
        return self.by_id[evidence_id]

    def check(self, evidence: Iterable[Evidence]) -> None:
        """Raise EvidenceConflict if adding would be inconsistent. Does not mutate."""
        pending_lines: dict = {}
        pending_ids: dict = {}
        for ev in evidence:
            known = self.by_id.get(ev.id) or pending_ids.get(ev.id)
            if known is not None and known.fingerprint() != ev.fingerprint():
                raise EvidenceConflict(f'evidence id {ev.id} reused with different content')
            pending_ids[ev.id] = ev
            for n, t in ev.lines:
                key = (ev.snapshot, ev.file)
                prior = self._lines.get(key, {}).get(n)
                if prior is None:
                    prior = pending_lines.get((key, n))
                if prior is not None and prior != t:
                    raise EvidenceConflict(f'{ev.snapshot}:{ev.file}:{n} has two different texts')
                pending_lines[(key, n)] = t

    def add(self, ev: Evidence) -> int:
        """Add; returns number of newly covered source lines."""
        self.check([ev])
        self.by_id[ev.id] = ev
        added = 0
        for n, t in ev.lines:
            bucket = self._lines.setdefault((ev.snapshot, ev.file), {})
            if n not in bucket:
                bucket[n] = t
                added += 1
        return added

    def covers(self, snapshot: str, file: str, start: int, end: int) -> list:
        """Ids of stored evidence jointly covering [start,end]; [] when not fully covered."""
        bucket = self._lines.get((snapshot, file), {})
        if not all(n in bucket for n in range(start, end + 1)):
            return []
        return sorted(e.id for e in self.by_id.values() if e.snapshot == snapshot and e.file == file
                      and any(start <= n <= end for n, _ in e.lines))

    def segments(self) -> list:
        """Union as (snapshot, file, start, end, text) contiguous segments, stable order."""
        out = []
        for (snapshot, file) in sorted(self._lines):
            bucket = self._lines[(snapshot, file)]
            run: list = []
            for n in sorted(bucket):
                if run and run[-1] != n - 1:
                    out.append(self._segment(snapshot, file, run, bucket))
                    run = []
                run.append(n)
            if run:
                out.append(self._segment(snapshot, file, run, bucket))
        return out

    @staticmethod
    def _segment(snapshot, file, run, bucket):
        return (snapshot, file, run[0], run[-1], '\n'.join(bucket[n] for n in run))

    def union_tokens(self, counter: Callable[[str], int] = count_reference_tokens) -> int:
        return sum(counter(seg[4]) for seg in self.segments())

    def union_line_count(self) -> int:
        return sum(len(b) for b in self._lines.values())

    def union_hash(self) -> str:
        return _sha([[s, f, n, t] for (s, f), b in sorted(self._lines.items())
                     for n, t in sorted(b.items())])


# ---------------------------------------------------------------- budgets / packing
@dataclass
class Budgets:
    source_reference_tokens: int = 5200
    metadata_tokens_per_call: int = 800
    state_tokens_per_call: int = 1600
    max_queries_total: int = 6
    max_queries_per_round: int = 3
    max_rounds: int = 2


@dataclass(frozen=True)
class UnitPart:
    role: str            # e.g. 'condition', 'value', 'body'
    evidence: Evidence


@dataclass(frozen=True)
class EvidenceUnit:
    """A semantic unit admitted atomically: all parts or none."""
    id: str
    parts: tuple
    required_roles: tuple = ()
    critical: bool = False


@dataclass
class AdmitResult:
    unit_id: str
    admitted: bool
    blocked_by: Optional[str] = None
    marginal_tokens: int = 0
    detail: str = ''


class ProtectedEvidence(RuntimeError):
    pass


class EvidenceAccount:
    """Cumulative exposure accounting. Each cap is logged separately.

    ``exposed union`` only grows: evicting from the packet never refunds exposure, so
    swapping packets cannot bypass the source cap. Seeds are admitted unconditionally
    and protected; if they alone exceed the cap that is logged, not truncated.
    """

    def __init__(self, budgets: Optional[Budgets] = None,
                 counter: Callable[[str], int] = count_reference_tokens):
        self.budgets = budgets or Budgets()
        self.counter = counter
        self.store = EvidenceStore()
        self.packet: list = []            # ordered evidence ids currently in the packet
        self.protected: set = set()
        self.seed_ids: set = set()
        self.incomplete_units: set = set()
        self.log: list = []
        self.round = 0
        self.queries_total = 0
        self.queries_this_round = 0

    # -- logging
    def _log(self, cap: str, **fields):
        entry = {'cap': cap, 'round': self.round, **fields}
        self.log.append(entry)
        return entry

    # -- seeds
    def add_seed(self, evidence: Iterable[Evidence]) -> None:
        evidence = list(evidence)
        self.store.check(evidence)
        for ev in evidence:
            self.store.add(ev)
            self.seed_ids.add(ev.id)
            self.protected.add(ev.id)
            if ev.id not in self.packet:
                self.packet.append(ev.id)
        tokens = self.exposed_tokens()
        if tokens > self.budgets.source_reference_tokens:
            self._log('source_cap', event='seed_exceeds_cap', used=tokens,
                      limit=self.budgets.source_reference_tokens, admitted=True)

    # -- source
    def exposed_tokens(self) -> int:
        return self.store.union_tokens(self.counter)

    def admit_unit(self, unit: EvidenceUnit, *, allow_incomplete: bool = False) -> AdmitResult:
        roles = {p.role for p in unit.parts}
        missing = [r for r in unit.required_roles if r not in roles]
        if missing and not allow_incomplete:
            self._log('unit', unit=unit.id, blocked_by='incomplete_unit', missing=missing)
            return AdmitResult(unit.id, False, 'incomplete_unit', detail=f'missing roles {missing}')
        evidence = [p.evidence for p in unit.parts]
        try:
            self.store.check(evidence)
        except EvidenceConflict as exc:
            self._log('consistency', unit=unit.id, blocked_by='inconsistent_source', detail=str(exc))
            return AdmitResult(unit.id, False, 'inconsistent_source', detail=str(exc))
        before = self.exposed_tokens()
        trial = EvidenceStore()
        trial.by_id, trial._lines = dict(self.store.by_id), {k: dict(v) for k, v in self.store._lines.items()}
        for ev in evidence:
            trial.add(ev)
        marginal = trial.union_tokens(self.counter) - before
        limit = self.budgets.source_reference_tokens
        if before + marginal > limit:
            self._log('source_cap', unit=unit.id, used=before, requested=marginal, limit=limit,
                      admitted=False)
            return AdmitResult(unit.id, False, 'budget_blocked', marginal,
                               f'source cap {before}+{marginal}>{limit}')
        for ev in evidence:
            self.store.add(ev)
            if ev.id not in self.packet:
                self.packet.append(ev.id)
            if unit.critical:
                self.protected.add(ev.id)
        if missing:
            self.incomplete_units.add(unit.id)
        self._log('source_cap', unit=unit.id, used=before, requested=marginal, limit=limit,
                  admitted=True, incomplete=bool(missing))
        return AdmitResult(unit.id, True, None, marginal)

    def protect(self, evidence_ids: Iterable[str]) -> None:
        for eid in evidence_ids:
            if eid not in self.store:
                raise KeyError(eid)
            self.protected.add(eid)

    def evict(self, evidence_ids: Iterable[str]) -> list:
        """Remove from the packet only. Protected evidence raises; exposure is not refunded."""
        ids = list(evidence_ids)
        blocked = [i for i in ids if i in self.protected]
        if blocked:
            self._log('evict', blocked_by='protected', ids=blocked)
            raise ProtectedEvidence(f'protected evidence cannot be evicted: {blocked}')
        self.packet = [i for i in self.packet if i not in set(ids)]
        return ids

    def packet_evidence(self) -> list:
        return [self.store.get(i) for i in self.packet]

    # -- metadata / state / queries
    def charge_metadata(self, call_id: str, text: str) -> bool:
        tokens = self.counter(text)
        limit = self.budgets.metadata_tokens_per_call
        ok = tokens <= limit
        self._log('metadata_cap', call=call_id, used=tokens, limit=limit, admitted=ok)
        return ok

    def check_state_tokens(self, tokens: int, label: str = 'state') -> bool:
        limit = self.budgets.state_tokens_per_call
        ok = tokens <= limit
        self._log('state_cap', label=label, used=tokens, limit=limit, admitted=ok)
        return ok

    def begin_round(self) -> bool:
        if self.round >= self.budgets.max_rounds:
            self._log('round_cap', blocked_by='max_rounds', limit=self.budgets.max_rounds)
            return False
        self.round += 1
        self.queries_this_round = 0
        return True

    def query_allowance(self) -> int:
        if self.round == 0 or self.round > self.budgets.max_rounds:
            return 0
        return max(0, min(self.budgets.max_queries_per_round - self.queries_this_round,
                          self.budgets.max_queries_total - self.queries_total))

    def charge_queries(self, requests: Iterable[Mapping]) -> int:
        n = 0
        for req in requests:
            if self.query_allowance() <= 0:
                self._log('query_cap', request=req.get('id'), blocked_by='query_cap', admitted=False,
                          total=self.queries_total, per_round=self.queries_this_round)
                break
            self.queries_total += 1
            self.queries_this_round += 1
            n += 1
            self._log('query_cap', request=req.get('id'), admitted=True, total=self.queries_total,
                      per_round=self.queries_this_round)
        return n

    def summary(self) -> dict:
        caps: dict = {}
        for e in self.log:
            c = caps.setdefault(e['cap'], {'events': 0, 'blocked': 0})
            c['events'] += 1
            c['blocked'] += e.get('admitted') is False or 'blocked_by' in e
        return {'exposed_reference_tokens': self.exposed_tokens(),
                'source_cap': self.budgets.source_reference_tokens,
                'union_lines': self.store.union_line_count(), 'union_sha256': self.store.union_hash(),
                'queries_used': self.queries_total, 'rounds': self.round,
                'packet_ids': list(self.packet), 'protected_ids': sorted(self.protected),
                'caps': caps, 'tokenizer': 'tiktoken:o200k_base (reference; not billed usage)'}


# ---------------------------------------------------------------- ledger model
@dataclass
class Obligation:
    id: str
    predicate: str
    required: bool = True
    requires: str = 'name'
    fact_kind: Optional[str] = None
    status: str = 'unknown'
    evidence_ids: list = field(default_factory=list)
    depends_on: list = field(default_factory=list)
    snapshot: Optional[str] = None
    assessed_by: str = 'model'


@dataclass
class SupportRoute:
    id: str
    obligation_ids: list = field(default_factory=list)


@dataclass
class Challenge:
    id: str
    text: str
    material: bool = True
    state: str = 'pending'
    evidence_ids: list = field(default_factory=list)
    target: Optional[str] = None     # obligation id, route id, or None for candidate-wide


@dataclass
class Candidate:
    id: str
    claim: str
    scope: str
    origin: str
    origin_evidence_ids: list = field(default_factory=list)
    obligations: dict = field(default_factory=dict)
    routes: dict = field(default_factory=dict)
    challenges: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)       # id -> text (unresolved facts)
    withdrawn: Optional[dict] = None

    # ---- derived (never stored) ----
    def effective_status(self, oid: str, _seen=None) -> str:
        ob = self.obligations[oid]
        if ob.status != 'supported':
            return ob.status
        seen = (_seen or set()) | {oid}
        for dep in ob.depends_on:
            if dep in seen or dep not in self.obligations:
                return 'unknown'
            if self.effective_status(dep, seen) != 'supported':
                return 'unknown'
        return 'supported'

    def route_state(self, route: SupportRoute) -> str:
        required = [o for o in route.obligation_ids if self.obligations[o].required]
        if not required:
            return 'unknown'            # an empty route is never vacuously supported
        states = [self.effective_status(o) for o in required]
        if 'contradicted' in states:
            return 'contradicted'
        if 'conflicted' in states:
            return 'conflicted'
        return 'supported' if all(s == 'supported' for s in states) else 'unknown'

    def route_challenged(self, route: SupportRoute) -> list:
        return sorted(c.id for c in self.challenges.values()
                      if c.material and c.state == 'pending'
                      and (c.target is None or c.target == route.id or c.target in route.obligation_ids))

    def verdict(self) -> str:
        if self.withdrawn:
            return 'withdrawn'
        states = [(r, self.route_state(r)) for r in self.routes.values()]
        if any(s == 'supported' and not self.route_challenged(r) for r, s in states):
            return 'supported'
        if states and all(s == 'contradicted' for _, s in states):
            return 'refuted_in_scope'      # all KNOWN routes dead, in the declared scope only
        return 'unresolved'

    def open_gaps(self) -> dict:
        """Mechanically derived: cannot be hidden by the model's wording."""
        gaps = sorted(o.id for o in self.obligations.values()
                      if o.required and self.effective_status(o.id) in ('unknown', 'conflicted'))
        return {'obligations': gaps,
                'challenges': sorted(c.id for c in self.challenges.values()
                                     if c.material and c.state == 'pending'),
                'notes': sorted(self.notes)}

    def to_dict(self) -> dict:
        return {'id': self.id, 'claim': self.claim, 'scope': self.scope, 'origin': self.origin,
                'origin_evidence_ids': list(self.origin_evidence_ids),
                'obligations': [vars(o).copy() for _, o in sorted(self.obligations.items())],
                'routes': [{'id': r.id, 'obligation_ids': list(r.obligation_ids)}
                           for _, r in sorted(self.routes.items())],
                'challenges': [vars(c).copy() for _, c in sorted(self.challenges.items())],
                'unresolved': [{'id': k, 'text': v} for k, v in sorted(self.notes.items())],
                'withdrawn': self.withdrawn, 'verdict': self.verdict(),
                'route_states': {r.id: self.route_state(r) for r in self.routes.values()},
                'open_gaps': self.open_gaps()}


@dataclass
class Transition:
    seq: int
    round: int
    candidate_id: str
    kind: str
    target_id: str
    old: Any
    new: Any
    reason: str
    source_refs: list
    flags: list = field(default_factory=list)

    def to_dict(self):
        return vars(self).copy()


@dataclass
class StateCaps:
    max_candidates: int = 12
    max_obligations_per_candidate: int = 10
    max_routes_per_candidate: int = 4
    max_challenges_per_candidate: int = 6
    max_notes_per_candidate: int = 8


@dataclass
class UpdateResult:
    accepted: bool
    errors: list
    warnings: list
    transitions: list
    state_sha256_before: str
    state_sha256_after: str


@dataclass
class StateRender:
    text: Optional[str]
    tokens: int
    cap: int
    tier: int
    compacted_fields: list
    blocked: bool


def _err(code, path, message):
    return {'code': code, 'path': path, 'message': message}


def _str_list(value, path, errors, allow_empty=True) -> Optional[list]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        errors.append(_err('bad_type', path, 'expected list of non-empty strings'))
        return None
    if not allow_empty and not value:
        errors.append(_err('empty', path, 'must be non-empty'))
    return value


class Ledger:
    """Candidate ledger with validated, reason-bearing, atomic updates."""

    def __init__(self, store: EvidenceStore, caps: Optional[StateCaps] = None,
                 snapshots: Iterable[str] = ('head', 'base')):
        self.store = store
        self.caps = caps or StateCaps()
        self.snapshots = set(snapshots)
        self.candidates: dict = {}
        self.transitions: list = []
        self.rejections: list = []
        self.cap_log: list = []
        self._seq = 0

    # ------------------------------------------------------------ export
    def export(self) -> dict:
        return {'assessment_basis': ASSESSMENT_BASIS,
                'candidates': [c.to_dict() for _, c in sorted(self.candidates.items())]}

    def state_hash(self) -> str:
        return _sha(self.export())

    def verdicts(self) -> dict:
        return {cid: c.verdict() for cid, c in sorted(self.candidates.items())}

    # ------------------------------------------------------------ update
    def apply_update(self, payload: Any, *, round_index: int,
                     provided_evidence_ids: Iterable[str]) -> UpdateResult:
        provided = set(provided_evidence_ids)
        before = self.state_hash()
        errors: list = []
        warnings: list = []
        working = copy.deepcopy(self.candidates)
        transitions: list = []
        pending: list = []        # (candidate_before, candidate_after, records)

        if not isinstance(payload, dict) or not isinstance(payload.get('candidates'), list):
            errors.append(_err('bad_payload', '$', "payload must be an object with a 'candidates' list"))
        else:
            self._check_refs(payload.get('evidence_refs', []), errors)
            seen_ids: set = set()
            for i, item in enumerate(payload['candidates']):
                path = f'$.candidates[{i}]'
                if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
                    errors.append(_err('bad_candidate', path, 'candidate needs a string id'))
                    continue
                if item['id'] in seen_ids:
                    errors.append(_err('duplicate_candidate', path, f"{item['id']} listed twice"))
                    continue
                seen_ids.add(item['id'])
                old = self.candidates.get(item['id'])
                n0 = len(errors)
                self._check_shapes(item, path, errors)
                if len(errors) > n0:
                    continue
                try:
                    new, records = self._merge(old, item, path, errors)
                except (TypeError, AttributeError, KeyError, ValueError) as exc:   # backstop: never escape atomically
                    errors.append(_err('malformed_candidate', path, f'{type(exc).__name__}: {exc}'))
                    continue
                if new is not None:
                    working[new.id] = new
                    pending.append((old, new, records, item))
        if not errors:
            for old, new, records, item in pending:
                try:
                    self._validate_candidate(old, new, records, item, provided, round_index,
                                             errors, warnings, transitions)
                except (TypeError, AttributeError, KeyError, ValueError) as exc:
                    errors.append(_err('malformed_candidate', f'$.candidate[{new.id}]',
                                       f'{type(exc).__name__}: {exc}'))
            self._check_caps(working, errors)
            mentioned = {new.id for _, new, _, _ in pending}
            for cid in sorted(self.candidates):
                if cid not in mentioned:
                    transitions.append(self._transition(round_index, cid, 'retained_unmentioned', cid,
                        self.candidates[cid].verdict(), self.candidates[cid].verdict(),
                        'candidate absent from response; retained unchanged (omission is not withdrawal)',
                        [], []))
        if errors:
            self.rejections.append({'round': round_index, 'errors': errors,
                                    'state_sha256': before})
            return UpdateResult(False, errors, warnings, [], before, before)
        self.candidates = working
        for t in transitions:
            self._seq += 1
            t.seq = self._seq
        self.transitions.extend(transitions)
        return UpdateResult(True, [], warnings, transitions, before, self.state_hash())

    def final_report(self, last: Optional[UpdateResult]) -> dict:
        """An invalid last update is an operational failure, never a fallback success."""
        if last is not None and not last.accepted:
            return {'status': 'unresolved', 'operational_failure': True,
                    'reason': 'invalid_final_update', 'errors': last.errors,
                    'reportable': [], 'assessment_basis': ASSESSMENT_BASIS,
                    'prior_state_for_audit': self.export()}
        verdicts = self.verdicts()
        return {'status': 'ok', 'operational_failure': False,
                'reportable': [c for c, v in verdicts.items() if v == 'supported'],
                'refuted_in_scope': [c for c, v in verdicts.items() if v == 'refuted_in_scope'],
                'unresolved': [c for c, v in verdicts.items() if v == 'unresolved'],
                'withdrawn': [c for c, v in verdicts.items() if v == 'withdrawn'],
                'scope_note': 'refuted_in_scope refers to known routes in the declared scope only',
                'assessment_basis': ASSESSMENT_BASIS}

    # ------------------------------------------------------------ rendering
    def render_state(self, token_cap: int,
                     counter: Callable[[str], int] = count_reference_tokens) -> StateRender:
        """Render for a prompt. Never truncates: compacts named fields, else blocks."""
        def build(tier: int):
            # Obligation meaning (predicate, requires, snapshot, evidence ids, dependency ids) is
            # never compacted. Tier 1 only drops presentation fields that are derivable or redundant.
            rows, compacted = [], []
            for _, c in sorted(self.candidates.items()):
                obs = []
                for _, o in sorted(c.obligations.items()):
                    row = {'id': o.id, 'predicate': o.predicate, 'status': o.status, 'required': o.required,
                           'requires': o.requires, 'snapshot': o.snapshot, 'evidence_ids': o.evidence_ids,
                           'depends_on': o.depends_on}
                    if tier == 0:
                        row['fact_kind'] = o.fact_kind
                    elif o.fact_kind is not None:
                        compacted.append(f'{c.id}.{o.id}.fact_kind')
                    obs.append(row)
                row = {'id': c.id, 'verdict': c.verdict(), 'claim': c.claim, 'scope': c.scope,
                       'obligations': obs,
                       'routes': {r.id: r.obligation_ids for r in c.routes.values()},
                       'challenges': [vars(x) for x in c.challenges.values()
                                      if x.state == 'pending' or tier == 0],
                       'unresolved': dict(c.notes)}
                if tier == 0:
                    row['open_gaps'] = c.open_gaps()
                else:
                    compacted.append(f'{c.id}.open_gaps')
                    compacted.extend(f'{c.id}.challenge.{x.id}(closed)' for x in c.challenges.values()
                                     if x.state != 'pending')
                rows.append(row)
            return json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':')), compacted
        for tier in (0, 1):
            text, compacted = build(tier)
            tokens = counter(text)
            if tokens <= token_cap:
                if tier == 1:
                    self.cap_log.append({'cap': 'state_cap', 'event': 'compacted', 'tokens': tokens,
                                         'limit': token_cap, 'fields': compacted})
                return StateRender(text, tokens, token_cap, tier, compacted, False)
        self.cap_log.append({'cap': 'state_cap', 'event': 'blocked', 'tokens': tokens, 'limit': token_cap})
        return StateRender(None, tokens, token_cap, 1, compacted, True)

    # ------------------------------------------------------------ internals
    def _check_refs(self, refs, errors):
        if not isinstance(refs, list):
            errors.append(_err('bad_type', '$.evidence_refs', 'expected list'))
            return
        for i, ref in enumerate(refs):
            path = f'$.evidence_refs[{i}]'
            if not isinstance(ref, dict) or not isinstance(ref.get('id'), str) or ref['id'] not in self.store:
                errors.append(_err('unknown_evidence_id', path, f'evidence id {ref.get("id") if isinstance(ref, dict) else ref!r} was never provided'))
                continue
            ev = self.store.get(ref['id'])
            actual = {'snapshot': ev.snapshot, 'file': ev.file,
                      'start_line': ev.start_line, 'end_line': ev.end_line}
            for key, value in actual.items():
                if key == 'snapshot' and ref.get(key) in ev.snapshot_set:
                    continue                    # a diff represents each of its revisions
                if key in ref and ref[key] != value:
                    code = 'wrong_snapshot' if key == 'snapshot' else 'evidence_ref_mismatch'
                    errors.append(_err(code, path, f'{ref["id"]}.{key} is {value!r}, response says {ref[key]!r}'))

    @staticmethod
    def _check_shapes(item: dict, path: str, errors: list) -> None:
        """Collection shapes and duplicate ids, checked before anything is merged."""
        for key in LIST_FIELDS:
            if key in item and not isinstance(item[key], list):
                errors.append(_err('bad_type', f'{path}.{key}', f'expected a list, got {type(item[key]).__name__}'))
        for key in KEYED_LISTS:
            seen: set = set()
            for j, entry in enumerate(item[key] if isinstance(item.get(key), list) else []):
                eid = entry.get('id') if isinstance(entry, dict) else None
                if not isinstance(eid, str) or not eid:
                    errors.append(_err('bad_type', f'{path}.{key}[{j}]', 'expected an object with a string id'))
                elif eid in seen:
                    errors.append(_err('duplicate_id', f'{path}.{key}[{j}]', f'{eid} listed twice in {key}'))
                else:
                    seen.add(eid)
        for key in ID_LISTS:
            values = item.get(key)
            if isinstance(values, list) and all(isinstance(v, str) and v for v in values):
                if len(values) != len(set(values)):
                    errors.append(_err('duplicate_id', f'{path}.{key}', f'duplicate ids in {key}'))
            elif isinstance(values, list):
                errors.append(_err('bad_type', f'{path}.{key}', 'expected list of non-empty strings'))
        if 'withdraw' in item and not isinstance(item['withdraw'], bool):
            errors.append(_err('bad_type', f'{path}.withdraw', 'expected boolean'))
        if item.get('asserted_verdict') is not None and not isinstance(item['asserted_verdict'], str):
            errors.append(_err('bad_type', f'{path}.asserted_verdict', 'expected string'))

    def _merge(self, old: Optional[Candidate], item: dict, path: str, errors: list):
        n0 = len(errors)
        cid = item['id']
        if old is None:
            for key in ('claim', 'scope', 'origin'):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    errors.append(_err('missing_field', f'{path}.{key}', f'new candidate {cid} needs {key}'))
            ids = _str_list(item.get('origin_evidence_ids', []), f'{path}.origin_evidence_ids', errors)
            if len(errors) > n0:
                return None, []
            new = Candidate(cid, item['claim'], item['scope'], item['origin'], list(ids))
        else:
            new = copy.deepcopy(old)
            for key in ('claim', 'scope'):
                if key in item:
                    if not isinstance(item[key], str) or not item[key].strip():
                        errors.append(_err('bad_type', f'{path}.{key}', 'expected non-empty string'))
                    else:
                        setattr(new, key, item[key])
        records = item.get('changes', [])
        if not isinstance(records, list):
            errors.append(_err('bad_type', f'{path}.changes', 'expected list'))
            records = []
        for j, rec in enumerate(records):
            self._check_record(rec, f'{path}.changes[{j}]', errors)
        for j, o in enumerate(item.get('obligations', [])):
            self._merge_obligation(new, o, f'{path}.obligations[{j}]', errors)
        for j, r in enumerate(item.get('routes', [])):
            if not isinstance(r, dict) or not isinstance(r.get('id'), str):
                errors.append(_err('bad_type', f'{path}.routes[{j}]', 'route needs id'))
                continue
            ids = _str_list(r.get('obligation_ids'), f'{path}.routes[{j}].obligation_ids', errors)
            if ids is not None:
                new.routes[r['id']] = SupportRoute(r['id'], list(ids))
        for j, c in enumerate(item.get('challenges', [])):
            self._merge_challenge(new, c, f'{path}.challenges[{j}]', errors)
        for j, n in enumerate(item.get('unresolved', [])):
            if not isinstance(n, dict) or not isinstance(n.get('id'), str) or not isinstance(n.get('text'), str):
                errors.append(_err('bad_type', f'{path}.unresolved[{j}]', 'note needs id and text'))
            elif n['id'] not in new.notes:
                new.notes[n['id']] = n['text']
        for key, store in (('remove_obligations', new.obligations), ('remove_routes', new.routes),
                           ('resolve_unresolved', new.notes)):
            ids = item.get(key, [])
            if _str_list(ids, f'{path}.{key}', errors) is None:
                continue
            for i in ids:
                if i not in store:
                    errors.append(_err('unknown_id', f'{path}.{key}', f'{i} does not exist'))
                else:
                    del store[i]
        if item.get('withdraw') and not new.withdrawn:
            new.withdrawn = {'round': None}
        return (new if len(errors) == n0 else None), records

    def _check_record(self, rec, path, errors):
        if not isinstance(rec, dict) or rec.get('kind') not in CHANGE_KINDS:
            errors.append(_err('bad_change_record', path, f'kind must be one of {CHANGE_KINDS}'))
            return
        if not isinstance(rec.get('reason'), str) or not rec['reason'].strip():
            errors.append(_err('missing_reason', path, 'every change needs a non-empty reason'))
        ids = rec.get('affected_ids')
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            errors.append(_err('missing_affected_ids', path, 'affected_ids must list the changed ids'))
        for key in ('basis', 'superseded_by'):
            if key in rec and not (isinstance(rec[key], str) and rec[key]):
                errors.append(_err('bad_type', f'{path}.{key}', f'{key} must be a non-empty string'))
        if 'evidence_ids' in rec:       # may be empty (e.g. an interpretation correction)
            _str_list(rec['evidence_ids'], f'{path}.evidence_ids', errors)
        if rec['kind'] in EFFECT_KINDS:
            for key in ('claim_effect_if_true', 'claim_effect_if_false'):
                if not isinstance(rec.get(key), str) or not rec[key].strip():
                    errors.append(_err('missing_effect', path,
                        f'{rec["kind"]} must state {key} (how the claim differs if the fact is true/false)'))

    def _merge_obligation(self, cand: Candidate, o, path, errors):
        if not isinstance(o, dict) or not isinstance(o.get('id'), str) or not o['id']:
            errors.append(_err('bad_type', path, 'obligation needs id'))
            return
        ob = cand.obligations.get(o['id'])
        if ob is None:
            if not isinstance(o.get('predicate'), str) or not o['predicate'].strip():
                errors.append(_err('missing_field', f'{path}.predicate', 'new obligation needs predicate'))
                return
            ob = Obligation(o['id'], o['predicate'])
        for key, kind in (('predicate', str), ('required', bool), ('requires', str), ('fact_kind', (str, type(None))),
                          ('status', str), ('snapshot', (str, type(None)))):
            if key in o:
                if not isinstance(o[key], kind) or (key == 'predicate' and not o[key].strip()):
                    errors.append(_err('bad_type', f'{path}.{key}', f'bad {key}'))
                else:
                    setattr(ob, key, o[key])
        if ob.status not in STATUSES:
            errors.append(_err('bad_status', f'{path}.status', f'status must be one of {STATUSES}'))
        if ob.requires not in REQUIRES:
            errors.append(_err('bad_requires', f'{path}.requires', f'requires must be one of {REQUIRES}'))
        if ob.fact_kind is not None and ob.fact_kind not in FACT_KINDS:
            errors.append(_err('bad_fact_kind', f'{path}.fact_kind', f'fact_kind must be one of {FACT_KINDS}'))
        if ob.snapshot is not None and ob.snapshot not in self.snapshots:
            errors.append(_err('wrong_snapshot', f'{path}.snapshot', f'unknown snapshot {ob.snapshot!r}'))
        for key in ('evidence_ids', 'depends_on'):
            if key in o:
                ids = _str_list(o[key], f'{path}.{key}', errors)
                if ids is not None:
                    setattr(ob, key, list(ids))
        if 'assessed_by' in o and o['assessed_by'] != 'model':
            errors.append(_err('bad_assessor', f'{path}.assessed_by', 'statuses are model evaluations'))
        cand.obligations[ob.id] = ob

    def _merge_challenge(self, cand: Candidate, c, path, errors):
        if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not c['id']:
            errors.append(_err('bad_type', path, 'challenge needs id'))
            return
        ch = cand.challenges.get(c['id'])
        if ch is None:
            if not isinstance(c.get('text'), str) or not c['text'].strip():
                errors.append(_err('missing_field', f'{path}.text', 'new challenge needs text'))
                return
            ch = Challenge(c['id'], c['text'])
        if 'text' in c:
            if not isinstance(c['text'], str) or not c['text'].strip():
                errors.append(_err('bad_type', f'{path}.text', 'expected non-empty string'))
            else:
                ch.text = c['text']
        for key, kind in (('material', bool), ('state', str), ('target', (str, type(None)))):
            if key in c:
                if not isinstance(c[key], kind):
                    errors.append(_err('bad_type', f'{path}.{key}', f'bad {key}'))
                else:
                    setattr(ch, key, c[key])
        if ch.state not in CHALLENGE_STATES:
            errors.append(_err('bad_state', f'{path}.state', f'state must be one of {CHALLENGE_STATES}'))
        if 'evidence_ids' in c:
            ids = _str_list(c['evidence_ids'], f'{path}.evidence_ids', errors)
            if ids is not None:
                ch.evidence_ids = list(ids)
        cand.challenges[ch.id] = ch

    # ---- diff + validation
    def _diffs(self, old: Optional[Candidate], new: Candidate) -> list:
        """(kind, target_id, old_value, new_value, accepted_record_kinds)"""
        out = []
        if old is None:
            return out
        if old.claim != new.claim:
            out.append(('claim', new.id, old.claim, new.claim, ('claim_revised', 'interpretation_correction')))
        if old.scope != new.scope:
            out.append(('scope', new.id, old.scope, new.scope, ('scope_revised', 'interpretation_correction')))
        for oid in sorted(set(old.obligations) | set(new.obligations)):
            a, b = old.obligations.get(oid), new.obligations.get(oid)
            if a is None:
                out.append(('obligation_added', oid, None, vars(b).copy(), ('obligation_added',)))
            elif b is None:
                out.append(('obligation_removed', oid, vars(a).copy(), None, ('obligation_removed',)))
            else:
                if a.required != b.required:
                    out.append(('required', oid, a.required, b.required, ('required_changed',)))
                for key in ('predicate', 'requires', 'fact_kind', 'snapshot', 'depends_on'):
                    if getattr(a, key) != getattr(b, key):
                        out.append(('obligation_' + key, oid, getattr(a, key), getattr(b, key),
                                    ('obligation_revised', 'interpretation_correction')))
                if a.status != b.status:
                    out.append(('status', oid, a.status, b.status,
                                ('status_change', 'interpretation_correction')))
                if a.evidence_ids != b.evidence_ids:
                    out.append(('evidence', oid, list(a.evidence_ids), list(b.evidence_ids),
                                ('status_change', 'evidence_changed', 'interpretation_correction')))
        for rid in sorted(set(old.routes) | set(new.routes)):
            a, b = old.routes.get(rid), new.routes.get(rid)
            if a is None:
                out.append(('route_added', rid, None, list(b.obligation_ids), ('route_added',)))
            elif b is None:
                out.append(('route_removed', rid, list(a.obligation_ids), None, ('route_removed',)))
            elif a.obligation_ids != b.obligation_ids:
                out.append(('route_modified', rid, list(a.obligation_ids), list(b.obligation_ids),
                            ('route_modified',)))
        for cid in sorted(set(old.challenges) | set(new.challenges)):
            a, b = old.challenges.get(cid), new.challenges.get(cid)
            if a is None:
                out.append(('challenge_added', cid, None, vars(b).copy(), ('challenge_added',)))
            elif b is None:
                pass  # challenges are never deleted by merge semantics
            else:
                if (a.state, a.evidence_ids) != (b.state, b.evidence_ids):
                    out.append(('challenge_changed', cid, vars(a).copy(), vars(b).copy(),
                                ('challenge_resolved', 'interpretation_correction')))
                if (a.text, a.target, a.material) != (b.text, b.target, b.material):
                    out.append(('challenge_revised', cid, vars(a).copy(), vars(b).copy(),
                                ('challenge_revised', 'interpretation_correction')))
        for nid in sorted(set(old.notes) - set(new.notes)):
            out.append(('note_resolved', nid, old.notes[nid], None, ('unresolved_resolved',)))
        if new.withdrawn and not old.withdrawn:
            out.append(('withdrawn', new.id, None, {'withdrawn': True}, ('candidate_withdrawn',)))
        return out

    def _validate_candidate(self, old, new, records, item, provided, round_index,
                            errors, warnings, transitions):
        cid, base = new.id, f'$.candidate[{new.id}]'
        n0 = len(errors)
        # structural invariants
        for rid, route in new.routes.items():
            for oid in route.obligation_ids:
                if oid not in new.obligations:
                    errors.append(_err('dangling_route', base, f'route {rid} references missing obligation {oid}'))
        for oid, ob in new.obligations.items():
            for dep in ob.depends_on:
                if dep not in new.obligations or dep == oid:
                    errors.append(_err('bad_dependency', base, f'{oid} depends on invalid {dep}'))
        for chid, ch in new.challenges.items():
            if ch.target is not None and ch.target not in new.obligations and ch.target not in new.routes:
                errors.append(_err('bad_challenge_target', base, f'challenge {chid} targets unknown {ch.target}'))
        if old is None:
            for eid in new.origin_evidence_ids:
                self._check_evidence_id(eid, eid in provided or eid in self.store, provided, base, errors, new_id=True)
        cited = set(old.origin_evidence_ids) if old else set()
        for coll in ((old.obligations, old.challenges) if old else ()):
            for x in coll.values():
                cited.update(x.evidence_ids)
        for j, r in enumerate(records):             # change-record evidence must be real and exposed
            for eid in r.get('evidence_ids') or []:
                self._check_evidence_id(eid, True, None if eid in cited else provided,
                                        f'{base}.changes[{j}].evidence_ids', errors)
        # evidence discipline for every obligation/challenge touched
        for oid, ob in new.obligations.items():
            prior = old.obligations.get(oid) if old else None
            self._check_obligation_evidence(ob, prior, provided, base, errors)
        for chid, ch in new.challenges.items():
            prior = old.challenges.get(chid) if old else None
            fresh = [e for e in ch.evidence_ids if prior is None or e not in prior.evidence_ids]
            for eid in ch.evidence_ids:
                self._check_evidence_id(eid, True, provided if eid in fresh else None, base, errors)
        # state-change documentation
        flags_by_target: dict = {}
        used_records: set = set()
        diffs = self._diffs(old, new)
        for kind, target, a, b, accepted in diffs:
            rec_index = next((i for i, r in enumerate(records) if r.get('kind') in accepted
                              and target in (r.get('affected_ids') or [])), None)
            if rec_index is None:
                errors.append(_err('undocumented_change', base,
                    f'{kind} on {target} needs a change record of kind {accepted} naming it in affected_ids'))
                continue
            used_records.add(rec_index)
            rec = records[rec_index]
            self._check_diff_basis(old, new, kind, target, a, b, rec, base, errors, flags_by_target)
        for i, r in enumerate(records):
            if i not in used_records:
                warnings.append(_err('change_record_without_effect', base,
                                     f"record {r.get('kind')} {r.get('affected_ids')} matched no state change"))
        # asserted verdict must be derivable
        asserted = item.get('asserted_verdict')
        if asserted is not None and asserted != new.verdict():
            errors.append(_err('asserted_verdict_not_derivable', base,
                f'response asserts {asserted!r} but obligations/routes/challenges derive {new.verdict()!r}'))
        if new.withdrawn is not None and old and not old.withdrawn:
            rec = next((r for r in records if r.get('kind') == 'candidate_withdrawn'), None)
            if rec is not None:
                if rec.get('basis') not in WITHDRAW_BASES:
                    errors.append(_err('bad_withdraw_basis', base, f'basis must be one of {WITHDRAW_BASES}'))
                else:
                    without = copy.deepcopy(new)
                    without.withdrawn = None
                    if rec['basis'] == 'refuted_in_scope' and without.verdict() != 'refuted_in_scope':
                        errors.append(_err('withdraw_without_refutation', base,
                            'basis refuted_in_scope but not every known route is contradicted'))
        if new.withdrawn is not None:
            new.withdrawn = {'round': round_index, 'reason': next((r.get('reason') for r in records
                             if r.get('kind') == 'candidate_withdrawn'), None)} if old else new.withdrawn
        if len(errors) > n0:
            return
        transitions.extend(self._build_transitions(old, new, records, diffs, round_index, flags_by_target))

    def _check_evidence_id(self, eid, exists, provided, path, errors, new_id=False):
        if eid not in self.store:
            errors.append(_err('unknown_evidence_id', path, f'evidence id {eid!r} was never provided'))
        elif provided is not None and eid not in provided:
            errors.append(_err('evidence_not_provided', path,
                               f'evidence id {eid!r} exists but was not provided to the model this round'))

    def _check_obligation_evidence(self, ob: Obligation, prior, provided, base, errors):
        fresh = [e for e in ob.evidence_ids if prior is None or e not in prior.evidence_ids]
        usable = []
        for eid in ob.evidence_ids:
            before = len(errors)
            self._check_evidence_id(eid, True, provided if eid in fresh else None, f'{base}.{ob.id}', errors)
            if len(errors) > before:
                continue
            ev = self.store.get(eid)
            if ob.snapshot is not None and not ev.represents(ob.snapshot):
                errors.append(_err('wrong_snapshot', f'{base}.{ob.id}',
                    f'{eid} is {ev.snapshot!r} evidence but the obligation is about {ob.snapshot!r}'))
                continue
            usable.append(ev)
        if ob.status in ('supported', 'contradicted', 'conflicted') and not ob.evidence_ids:
            errors.append(_err('missing_evidence', f'{base}.{ob.id}',
                               f'status {ob.status} requires cited evidence ids'))
        if ob.status in ('supported', 'contradicted') and usable:
            if not any(self._can_carry(ob.requires, ev) for ev in usable):
                errors.append(_err('evidence_cannot_carry_obligation', f'{base}.{ob.id}',
                    f'obligation requires {ob.requires!r}; cited evidence kinds '
                    f'{sorted({e.content_kind for e in usable})} provenance '
                    f'{sorted({e.provenance for e in usable})} cannot establish it '
                    f'(member lists/edges/locations are not values; static evidence is not runtime)'))

    @staticmethod
    def _can_carry(requires: str, ev: Evidence) -> bool:
        if requires == 'name':
            return True
        if requires in ('value', 'body'):
            return ev.bears_source
        if requires == 'runtime':
            return ev.runtime_validity == 'runtime_observed'
        return False

    def _check_diff_basis(self, old, new, kind, target, a, b, rec, base, errors, flags):
        basis = rec.get('basis')
        mark = flags.setdefault(target, [])
        if kind == 'status':
            if b == 'supported' and not new.obligations[target].evidence_ids:
                errors.append(_err('missing_evidence', base, f'{target} became supported without evidence'))
            if a == 'supported' and b == 'unknown' and basis not in REGRESSION_BASES:
                errors.append(_err('regression_needs_basis', base,
                    f'supported->unknown on {target} needs basis in {REGRESSION_BASES}'))
            if a in ('contradicted', 'conflicted') and b in ('supported', 'unknown'):
                prior_ids = set(old.obligations[target].evidence_ids)
                fresh = set(new.obligations[target].evidence_ids) - prior_ids
                if not fresh and basis != 'interpretation_error':
                    errors.append(_err('reopen_needs_basis', base,
                        f'{a}->{b} on {target} needs new evidence or basis interpretation_error'))
            if a == 'supported' and b == 'unknown' and basis == 'interpretation_error':
                mark.append('interpretation_correction_without_new_source')
        elif kind == 'obligation_removed':
            if basis not in REMOVAL_BASES:
                errors.append(_err('bad_removal_basis', base, f'basis must be one of {REMOVAL_BASES}'))
            elif a['status'] in ('contradicted', 'conflicted') and basis == 'not_necessary_for_claim':
                errors.append(_err('removal_hides_contradiction', base,
                    f'{target} is {a["status"]}; removal needs basis interpretation_error or scope_revised'))
            mark.append('obligation_removed')
        elif kind == 'required' and a is True and b is False:
            if basis not in REMOVAL_BASES:
                errors.append(_err('bad_removal_basis', base, f'making {target} optional needs basis in {REMOVAL_BASES}'))
            mark.append('obligation_made_optional')
        elif kind == 'route_removed':
            if basis not in ROUTE_REMOVAL_BASES:
                errors.append(_err('bad_route_removal_basis', base,
                    f'basis must be one of {ROUTE_REMOVAL_BASES}; adding unrelated unknowns never removes a route'))
            elif basis == 'contradicted':
                dead = copy.deepcopy(new)
                dead.routes[target] = SupportRoute(target, [o for o in a if o in new.obligations])
                if dead.route_state(dead.routes[target]) != 'contradicted':
                    errors.append(_err('route_removal_unjustified', base,
                        f'route {target} removed as contradicted but none of its obligations is contradicted'))
            elif basis == 'superseded' and rec.get('superseded_by') not in new.routes:
                errors.append(_err('route_removal_unjustified', base, 'superseded needs superseded_by existing route'))
        elif kind == 'challenge_changed':
            ch = new.challenges[target]
            if a['state'] == 'pending' and ch.state in ('resolved', 'rejected'):
                if not ch.evidence_ids and basis != 'interpretation_error':
                    errors.append(_err('challenge_needs_basis', base,
                        f'closing challenge {target} needs evidence ids or basis interpretation_error'))
                elif not ch.evidence_ids:
                    mark.append('challenge_dismissed_without_evidence')
        elif kind == 'challenge_revised':
            if basis not in CHALLENGE_REVISION_BASES:
                errors.append(_err('challenge_revision_needs_basis', base,
                    f'revising text/target/materiality of challenge {target} needs basis in {CHALLENGE_REVISION_BASES}'))
            if a['state'] == 'pending' and a['material'] and (
                    a['target'] != b['target'] or not b['material']):
                mark.append('material_challenge_retargeted_or_demoted')
            else:
                mark.append('challenge_revised')
        elif kind in ('claim', 'scope'):
            if any(new.route_state(r) == 'supported' for r in new.routes.values()):
                mark.append('support_carried_over_without_reassessment')

    def _check_caps(self, working: dict, errors: list):
        c = self.caps
        problems = []
        if len(working) > c.max_candidates:
            problems.append(('candidates', len(working), c.max_candidates))
        for cid, cand in working.items():
            for name, n, cap in (('obligations', len(cand.obligations), c.max_obligations_per_candidate),
                                 ('routes', len(cand.routes), c.max_routes_per_candidate),
                                 ('challenges', len(cand.challenges), c.max_challenges_per_candidate),
                                 ('notes', len(cand.notes), c.max_notes_per_candidate)):
                if n > cap:
                    problems.append((f'{cid}.{name}', n, cap))
        for what, n, cap in problems:
            self.cap_log.append({'cap': 'state_cap', 'event': 'update_rejected', 'what': what,
                                 'count': n, 'limit': cap})
            errors.append(_err('state_cap_exceeded', '$', f'{what}: {n} > {cap}; update rejected, nothing truncated'))

    def _refs(self, ids: Iterable[str]) -> list:
        out = []
        for eid in sorted(set(ids)):
            ev = self.store.get(eid)             # validated earlier; a KeyError here is a bug, not a silent drop
            out.append({'evidence_id': eid, 'snapshot': ev.snapshot, 'snapshots': list(ev.snapshot_set), 'file': ev.file,
                        'start_line': ev.start_line, 'end_line': ev.end_line,
                        'provenance': ev.provenance, 'runtime_validity': ev.runtime_validity})
        return out

    def _transition(self, rnd, cid, kind, target, old, new, reason, source_ids, flags):
        return Transition(0, rnd, cid, kind, target, old, new, reason, self._refs(source_ids), list(flags))

    def _build_transitions(self, old, new, records, diffs, rnd, flags_by_target) -> list:
        out = []
        cid = new.id
        if old is None:
            out.append(self._transition(rnd, cid, 'candidate_created', cid, None, new.verdict(),
                                        new.origin, new.origin_evidence_ids, []))
            for oid, ob in sorted(new.obligations.items()):
                out.append(self._transition(rnd, cid, 'obligation_created', oid, None, ob.status,
                                            f'created with candidate: {ob.predicate}', ob.evidence_ids, []))
            for rid, route in sorted(new.routes.items()):
                out.append(self._transition(rnd, cid, 'route_created', rid, None, list(route.obligation_ids),
                                            'created with candidate', [], []))
            for chid, ch in sorted(new.challenges.items()):
                out.append(self._transition(rnd, cid, 'challenge_created', chid, None, ch.state,
                                            ch.text, ch.evidence_ids, []))
            return out
        reasons = []
        for kind, target, a, b, accepted in diffs:
            rec = next(r for r in records if r.get('kind') in accepted and target in r['affected_ids'])
            ids = list(rec.get('evidence_ids') or [])
            if kind in ('status', 'evidence', 'obligation_added'):
                ob = new.obligations.get(target)
                ids += ob.evidence_ids if ob else []
            if kind == 'challenge_changed':
                ids += new.challenges[target].evidence_ids
            flags = list(flags_by_target.get(target, []))
            out.append(self._transition(rnd, cid, kind, target, a, b, rec['reason'], ids, flags))
            reasons.append(rec['reason'])
        before, after = old.verdict(), new.verdict()
        if before != after:
            flags = []
            if before == 'unresolved' and after == 'supported' and any(
                    'obligation_removed' in f or 'obligation_made_optional' in f
                    for f in flags_by_target.values()):
                flags.append('reportability_gained_by_obligation_weakening')
            if any('material_challenge_retargeted_or_demoted' in f for f in flags_by_target.values()):
                flags.append('verdict_changed_by_challenge_revision')
            out.append(self._transition(rnd, cid, 'verdict_change', cid, before, after,
                                        '; '.join(reasons) or 'derived from state changes',
                                        [e for o in new.obligations.values() for e in o.evidence_ids], flags))
        elif not diffs:
            out.append(self._transition(rnd, cid, 'retained_unchanged', cid, before, after,
                                        'listed in response with no state change', [], []))
        return out


# ---------------------------------------------------------------- request ranking
TOOL_PREFERENCE = {
    'declaration': ('definition', 'implementations', 'read_lines'),
    'callers': ('references', 'calls', 'read_lines'),
    'value': ('read_lines', 'grep_literal'),
    'import_alias': ('read_lines', 'grep_literal', 'definition'),
    'inheritance': ('read_lines', 'definition', 'implementations'),
    'test_contract': ('related_tests', 'read_lines'),
    'stdlib': ('read_lines', 'definition'),
}
TOOL_COST = {'read_lines': 1, 'definition': 1, 'ast_index': 1, 'grep_literal': 2, 'related_tests': 2,
             'references': 3, 'implementations': 3, 'calls': 3}
TIER_NAMES = ('challenge', 'required_gap', 'base_check', 'peripheral')


def query_key(request: Mapping) -> str:
    return _canon([request.get('tool'), request.get('snapshot'), request.get('args', {})])


def rank_requests(ledger: Ledger, requests: Iterable[Mapping], *, seen_queries: Iterable[str] = (),
                  limit: int, store: Optional[EvidenceStore] = None) -> dict:
    """Deterministic, metadata-only ranking.

    Request mapping (plain dict; an adapter converts to/from the product ContextRequest):
      id, tool, args, snapshot, purpose ('model_request'|'base_check'|'peripheral'),
      candidate_id?, obligation_id?, challenge_id?
    Tiers: 0 pending material challenge -> 1 required unresolved obligation (unknown/conflicted)
    -> 2 base change check -> 3 peripheral. Within a tier: round robin across candidates (stable id
    order, unlinked last); within a candidate: tool directness for the obligation's fact_kind,
    then cost, then id. Ids that don't resolve in the ledger demote a request to peripheral.
    Nothing is dropped silently: every non-selected request appears in ``skipped`` with a reason.
    """
    store = store or ledger.store
    seen = set(seen_queries)
    skipped: list = []
    tiers: dict = {t: {} for t in range(4)}
    batch_keys: set = set()
    order = 0
    for req in requests:
        order += 1
        req = dict(req)
        req.setdefault('id', f'req-{order}')
        key = query_key(req)
        req['query_key'] = key
        if key in seen or key in batch_keys:
            skipped.append({'request': req, 'reason': 'duplicate_query'})
            continue
        args = req.get('args', {})
        if req.get('tool') == 'read_lines' and {'file', 'start_line', 'end_line'} <= set(args):
            cover = store.covers(req.get('snapshot') or 'head', args['file'],
                                 int(args['start_line']), int(args['end_line']))
            if cover:
                skipped.append({'request': req, 'reason': 'already_read', 'covering_evidence_ids': cover})
                continue
        batch_keys.add(key)
        cand = ledger.candidates.get(req.get('candidate_id') or '')
        ob = cand.obligations.get(req.get('obligation_id') or '') if cand else None
        chal = cand.challenges.get(req.get('challenge_id') or '') if cand else None
        live = cand is not None and cand.verdict() not in ('refuted_in_scope', 'withdrawn')
        tier, notes = 3, []
        if (req.get('candidate_id') or req.get('obligation_id') or req.get('challenge_id')) and not (live and (ob or chal)):
            notes.append('unlinked_or_inactive_ids')
        if live and chal is not None and chal.material and chal.state == 'pending':
            tier = 0
        elif live and ob is not None and ob.required and cand.effective_status(ob.id) in ('unknown', 'conflicted'):
            tier = 1
        elif live and ob is not None:
            notes.append('obligation_already_resolved')
        elif req.get('purpose') == 'base_check':
            tier = 2
        fact_pref = TOOL_PREFERENCE.get(ob.fact_kind, ()) if ob is not None and ob.fact_kind else ()
        directness = fact_pref.index(req.get('tool')) if req.get('tool') in fact_pref else 99
        sort_key = (directness, TOOL_COST.get(req.get('tool'), 5), req['id'])
        req['tier'], req['tier_name'], req['notes'] = tier, TIER_NAMES[tier], notes
        bucket = req.get('candidate_id') if req.get('candidate_id') in ledger.candidates else None
        tiers[tier].setdefault(bucket, []).append((sort_key, req))
    ordered: list = []
    for tier in range(4):
        buckets = tiers[tier]
        names = sorted(k for k in buckets if k is not None) + ([None] if None in buckets else [])
        queues = {k: [r for _, r in sorted(buckets[k], key=lambda x: x[0])] for k in names}
        while any(queues.values()):
            for k in names:
                if queues[k]:
                    ordered.append(queues[k].pop(0))
    selected = ordered[:max(0, limit)]
    for rank, req in enumerate(selected):
        req['rank'] = rank
    for req in ordered[max(0, limit):]:
        skipped.append({'request': req, 'reason': 'query_cap_blocked'})
    return {'selected': selected, 'skipped': skipped, 'limit': limit,
            'tier_counts': {TIER_NAMES[t]: sum(len(v) for v in tiers[t].values()) for t in range(4)}}
