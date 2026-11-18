"""Post-freeze retrieval diagnostics, with target annotations only in evaluator."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ace_review.auto_context import Graph, retrieve
from ace_review.context_necessity import assert_frozen, digest
from ace_review.counterfactual import check_witness, write_new
from ace_review.obligation import render_blocks, token_count
from ace_review.protocol_hypothesis import mask_source
from ace_review.reviewer import render_prompt

RUN = ROOT / 'runs/auto_context2'
SETUP = ROOT / 'runs/auto_context_setup2'


def main():
    assert_frozen(RUN); assert_frozen(SETUP)
    assert digest(ROOT / 'ace_review/auto_context.py') == digest(RUN / 'frozen_cases.py')
    labels = json.loads((RUN / 'private_labels.json').read_text())
    rows = [json.loads(l) for l in (RUN / 'requests.jsonl').read_text().splitlines()]
    audits = {(a['task_id'], a['arm']): a for a in json.loads((RUN / 'source_audit.json').read_text())}
    originals = {r['task_id']: r for c in {m['source_run'] for m in labels.values()}
                 for r in map(json.loads, (Path(c) / 'requests.jsonl').read_text().splitlines())
                 if r['condition'] == 'relevant'}
    # These gold targets are evaluation-only, defined from prior manual evidence.
    targets = {'pager_stream': ['src/click/_termui_impl.py::_tempfilepager::1::0',
                                'src/click/termui.py::echo_via_pager::1::0'],
               'backend_options': ['src/anyio/_backends/_trio.py::TrioBackend.run::1::0']}
    checked_spans = []
    for row in rows:
        label = labels[row['task_id']]
        snapshot = Path(label['source_run']) / 'snapshots' / (label['variant'] + '_head')
        for b in row['contexts']:
            lines = mask_source((snapshot / b['file']).read_text()).splitlines()
            assert b['content'] == '\n'.join(lines[b['start_line'] - 1:b['end_line']])
            checked_spans.append((row['task_id'], row['condition'], b['id']))
    measurements, comparisons, budgets, runtime = [], [], [], {}
    for row in rows:
        label = labels[row['task_id']]; a = audits[row['task_id'], row['condition']]
        chosen = {(b['file'], line) for b in row['contexts']
                  for line in range(b['start_line'], b['end_line'] + 1)}
        endpoints = []
        for b in originals[row['task_id']]['contexts']:
            points = {(b['file'], line) for line in range(b['start_line'], b['end_line'] + 1)}
            endpoints.append(dict(file=b['file'], start=b['start_line'], end=b['end_line'],
                                  covered=len(points & chosen), total=len(points), full=points <= chosen))
        measurements.append(dict(task_id=row['task_id'], **label, arm=row['condition'],
            source_tokens=a['source_tokens'], selected_blocks=len(row['contexts']), manual_spans=endpoints,
            targets=[dict(key=k, candidate=k in a['ranked_keys'], selected=k in a['selected_keys'],
                          rank=a['ranked_keys'].index(k) + 1 if k in a['ranked_keys'] else None,
                          budget_skipped=k in a['budget_skipped_keys']) for k in targets[label['family']]]))
    for family in sorted(targets):
        for arm in ('diff', 'bm25', 'scip_head', 'scip_delta'):
            pair = [r for r in rows if labels[r['task_id']]['family'] == family and r['condition'] == arm]
            comparisons.append(dict(family=family, arm=arm,
                identical_safe_unsafe_source=pair[0]['contexts'] == pair[1]['contexts'],
                identical_safe_unsafe_initial_prompt=render_prompt(pair[0], RUN / 'proposal.txt') == render_prompt(pair[1], RUN / 'proposal.txt')))
        for variant in ('safe', 'unsafe'):
            pair = [r for r in rows if labels[r['task_id']]['family'] == family and labels[r['task_id']]['variant'] == variant and r['condition'].startswith('scip_')]
            comparisons.append(dict(family=family, variant=variant,
                identical_head_delta_prompt=render_prompt(pair[0], RUN / 'proposal.txt') == render_prompt(pair[1], RUN / 'proposal.txt'),
                identical_head_delta_unordered_source=sorted((b['file'], b['start_line'], b['content']) for b in pair[0]['contexts']) == sorted((b['file'], b['start_line'], b['content']) for b in pair[1]['contexts'])))
    for family, project in [('pager_stream', 'click'), ('backend_options', 'anyio')]:
        sample = next(r for r in rows if labels[r['task_id']]['family'] == family and labels[r['task_id']]['variant'] == 'unsafe')
        case = Path(labels[sample['task_id']]['source_run'])
        graphs = []
        for version in ('base', 'head'):
            index_root = SETUP / 'indices' / project / ('unsafe_' + version)
            hashes = json.loads((index_root / 'index_audit.json').read_text())['source_sha256']
            sources = {f: (index_root / f).read_text() for f in hashes}
            graphs.append(Graph(sources, json.loads((index_root / 'index.json').read_text())))
        for arm in ('bm25', 'scip_head', 'scip_delta'):
            for cap in (800, 1600, 3200):
                blocks, a = retrieve(*graphs, sample['diff'], arm, cap)
                budgets.append(dict(family=family, arm=arm, budget=cap, actual_tokens=token_count(render_blocks(blocks)),
                                    targets={k: k in a['selected_keys'] for k in targets[family]}))
        runtime[family] = {cell: check_witness(case / 'snapshots' / cell, case / 'witness.py',
                            ROOT / 'tools/venv-auto-scip311/bin/python')
                           for cell in ('safe_base', 'safe_head', 'unsafe_base', 'unsafe_head')}
    result = dict(checked_source_spans=len(checked_spans), source_spans_match_masked_head=True,
                  measurements=measurements, prompt_comparisons=comparisons,
                  posthoc_no_model_budget_sweep=budgets, python311_portability_witness=runtime,
                  note='Gold used only in these post-freeze diagnostics. Larger budgets did not trigger model calls or change primary inputs.')
    write_new(RUN / 'retrieval_diagnostics.json', result)
    print(json.dumps(dict(checked_source_spans=len(checked_spans), prompt_comparisons=comparisons,
                         budget_sweep=budgets, runtime_status={f:{c:v['status'] for c,v in cells.items()} for f,cells in runtime.items()}), indent=2))


if __name__ == '__main__':
    main()
