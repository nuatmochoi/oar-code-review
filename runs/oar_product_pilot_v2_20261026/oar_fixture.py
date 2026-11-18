"""Model-free stand-in for the CLI client, used ONLY by smoke runs and tests.

It is not a model and its output is never a performance result. It implements the same stage/claim
discipline as ``CliCompletionClient`` (one call per stage, retry refused, 3-call cap) and writes the same
file names, but the response text is produced by a deterministic script that reads nothing except what the
prompt already contains (evidence ids, the changed file/symbol names the product put in the packet).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import cli_transport as ct
import oar_core as oc

ID_PATTERN = re.compile(r'(?:evidence_id|id)="([^"]+)"')


def empty_response(**over) -> dict:
    base = {'review': {'summary': 'fixture', 'reviewed_areas': [], 'observations': [],
                       'context_assessment': {'cross_file_conclusion': 'insufficient_context',
                                              'used_context': [], 'dismissed_context': []}},
            'findings': [], 'context_requests': [], 'candidates': []}
    base.update(over)
    return base


def request(i, operation, reason='fixture lookup', **kw):
    base = {'id': f'ctx-{i}', 'operation': operation, 'path': '', 'query': '', 'start': None, 'end': None,
            'reason': reason, 'candidate_id': '', 'obligation_id': '', 'challenge_id': ''}
    base.update(kw)
    return base


def candidate(cid='C1', evidence=(), status='supported', extra=(), close=False):
    """``close=True``: O2 is also supported (needs a ``status_change`` record once C1 exists), so the route is supported."""
    ids = list(evidence)[:1]
    out = {'id': cid, 'claim': 'fixture claim', 'scope': 'fixture scope', 'origin': 'fixture origin',
            'origin_evidence_ids': ids,
            'obligations': [{'id': 'O1', 'predicate': 'fixture fact one', 'required': True, 'requires': 'body',
                             'fact_kind': 'declaration', 'snapshot': None, 'status': status if ids else 'unknown',
                             'evidence_ids': ids if status != 'unknown' else [], 'depends_on': []},
                            {'id': 'O2', 'predicate': 'fixture fact two (open)', 'required': True, 'requires': 'name',
                             'fact_kind': 'callers', 'snapshot': None, 'status': 'unknown', 'evidence_ids': [],
                             'depends_on': []}, *extra],
            'routes': [{'id': 'R1', 'obligation_ids': ['O1', 'O2']}], 'challenges': [], 'unresolved': [],
            'remove_obligations': [], 'remove_routes': [], 'resolve_unresolved': [], 'withdraw': False, 'changes': []}
    if close and ids:
        out['obligations'][1].update(status='supported', evidence_ids=ids)
        out['changes'] = [{'kind': 'status_change', 'affected_ids': ['O2'], 'reason': 'fixture: seen in the supplied lines',
                           'basis': '', 'evidence_ids': ids, 'claim_effect_if_true': '', 'claim_effect_if_false': ''}]
    return out


class FixtureClient:
    """``script(stage_index, ctx) -> dict`` returns the response object for that stage."""

    def __init__(self, *, out_dir, review_id, script, max_calls=3):
        self.dir = Path(out_dir) / review_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.review_id, self.script, self.max_calls = review_id, script, max_calls
        self._stage = None
        self._stage_calls = 0
        self._begun: list = []
        self.calls: list = []

    def begin_stage(self, stage: str) -> None:
        if stage not in ct.STAGES or ct.STAGES.index(stage) != len(self._begun):
            raise ct.StageError(f'bad stage {stage}')
        self._begun.append(stage)
        self._stage, self._stage_calls = stage, 0

    def complete(self, *, system: str, prompt: str, suffix: str = '', shared: str = '') -> str:
        if self._stage is None:
            raise ct.StageError('complete() outside begin_stage()')
        if self._stage_calls >= 1:
            raise ct.RetryRefused(f'second complete() in stage {self._stage}')
        self._stage_calls += 1
        if len(self._begun) > self.max_calls:
            raise ct.CallCapExceeded('cap')
        text, parts = ct.compose_input(ct.DEFAULT_ORDER, system, shared, prompt, suffix)
        ids = ID_PATTERN.findall(prompt + suffix)
        response = self.script(len(self._begun) - 1, {'prompt': prompt, 'suffix': suffix, 'shared': shared,
                                                      'system': system, 'ids': ids, 'text': text})
        raw = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)   # str = raw (maybe invalid) text
        stage = self._stage
        ct.write_new(self.dir / f'{stage}.claim.json', {'review_id': self.review_id, 'stage': stage, 'fixture': True})
        ct.write_new(self.dir / f'{stage}.input.json', {'stage': stage, 'parts': parts, 'raw': {
            'system': system, 'shared': shared, 'prompt': prompt, 'suffix': suffix}, 'composed_text': text,
            'composed_sha256': oc._sha(text), 'reference_tokens_o200k': oc.count_reference_tokens(text),
            'fixture_not_a_model': True})
        ct.write_new(self.dir / f'{stage}.raw.txt', raw)
        ct.write_new(self.dir / f'{stage}.result.json', {'status': 'ok', 'fixture_not_a_model': True, 'stage': stage,
                                                         'actual_tool_calls': 0, 'raw_response': raw})
        self.calls.append(stage)
        return raw


# ---------------------------------------------------------------- generic scripted "model" for smoke / scheduler tests
CHANGED = re.compile(r'changed_files=\[(.*?)\]')
SYMBOL = re.compile(r'changed_symbols=([^\n]*)')


def generic_script(stage: int, ctx: dict) -> dict:
    """Deterministic, case-agnostic script. Exercises: over-long request lists (4 > 3), overlapping/beyond-seed
    reads, every enabled lookup kind, an invalid FIRST state update (R/AR rejection feedback), a final finding whose candidate is fully supported (the R/AR reportability rule holds)."""
    text = ctx['prompt']
    files = re.findall(r"'([^']+)'", (CHANGED.search(text) or [None, ''])[1])
    changed = files[0] if files else ''
    symbols = [s.split(':') for s in re.split(r',\s*', (SYMBOL.search(text) or [None, ''])[1]) if s.count(':') >= 4]
    name = symbols[0][1] if symbols else ''
    sources = [i for i in ctx['ids'] if i.startswith('context:')]
    first = sources[:1]
    if stage == 0:
        broken = candidate('C1', first)
        broken['obligations'][0]['evidence_ids'] = ['agentic:fixture:evidence-never-provided']   # rejected by the R/AR ledger
        requests = [request(1, 'read_lines', path=changed, start=1, end=60),
                    request(2, 'find_references', query=name or 'self', path=changed, candidate_id='C1', obligation_id='O2'),
                    request(3, 'grep_literal', query=name or 'return'),
                    request(4, 'go_to_definition', query=name or 'self', path=changed)]
        return empty_response(context_requests=requests, candidates=[broken])
    if stage == 1:
        requests = [request(1, 'read_lines', path=changed, start=40, end=240),
                    request(2, 'document_symbols', path=changed),
                    request(3, 'related_tests', path=changed),
                    request(4, 'symbol_search', query=name or 'self')]
        return empty_response(context_requests=requests, candidates=[candidate('C1', first)])
    finding = {'title': 'fixture finding', 'severity': 'major', 'confidence': 70, 'introduced_by_mr': True,
               'category': 'correctness', 'path': changed, 'line_start': 1, 'line_end': 1, 'candidate_id': 'C1',
               'trigger': 'fixture trigger', 'mechanism': 'fixture mechanism', 'impact': 'fixture impact',
               'evidence': [{'evidence_id': first[0] if first else 'diff:none', 'path': changed, 'lines': '1', 'note': 'n'}],
               'why_it_matters': 'x'}
    return empty_response(findings=[finding], candidates=[candidate('C1', first, close=True)])
