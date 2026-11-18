"""Retrieval-only P2-A gate; no model calls and no target input to selector."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'runs'))
from selector_ablation_setup import INDEX_SOURCE, load_graph  # noqa: E402
from ace_review.answer_source import AnswerIndex, select_answers  # noqa: E402
from ace_review.context_necessity import assert_frozen, digest, freeze  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402
from ace_review.question_context import select  # noqa: E402

SOURCE = ROOT / 'runs/question_review1'
RUN = ROOT / 'runs/answer_source1'


def main():
    assert_frozen(SOURCE)
    assert_frozen(SOURCE / 'final_inputs')
    assert_frozen(INDEX_SOURCE)
    RUN.mkdir(exist_ok=False)
    inputs = RUN / 'inputs'
    for name in ('ace_review/answer_source.py', 'tests/test_answer_source.py',
                 'runs/answer_source_experiment.py', 'runs/ANSWER_SOURCE_PLAN_KO.md',
                 'ace_review/question_context.py', 'ace_review/auto_context.py',
                 'ace_review/delta_scip.py', 'ace_review/obligation.py',
                 'ace_review/selector_ablation.py', 'ace_review/bm25.py',
                 'runs/selector_ablation_setup.py'):
        write_new(inputs / 'code' / name, (ROOT / name).read_text(), raw=True)
    write_new(inputs / 'provenance.json', {
        'source_freeze_sha256': digest(SOURCE / 'freeze.json'),
        'source_final_freeze_sha256': digest(SOURCE / 'final_inputs/freeze.json'),
        'source_result_sha256': digest(SOURCE / 'predictions.jsonl'),
        'index_freeze_sha256': digest(INDEX_SOURCE / 'freeze.json'),
        'source_cap': 800, 'new_model_calls': 0,
        'cohort': 'Reused two development families, not a holdout'})
    base_rows = [json.loads(line) for line in (SOURCE / 'base_requests.jsonl').read_text().splitlines()]
    write_new(inputs / 'base_requests.jsonl', base_rows, jsonl=True)
    proposals = {}
    for i in range(4):
        for repeat in (1, 2, 3):
            name = f'proposal_{i:02}_r{repeat}.jsonl'
            raw = json.loads((SOURCE / 'raw' / name).read_text())
            proposals[i, repeat] = raw['response']
            write_new(inputs / name, [raw], jsonl=True)
    freeze(inputs)
    # Labels only choose the existing snapshot, never enter select_answers.
    labels = json.loads((SOURCE / 'private_labels.json').read_text())
    requests, audits = [], []
    for i, row in enumerate(base_rows):
        label = labels[row['task_id']]
        project = {'pager_stream': 'click', 'backend_options': 'anyio'}[label['family']]
        base, _ = load_graph(project, label['variant'] + '_base')
        head, source_audit = load_graph(project, label['variant'] + '_head')
        location = INDEX_SOURCE / 'indices' / project / (label['variant'] + '_head')
        sources = {name: (location / name).read_text() for name in source_audit['source_sha256']}
        index = AnswerIndex(sources, json.loads((location / 'index.json').read_text()))
        for repeat in (1, 2, 3):
            proposal = proposals[i, repeat]
            for arm in ('claim_search', 'question_search', 'answer_source'):
                if arm == 'answer_source':
                    blocks, audit = select_answers(base, head, index, row['diff'], proposal)
                else:
                    blocks, audit = select(base, head, row['diff'], proposal, arm)
                requests.append({'index': i, 'repeat': repeat, 'task_id': row['task_id'],
                                 'condition': arm, 'contexts': blocks})
                audits.append({'index': i, 'repeat': repeat, 'task_id': row['task_id'], **audit})
            print(json.dumps({'retrieval_complete': i, 'repeat': repeat}), flush=True)
    output = RUN / 'selections'
    write_new(output / 'requests.jsonl', requests, jsonl=True)
    write_new(output / 'source_audit.json', audits)
    freeze(output)
    # Endpoint diagnostics happen only AFTER all selections are frozen.
    endpoints = {0: ('::_tempfilepager::', '::echo_via_pager::'),
                 1: ('::_tempfilepager::', '::echo_via_pager::'),
                 2: ('::TrioBackend.run::',), 3: ('::TrioBackend.run::',)}
    diagnostics = []
    for a in audits:
        answers = a.get('answer_candidates', [])
        diagnostics.append({k: a[k] for k in ('index', 'repeat', 'arm', 'source_tokens')} | {
            'selected_endpoints': {name: any(name in k for k in a['selection_order']) for name in endpoints[a['index']]},
            'answer_candidates': len(answers),
            'fully_selected_answers': sum(c['full_function_selected'] for c in answers),
            'uncertain_answers': sum(c['uncertain_path'] for c in answers),
            'unsupported_questions': len(a.get('unsupported_questions', [])),
            'unresolved': a.get('unresolved', [])})
    assert_frozen(inputs)
    assert_frozen(output)
    assert_frozen(SOURCE)
    write_new(RUN / 'diagnostics.json', diagnostics)
    write_new(RUN / 'completion.json', {'retrieval_requests': len(requests), 'new_model_calls': 0,
                                       'complete': True, 'review_accuracy_measured': False})
    print(json.dumps(diagnostics, indent=2))


if __name__ == '__main__':
    main()
