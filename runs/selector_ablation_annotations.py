"""Explicit author decisions after reading every selector-ablation raw output."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ace_review.counterfactual import write_new  # noqa: E402

RUN = ROOT / 'runs/selector_ablation2'

# Lists are indexed by frozen input number (0..15); values are response item indices.
TARGET = {
    ('proposal', 1): [[], [], [], [], [1], [], [0], [], [0], [], [], [], [0], [0], [0], [0]],
    ('verify', 1): [[0], [0], [0], [], [0], [], [0], [], [], [], [], [], [], [], [0], [0]],
    ('proposal', 2): [[], [], [], [], [0], [0], [], [], [], [], [], [], [0, 1], [0, 1], [0], [0, 1, 2]],
    ('verify', 2): [[], [], [], [], [0], [0], [], [], [], [], [], [], [], [], [0], [0]],
    ('proposal', 3): [[0], [], [], [], [], [], [0], [], [0], [0], [], [], [0], [0], [0], [0]],
    ('verify', 3): [[], [], [], [], [0], [], [0], [], [], [], [], [], [], [], [0], [0]],
}

# The recurrent Click false mechanism says isinstance(..., typing.BinaryIO)
# raises. In the witnessed runtime it returns False; the failure occurs later
# when str is written to the unwrapped binary temporary file.
WRONG_MECHANISM = {
    ('proposal', 1): [[], [0], [], [], [0], [0], [], [], [], [], [], [], [], [], [], []],
    ('verify', 1): [[], [], [], [], [], [0], [], [], [], [], [], [], [], [], [], []],
    ('proposal', 2): [[0], [0], [], [0], [], [], [0], [0], [], [], [], [], [], [], [], []],
    ('verify', 2): [[0], [0], [], [0], [], [], [0], [], [], [], [], [], [], [], [], []],
    ('proposal', 3): [[], [0], [0], [0], [], [0], [], [], [], [], [], [], [], [], [], []],
    ('verify', 3): [[0], [0], [0], [0], [], [0], [], [], [], [], [], [], [], [], [], []],
}


def main():
    if set(TARGET) != set(WRONG_MECHANISM) or len(TARGET) != 6:
        raise ValueError('All stage/repeat blocks must be reviewed')
    annotations = []
    for (stage, repeat), targets in sorted(TARGET.items()):
        wrong_rows = WRONG_MECHANISM[stage, repeat]
        if len(targets) != 16 or len(wrong_rows) != 16:
            raise ValueError('Expected all sixteen frozen inputs')
        for index, target_indices in enumerate(targets):
            path = RUN / f'raw/{stage}_{index:03}_r{repeat}.jsonl'
            raw = json.loads(path.read_text())
            items = raw['response']['hypotheses' if stage == 'proposal' else 'issues']
            wrong_indices = wrong_rows[index]
            if set(target_indices) & set(wrong_indices):
                raise ValueError('Target and wrong-mechanism labels overlap')
            if any(type(i) is not int or not 0 <= i < len(items)
                   for i in target_indices + wrong_indices):
                raise ValueError(f'Invalid item index: {stage}/{repeat}/{index}')
            annotations.append({k: raw[k] for k in ('task_id', 'condition', 'repeat')} | {
                'stage': stage,
                'target_indices': target_indices,
                'wrong_mechanism_indices': wrong_indices,
                'raw_file': path.name,
                'reviewer': 'session author, not independent adjudicator',
            })
    write_new(RUN / 'annotations.json', annotations)
    print(json.dumps({'annotated_outputs': len(annotations),
                      'target_items': sum(len(a['target_indices']) for a in annotations),
                      'wrong_mechanism_items': sum(len(a['wrong_mechanism_indices']) for a in annotations)}))


if __name__ == '__main__':
    main()
