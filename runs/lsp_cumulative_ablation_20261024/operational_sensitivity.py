"""Post hoc common-transport-success sensitivity; never replaces primary F1."""
import json
from pathlib import Path
from score import summary, metric

R = Path(__file__).resolve().parent
read = lambda p: json.loads(p.read_text())
data, audit = read(R / 'scores.json'), read(R / 'audit_all.json')
key = lambda x: (x['case_id'], x['world'], x['repeat'])
# Exclude only known infrastructure/transport trajectory failures; a normal
# model answer with incorrect citations or cause stays in this secondary set.
bad = {key(x) for x in audit['rows'] if x['status'] != 'ok'}
arms = ['B0', 'B1', 'B2', 'F', 'G']
rows = [x for x in data['rows'] if key(x) not in bad]
totals = {a: summary([x for x in rows if x['arm'] == a]) for a in arms}
assert len({totals[a]['trajectories'] for a in arms}) == 1
out = {'status': 'post_hoc_sensitivity_only', 'primary_denominators_unchanged': True,
       'selection': 'Remove the union of infrastructure-failed case/world/repeat keys identically from all five arms. Normal but incorrect model responses remain.',
       'exclusion_trigger': 'Raw output directory was absent for initial6 transport entries. No lost response is recovered or inferred.',
       'excluded_keys': [list(x) for x in sorted(bad)], 'summary': totals,
       'strict_F1_delta_percentage_points': {f'{right}_minus_{left}': 100 * (totals[right]['grounded_F1']['F1'] - totals[left]['grounded_F1']['F1']) for left, right in zip(arms, arms[1:])},
       'limitation': 'This subset is post hoc and does not restore the original complete experiment. It does not replace the primary72-review/36-defect results or remove the historical-cohort and same-case limits.'}
(R / 'operational_sensitivity.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'excluded_keys': out['excluded_keys'], 'summary': totals}, ensure_ascii=False, indent=2))
