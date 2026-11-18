"""Post-hoc one-step retrieval diagnostic; no new reviewer accuracy claim.

Replays only the first S controller output against its original visible state.
Each gap gets one whole-unit lexical acquisition attempt before graph expansion.
No oracle target is passed to that selection. Later controllers are NOT replayed
against changed states, which would be causally inconsistent.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ace_review.context_adaptive import Corpus, EvidenceState

RUN = Path(__file__).resolve().parent
P0 = RUN / 'p0_v2'
cases = json.loads((P0 / 'cases.json').read_text())[:2]
audits = json.loads((P0 / 'retrieval_round2.json').read_text())['records']
results = []
for case in cases:
    cid = case['case_id']
    for world, spec in case['worlds'].items():
        corpus = Corpus(spec['versions'], spec['graphs'], case['diff'])
        original = next(r for r in audits if r['case_id'] == cid and r['world'] == world and r['arm'] == 'S')
        start = original['rounds'][0]['state']
        state = EvidenceState(corpus)
        state.lines = {k: set(v) for k, v in start['source_lines'].items()}
        state.paths = dict(start['paths'])
        controller = json.loads((P0 / 'raw' / f'{cid}_{world}_S_controller1.json').read_text())['response']
        chosen = []
        for question in controller['questions']:
            if not question['gaps']:
                continue
            query = ' '.join(g['retrieval_query'] + ' ' + g['needed_fact'] for g in question['gaps'])
            scores = corpus.scores(query)
            for key in sorted(scores, key=lambda k: (-scores[k], k)):
                if state.add([key], 'L1', 1200):
                    chosen.append({'question_id': question['question_id'], 'unit_id': key,
                                   'file': corpus.units[key].file, 'symbol': corpus.units[key].symbol})
                    break
        # Endpoint names appear only in this post-selection diagnostic.
        targets = [k for k, u in corpus.units.items() if u.version == 'head' and
                   (u.symbol == '_tempfilepager' if cid == 'case01' else
                    u.file == 'src/anyio/_backends/_trio.py' and u.symbol == 'TrioBackend.run')]
        assert len(targets) == 1
        key = targets[0]; unit = corpus.units[key]
        full = set(range(unit.start, unit.end + 1))
        results.append({'case_id': cid, 'world': world, 'chosen': chosen,
            'original_round1_endpoint_full': set(original['rounds'][1]['state']['source_lines'].get(key, [])) == full,
            'gap_priority_endpoint_full': state.lines.get(key, set()) == full,
            'source_tokens': state.tokens, 'cap': 1200,
            'same_initial_packet': True, 'same_first_controller_output': True})
report = {'status': 'post_hoc_one_step_diagnostic', 'new_model_calls': 0,
          'review_accuracy_measured': False, 'no_later_controller_reuse': True, 'results': results}
(RUN / 'gap_priority_replay.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False))
