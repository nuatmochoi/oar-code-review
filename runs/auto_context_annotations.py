"""Explicit author decisions after reading each raw output, not an LLM judge."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ace_review.counterfactual import write_new

RUN = ROOT / 'runs/auto_context2'
# Lists are indexed by frozen input number (0..15). Values are issue/hypothesis indices.
TARGET = {
    ('proposal', 1): [[], [0], [0], [], [0], [], [], [], [0], [], [0, 1], [0], [0, 1], [0], [0], [0]],
    ('verify', 1): [[], [0], [], [], [0], [], [], [], [], [], [], [], [], [0], [], []],
    ('proposal', 2): [[], [], [], [], [], [], [], [], [2], [], [], [0], [0, 1, 2], [0], [0], [0]],
    ('verify', 2): [[], [], [0], [], [0], [], [], [0], [], [], [], [], [], [0], [], []],
    ('proposal', 3): [[0], [0], [], [], [], [], [], [0], [0], [], [0], [0], [0], [0], [0], [0]],
    ('verify', 3): [[0], [], [], [0], [], [0], [0], [], [], [], [], [], [0], [0], [], []],
}
WRONG_MECHANISM = {
    ('proposal', 1): [[0], [], [], [0], [], [0], [0], [0], [], [], [], [], [], [], [], []],
    ('verify', 1): [[0], [], [0], [0], [], [0], [0], [0], [], [], [], [], [], [], [], []],
    ('proposal', 2): [[0], [0], [0], [0], [0], [], [0], [], [], [], [], [], [], [], [], []],
    ('verify', 2): [[0], [0], [], [0], [], [], [0], [], [], [], [], [], [], [], [], []],
    ('proposal', 3): [[], [], [], [0], [], [0], [0], [], [], [], [], [], [], [], [], []],
    ('verify', 3): [[], [0], [0], [], [], [], [], [0], [], [], [], [], [], [], [], []],
}


def main():
    if len(TARGET) != 6 or set(TARGET) != set(WRONG_MECHANISM):
        raise ValueError('All six stage/repeat blocks must be reviewed before finalization')
    annotations = []
    for (stage, repeat), targets in sorted(TARGET.items()):
        if len(targets) != 16 or len(WRONG_MECHANISM[stage, repeat]) != 16:
            raise ValueError('Expected all sixteen inputs')
        for index, indices in enumerate(targets):
            path = RUN / f'raw/{stage}_{index:03}_r{repeat}.jsonl'
            raw = json.loads(path.read_text())
            wrong = WRONG_MECHANISM[stage, repeat][index]
            items = raw['response']['hypotheses' if stage == 'proposal' else 'issues']
            if set(wrong) & set(indices) or any(not 0 <= i < len(items) for i in wrong + indices):
                raise ValueError('Invalid author item index')
            annotations.append({k: raw[k] for k in ('task_id', 'condition', 'repeat')} |
                               dict(stage=stage, target_indices=indices, wrong_mechanism_indices=wrong,
                                    raw_file=path.name, reviewer='session author, not independent adjudicator'))
    write_new(RUN / 'annotations.json', annotations)
    print({'annotated_outputs': len(annotations)})


if __name__ == '__main__':
    main()
