"""Post-freeze sensitivity check after adding Python-3.11-only dependency.

Primary inputs/results are never changed or rerun. Any discrepancy is reported.
"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'runs'))
import auto_context_setup as setup
from ace_review.auto_context import ARMS, retrieve
from ace_review.context_necessity import assert_frozen, freeze
from ace_review.counterfactual import check_witness, write_new


def main():
    run = ROOT / 'runs/auto_context2'
    assert_frozen(run)
    setup.SETUP = ROOT / 'runs/auto_context_dependency_check1'
    setup.SETUP.mkdir(exist_ok=False)
    code = ('import importlib.metadata as m,json; print(json.dumps(['
            '{"name":d.metadata["Name"],"version":d.version,"files":[str(f) for f in d.files or []]}'
            ' for d in m.distributions()]))')
    environment = json.loads(subprocess.check_output([str(setup.RUNTIME / 'bin/python'), '-c', code], text=True))
    write_new(setup.SETUP / 'environment.json', environment)
    write_new(setup.SETUP / 'frozen_check.py', Path(__file__).read_text(), raw=True)
    specs = [(ROOT / 'runs/context_pager_case1', 'click'), (ROOT / 'runs/context_backend_case3', 'anyio')]
    jobs = [(case, project, variant + '_' + version) for case, project in specs
            for variant in ('safe', 'unsafe') for version in ('base', 'head')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        indices = dict(pool.map(setup.index_cell, jobs))
    rows = [json.loads(l) for l in (run / 'requests.jsonl').read_text().splitlines()]
    labels = json.loads((run / 'private_labels.json').read_text())
    comparisons = []
    for row in rows:
        label = labels[row['task_id']]
        project = 'click' if label['family'] == 'pager_stream' else 'anyio'
        graphs = [indices[project, label['variant'] + '_' + v][0] for v in ('base', 'head')]
        blocks, audit = retrieve(*graphs, row['diff'], row['condition'])
        comparisons.append(dict(task_id=row['task_id'], condition=row['condition'],
                                contexts_identical=blocks == row['contexts'], source_tokens=audit['source_tokens']))
    witnesses = {}
    for case, project in specs:
        witnesses[project] = {cell: check_witness(case / 'snapshots' / cell, case / 'witness.py', setup.RUNTIME / 'bin/python')
                             for cell in ('safe_base', 'safe_head', 'unsafe_base', 'unsafe_head')}
    result = dict(added_dependency='typing_extensions==4.15.0 required by AnyIO on Python <3.13',
                  primary_inputs_not_changed=True, new_model_calls=0,
                  comparisons=comparisons, all_primary_contexts_reproduced=all(c['contexts_identical'] for c in comparisons),
                  portability_witnesses=witnesses)
    write_new(setup.SETUP / 'result.json', result)
    freeze(setup.SETUP)
    write_new(run / 'dependency_sensitivity.json', result)
    print(json.dumps(dict(all_primary_contexts_reproduced=result['all_primary_contexts_reproduced'],
                         runtime_status={p:{c:v['status'] for c,v in cells.items()} for p,cells in witnesses.items()}), indent=2))


if __name__ == '__main__':
    main()
