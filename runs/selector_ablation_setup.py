"""Build and freeze the paired-SCIP selector ablation; never call the model."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ace_review.auto_context import Graph  # noqa: E402
from ace_review.context_necessity import assert_frozen, digest, freeze, prepare  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402
from ace_review.protocol_hypothesis import clean_row  # noqa: E402
from ace_review.selector_ablation import ARMS, retrieve_selector  # noqa: E402

PRIMARY = ROOT / 'runs/auto_context2'
INDEX_SOURCE = ROOT / 'runs/auto_context_dependency_check1'
SETUP = ROOT / 'runs/selector_ablation_setup2'
RUN = ROOT / 'runs/selector_ablation2'
FAMILY_PROJECT = {'pager_stream': 'click', 'backend_options': 'anyio'}


def load_graph(project, cell):
    root = INDEX_SOURCE / 'indices' / project / cell
    audit = json.loads((root / 'index_audit.json').read_text())
    expected = audit['source_sha256']
    sources = {}
    for name, expected_hash in expected.items():
        text = (root / name).read_text()
        actual = hashlib.sha256(text.encode()).hexdigest()
        if actual != expected_hash:
            raise ValueError(f'Indexed source changed: {project}/{cell}/{name}')
        sources[name] = text
    raw = json.loads((root / 'index.json').read_text())
    graph = Graph(sources, raw)
    if graph.audit != audit['graph']:
        raise ValueError(f'Graph audit changed: {project}/{cell}')
    return graph, {
        'source_sha256': expected,
        'index_json_sha256': digest(root / 'index.json'),
        'index_scip_sha256': digest(root / 'index.scip'),
        'graph': graph.audit,
    }


def main():
    assert_frozen(PRIMARY)
    assert_frozen(INDEX_SOURCE)
    sensitivity = json.loads((INDEX_SOURCE / 'result.json').read_text())
    if not sensitivity['all_primary_contexts_reproduced']:
        raise ValueError('Corrected dependency index did not reproduce primary contexts')

    SETUP.mkdir(exist_ok=False)
    write_new(SETUP / 'plan.md', (ROOT / 'runs/SELECTOR_ABLATION_PLAN_KO.md').read_text(), raw=True)
    write_new(SETUP / 'frozen_cases.py', (ROOT / 'ace_review/selector_ablation.py').read_text(), raw=True)
    write_new(SETUP / 'frozen_setup.py', Path(__file__).read_text(), raw=True)
    write_new(SETUP / 'frozen_protocol.py', (ROOT / 'ace_review/protocol_hypothesis.py').read_text(), raw=True)
    write_new(SETUP / 'frozen_reviewer.py', (ROOT / 'ace_review/reviewer.py').read_text(), raw=True)

    labels = json.loads((PRIMARY / 'private_labels.json').read_text())
    original_rows = [json.loads(line) for line in (PRIMARY / 'requests.jsonl').read_text().splitlines()
                     if json.loads(line)['condition'] == 'diff']
    if len(original_rows) != 4:
        raise ValueError('Expected four source task variants')

    graphs, index_audits = {}, {}
    for task_id, label in labels.items():
        project = FAMILY_PROJECT[label['family']]
        for version in ('base', 'head'):
            cell = f"{label['variant']}_{version}"
            key = project, cell
            if key not in graphs:
                graphs[key], index_audits[f'{project}/{cell}'] = load_graph(*key)

    rows, audits = [], []
    for original in original_rows:
        label = labels[original['task_id']]
        project = FAMILY_PROJECT[label['family']]
        base = graphs[project, f"{label['variant']}_base"]
        head = graphs[project, f"{label['variant']}_head"]
        for arm in ARMS:
            # No target string, manual source span, prior response, or gold field is passed.
            blocks, audit = retrieve_selector(
                base, head, original['diff'], arm, budget=800, reserve_budget=400)
            row = clean_row(original) | {
                'condition': arm,
                'contexts': blocks,
                'context_status': {'context_tokens': audit['source_tokens']},
            }
            rows.append(row)
            audits.append({'task_id': row['task_id'], **audit})

    if len(rows) != 16 or len({(r['task_id'], r['condition']) for r in rows}) != 16:
        raise ValueError('Expected a complete four-by-four request matrix')
    if any(r['context_status']['context_tokens'] > 800 for r in rows):
        raise ValueError('Source-token cap exceeded')
    current = {(r['task_id'], r['condition']): r for r in rows}
    primary_diff = {r['task_id']: r for r in original_rows}
    if any(current[task_id, 'selector_current']['diff'] != row['diff']
           for task_id, row in primary_diff.items()):
        raise ValueError('Source diff changed')

    write_new(SETUP / 'requests.jsonl', rows, jsonl=True)
    write_new(SETUP / 'private_labels.json', labels)
    write_new(SETUP / 'source_audit.json', audits)
    dependencies = json.loads((PRIMARY / 'dependencies.json').read_text()) | {
        'source_cap': 800,
        'reserved_cap': 400,
        'selector_sha256': digest(ROOT / 'ace_review/selector_ablation.py'),
        'auto_context_sha256': digest(ROOT / 'ace_review/auto_context.py'),
        'corrected_index_freeze_sha256': digest(INDEX_SOURCE / 'freeze.json'),
        'primary_run_freeze_sha256': digest(PRIMARY / 'freeze.json'),
    }
    write_new(SETUP / 'dependencies.json', dependencies)
    manifest = {
        'cohort': 'Previously observed Click and AnyIO development cases; no holdout',
        'arms': list(ARMS),
        'rows': len(rows),
        'calls_cap': 96,
        'index_source': str(INDEX_SOURCE),
        'primary_source': str(PRIMARY),
        'corrected_indices_reproduce_primary_contexts': True,
        'supersedes': 'selector_ablation_setup1/run1 (zero model calls; pre-run dedup-boundary review)',
        'indices': index_audits,
        'setup_script_sha256': digest(Path(__file__)),
    }
    write_new(SETUP / 'case_manifest.json', manifest)
    freeze(SETUP)
    prepared = prepare(SETUP / 'requests.jsonl', RUN)
    print(json.dumps({'prepared_rows': prepared, 'planned_calls': prepared * 6}, indent=2))


if __name__ == '__main__':
    main()
