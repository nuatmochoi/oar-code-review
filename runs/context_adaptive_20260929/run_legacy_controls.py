"""Supplementary existing-selector controls, fixed before their model outputs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ace_review.answer_source import AnswerIndex, select_answers
from ace_review.context_adaptive import FINAL_PROMPT, FINAL_SCHEMA, digest
from ace_review.context_adaptive_run import ask, common
from ace_review.counterfactual import write_new
from ace_review.obligation import render_blocks, token_count
from ace_review.question_context import select
from ace_review.protocol_hypothesis import mask_source
from ace_review.selector_ablation import retrieve_selector
from ace_review.swing_pilot import load_graph

PARENT = Path(__file__).resolve().parent
SOURCE = PARENT / 'p0_v2'
OUTPUT = PARENT / 'legacy_controls_v2'


def prepare():
    OUTPUT.mkdir(exist_ok=False)
    cases = json.loads((SOURCE / 'cases.json').read_text())[:2]
    proposals = json.loads((SOURCE / 'proposals.json').read_text())
    packets, audits = [], []
    for case in cases:
        cid = case['case_id']
        questions = proposals[cid]['response']['questions']
        project = {'case01': 'click', 'case02': 'anyio'}[cid]
        proposal = {'hypotheses': [{'claim': q['question'], 'failure_scenario': '', 'questions': [
            {'question': q['question'], 'symbols': q['symbols'], 'support_if': '', 'refute_if': '',
             'operation': 'forwarding' if re.search(r'argument|option|forward|parameter|backend', q['question'], re.I) else 'other'}
        ]} for q in questions]}
        for world, variant in [('a', 'safe'), ('b', 'unsafe')]:
            index_root = ROOT / 'runs/auto_context_dependency_check1/indices' / project
            base, _ = load_graph(index_root / (variant + '_base'))
            head, sources = load_graph(index_root / (variant + '_head'))
            index = AnswerIndex(sources, json.loads((index_root / (variant + '_head') / 'index.json').read_text()))
            arms = ['legacy_claim', 'legacy_reserve'] + (['legacy_answer'] if cid == 'case02' else [])
            for arm in arms:
                budget = 1600
                while True:
                    if arm == 'legacy_claim':
                        blocks, audit = select(base, head, case['diff'], proposal, 'claim_search', budget)
                    elif arm == 'legacy_reserve':
                        blocks, audit = retrieve_selector(base, head, case['diff'], 'selector_both', budget, min(800, budget))
                    else:
                        blocks, audit = select_answers(base, head, index, case['diff'], proposal, budget)
                    for block in blocks:
                        block['id'] = 'E-' + digest([block['file'], block['start_line'], block['end_line']])[:12]
                        expected = '\n'.join(mask_source(sources[block['file']]).splitlines()[block['start_line'] - 1:block['end_line']])
                        # Existing block() serialization can drop a final empty
                        # line. No non-newline source difference is tolerated.
                        assert block['content'].rstrip('\n') == expected.rstrip('\n')
                    evidence = 'HEAD source excerpts; surrounding code may be omitted.\n' + render_blocks(blocks)
                    if token_count(evidence) <= 1600:
                        break
                    budget -= 32
                    if budget < 0:
                        raise ValueError('Cannot fit supplementary context')
                prompt = FINAL_PROMPT + common(case) + '\nQUESTIONS:\n' + json.dumps(questions) + '\nREPOSITORY EVIDENCE:\n' + evidence
                packets.append({'run_id': f'{cid}_{world}_{arm}_final', 'phase': 'final', 'case_id': cid, 'world': world,
                    'arm': arm, 'prompt': prompt, 'prompt_sha256': digest(prompt), 'evidence_tokens': token_count(evidence),
                    'evidence_ids': [b['id'] for b in blocks], 'evidence_sha256': digest(evidence), 'blocks': blocks})
                audits.append({'case_id': cid, 'world': world, 'arm': arm, 'selector_budget': budget,
                               'rendered_budget': 1600, 'selector_audit': audit})
    random.Random(20260929).shuffle(packets)
    write_new(OUTPUT / 'packets.json', packets)
    write_new(OUTPUT / 'audits.json', audits)
    write_new(OUTPUT / 'plan.md', '''# Existing-selector diagnostic controls

Ten supplementary final-review calls: prior claim-BM25 and seed-deduplicated
2-hop reserve retrieval for both Click and AnyIO worlds (8), plus prior
answer-source retrieval for the two AnyIO worlds (2). Same initial questions,
final-review instruction, model, output schema and 1,600 reference-token cap as
P0 v2. Questions are deterministically adapted to the prior proposal API, without
gold mechanism text. Answer-source forwarding classification uses question words.
Runtime resolution is not claimed.

Original selectors retain their 60-line AST chunking/head-only source policy;
formatting also differs from P0's line-numbered whole AST units. These are strong
existing-system checks, not a clean one-factor comparison. Selection and prompts
are frozen before any supplementary model outputs. No retries or output-based
selection. Original source is verified span-by-span. These are the same observed
development families; no generalization or novelty claim follows from this run.
''', raw=True)
    write_new(OUTPUT / 'freeze.json', {'packets_sha256': digest(packets), 'audits_sha256': digest(audits),
        'initial_proposals_sha256': digest(proposals), 'script_sha256': digest(Path(__file__).read_text()),
        'planned_model_calls': 10, 'source_run': str(SOURCE)})
    print(json.dumps({'prepared': len(packets)}))


def run():
    packets = json.loads((OUTPUT / 'packets.json').read_text())
    freeze = json.loads((OUTPUT / 'freeze.json').read_text())
    assert digest(packets) == freeze['packets_sha256']
    assert digest(Path(__file__).read_text()) == freeze['script_sha256']
    (OUTPUT / 'raw').mkdir(exist_ok=False)
    def call(row):
        return ask(OUTPUT, row['run_id'], row['prompt'], FINAL_SCHEMA,
                   {k: v for k, v in row.items() if k not in ('run_id', 'prompt', 'blocks')})
    with ThreadPoolExecutor(max_workers=4) as pool:
        predictions = list(pool.map(call, packets))
    write_new(OUTPUT / 'predictions.json', predictions)
    print(json.dumps({'completed': len(predictions), 'errors': sum(r['status'] != 'ok' for r in predictions)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'run'])
    args = parser.parse_args()
    prepare() if args.command == 'prepare' else run()
