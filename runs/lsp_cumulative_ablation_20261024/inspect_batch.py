"""Print complete ungraded model responses for manual reading; never grade."""
import argparse
import json
from pathlib import Path
import experiment as e

R = Path(__file__).resolve().parent
p = argparse.ArgumentParser()
p.add_argument('arm', choices=e.ARMS)
p.add_argument('limit', type=int, nargs='?', default=4)
p.add_argument('--case')
p.add_argument('--evidence', action='store_true')
a = p.parse_args()
ap = R / f'author_annotations_{a.arm}.json'
ann = e.read(ap) if ap.exists() else {}
labels = e.read(R / 'labels.json')
count = 0
for path in sorted((R / 'trajectories').glob(f'*_{a.arm}_r*.json')):
    if path.stem in ann:
        continue
    job = e.read(path)
    if a.case and job['case_id'] != a.case:
        continue
    print('\n###', path.stem, labels[job['case_id']]['worlds'][job['world']], job['status'])
    if a.evidence:
        print(e.read(R / 'cases' / f'{job["case_id"]}_{job["world"]}.json')['common'])
    for stage in [1, 2]:
        resolved = e.resolve_stage(job, stage)
        if a.evidence:
            packet = e.read(e.packet_path(job, stage))
            print('SUPPLIED_STAGE', stage, packet['source'], packet['plain'])
        raw = e.stage_record(job, stage)
        print('STAGE', stage, 'REUSED', resolved['reused'], 'RAW_PATH', str(resolved['raw_path']))
        print(json.dumps(raw.get('response') if raw and raw.get('response') else raw, ensure_ascii=False, indent=2))
    count += 1
    if count >= a.limit:
        break
print('FULL_RESPONSE_REVIEW_COUNT', count)
