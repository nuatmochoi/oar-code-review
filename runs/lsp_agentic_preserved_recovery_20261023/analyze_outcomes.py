"""Descriptive changes in reviewed outcomes; no new inference or model calls."""
import collections,hashlib,json
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
s=read(R/'scores.json');annotations=read(R/'author_annotations.json')
rows=s['rows'];byid={x['run_id']:x for x in rows}
def f1(rr):
 tp=sum(x['grounded_tp'] for x in rr);fp=sum(x['grounded_fp'] for x in rr);fn=sum(x['grounded_fn'] for x in rr)
 return {'TP':tp,'FP':fp,'FN':fn,'F1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0}
by_arm={}
for arm in ['F','G']:
 rr=[x for x in rows if x['arm']==arm];unsafe=[x for x in rr if x['label']=='unsafe']
 by_arm[arm]={'unsafe_first_final_states':dict(collections.Counter(x['first_target_state']+' -> '+x['final_target_state'] for x in unsafe)),
 'final_strict_TP_without_first_supported_finding':[x['run_id'] for x in unsafe if x['grounded_tp'] and x['first_target_state']!='reported_supported'],
 'first_supported_finding_without_final_strict_TP':[x['run_id'] for x in unsafe if x['first_target_state']=='reported_supported' and not x['grounded_tp']],
 'per_repeat':{str(rep):f1([x for x in rr if x['repeat']==rep]) for rep in [1,2,3]}}
pairs=[]
for f in rows:
 if f['arm']!='F' or f['label']!='unsafe':continue
 g=byid[f['run_id'].replace('_F_','_G_')]
 pairs.append({'case_id':f['case_id'],'repeat':f['repeat'],'F':f['run_id'],'G':g['run_id'],'F_first_state':f['first_target_state'],'G_first_state':g['first_target_state'],'F_TP':f['grounded_tp'],'G_TP':g['grounded_tp'],'F_FP':f['grounded_fp'],'G_FP':g['grounded_fp'],'both_first_unresolved':f['first_target_state']==g['first_target_state']=='unresolved'})
source_groups=collections.defaultdict(list)
for x in rows:
 p=R/'retrieval'/f"{x['run_id']}.json"
 if not p.exists():continue
 source=read(p)['source'];key=(x['case_id'],x['world'],x['arm'],hashlib.sha256(source.encode()).hexdigest())
 source_groups[key].append({'run_id':x['run_id'],'grounded_tp':x['grounded_tp'],'grounded_fp':x['grounded_fp'],'safe_grounded':x['safe_grounded'],'abstained':x['abstained'],'final_target_state':x['final_target_state']})
variation=[]
for (case,world,arm,h),rr in source_groups.items():
 signatures={(x['grounded_tp'],x['grounded_fp'],x['safe_grounded'],x['abstained']) for x in rr}
 if len(rr)>1 and len(signatures)>1:variation.append({'case_id':case,'world':world,'arm':arm,'final_source_sha256':h,'rows':rr})
out={'by_arm':by_arm,'matched_unsafe_pairs':pairs,'matched_counts':dict(collections.Counter('both' if x['F_TP'] and x['G_TP'] else 'G_only' if x['G_TP'] else 'F_only' if x['F_TP'] else 'neither' for x in pairs)),'identical_final_source_with_different_outcomes':variation,'limitations':['First-response outcomes are manually annotated and are descriptive; final strict counts additionally enforce machine evidence-ID validation.','F/G have identical first prompts but independent model responses. Matched final outcome differences do not isolate retrieval from model randomness.','Identical final source is not an identical final prompt: first-review hypotheses, observations, and requests may differ. No fixed-response causal replay was run.']}
(R/'outcome_analysis.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({'by_arm':by_arm,'matched_counts':out['matched_counts'],'variable_source_groups':len(variation)},ensure_ascii=False,indent=2))
