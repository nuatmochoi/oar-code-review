"""Evaluator-only post-hoc links between new physical source and gold coverage."""
import json
from pathlib import Path
from report_recovery import verified_rows
from report import covered
ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'reviewbench_changeunits_commit_query25_20261105'
def load(p):return json.loads(Path(p).read_text())
def armroot(a):return OLD/'arms/A'if a=='A'else ROOT/'arms'/a
def main():
    output=[]
    packets=[load(p)for p in sorted((ROOT/'public').glob('[0-9][0-9].json'))]
    for arm,reference in [('FLAT','A'),('PATH','FLAT'),('GROUPED','FLAT')]:
        ap=armroot(arm)/'scoring/results.details.json';bp=armroot(reference)/'scoring/results.details.json'
        if not ap.exists()or not bp.exists():continue
        ad=load(ap);bd=load(bp);added=covered(ad)-covered(bd);bykey={r['pr_key']:r for r in ad};records=[]
        for packet in packets:
            i=f'{packet["index"]:02d}';key=packet['key'];ard=armroot(arm)/'review'/i;brd=armroot(reference)/'review'/i
            ar=verified_rows(load(ard/'delivered_evidence.json'));br=verified_rows(load(brd/'delivered_evidence.json'))
            fresh={k for k in ar.keys()-br.keys()if ar[k].strip()}
            raw=load(ard/'decoded.json')['findings']
            for finding in bykey[key]['candidate_findings']:
                targets=[n for n in finding['covered_golden_indices']if(key,n)in added]
                if not targets:continue
                matches=[(j,f)for j,f in enumerate(raw)if finding['message'].startswith(f['title']+'\n\n')
                    and f['regression_anchor'].get('path')==finding['file']
                    and f['regression_anchor'].get('start_line')==finding['start_line']]
                cited=set()
                if len(matches)==1:
                    for loc in matches[0][1]['supporting_evidence']:
                        if loc.get('status')=='bound':
                            cited|={(loc['snapshot'],loc['path'],n)for n in range(loc['start_line'],loc['end_line']+1)}&fresh
                category=('new_nonblank_source_cited'if cited else
                          'no_new_physical_source'if not(ar.keys()-br.keys())else'new_source_available_but_not_cited')
                records.append(dict(PR=key,index=packet['index'],title=finding['message'].splitlines()[0],
                    official_status=finding['status'],new_gold_indices=targets,raw_match_count=len(matches),
                    mechanism_category=category,new_support_rows=[list(x)for x in sorted(cited)],
                    semantic_causal_contribution_proven=False))
        output.append(dict(comparison=arm+'-'+reference,added_gold_count=len(added),
            reports=records,new_source_linked_reports=sum(bool(x['new_support_rows'])for x in records),
            non_FP_source_linked_reports=sum(bool(x['new_support_rows'])and x['official_status']not in('matched_fp','novel_fp')for x in records)))
    result=dict(status='posthoc_source_citation_and_gold_cooccurrence_not_causal_ablation',comparisons=output,
        private_gold_evaluator_only=True,human_review=0,new_model_calls=0,
        limitations=['A supporting citation can be semantically irrelevant even if its physical row is valid.',
                    'Uncited new source can still influence a model; absence of citation is not proof of no effect.',
                    'Same physical source does not imply identical metadata or identical model outputs.',
                    'An official FP report can cover mixed gold indices; preserve its FP status rather than calling it a verified true report.'])
    (ROOT/'audit/incremental-findings.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps([{k:v for k,v in x.items()if k!='reports'}for x in output],ensure_ascii=False))
if __name__=='__main__':main()
