"""Offline, invertible catalogue encoding diagnostic. Never changes model inputs."""
from pathlib import Path
import json,hashlib,sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from experiment import load,tokens
from review_prompt import reviewer_input
from common import render_evidence
from location_ids import render_catalog
from diff48 import diff_evidence

def encode(catalog):
    out={}
    for eid,row in catalog['by_evidence'].items():
        runs=[]
        for n,lid in sorted((int(n),lid)for n,lid in row['rows'].items()):
            if runs and n==runs[-1][1]+1 and lid==runs[-1][2]+n-runs[-1][0]:runs[-1][1]=n
            else:runs.append([n,n,lid])
        out[eid]=dict(row_ID_runs=runs,contiguous_segments=row['contiguous_segments'])
    return out
def decode(compressed):
    return {eid:dict(rows={str(n):lid+n-a for a,b,lid in row['row_ID_runs']for n in range(a,b+1)},
                     contiguous_segments=row['contiguous_segments'])for eid,row in compressed.items()}
def main():
    results=[]
    for p in sorted((ROOT/'public').glob('[0-9][0-9].json')):
        packet=load(p);context=load(ROOT/'native'/p.stem/'FLAT/context.json')
        raw,schema,evidence,catalog=reviewer_input(packet['case'],context)
        compact=encode(catalog);assert decode(compact)==catalog['by_evidence']
        before=tokens(render_catalog(catalog));after=tokens(compact)
        results.append(dict(index=packet['index'],PR=packet['key'],
            original_unified_diff_tokens=tokens(packet['case']['diff']),
            diff_citation_handle_tokens=tokens(render_evidence(diff_evidence(packet['case']['diff']))),
            rendered_all_evidence_tokens=tokens(render_evidence(evidence)),
            catalogue_tokens=before,lossless_run_catalogue_tokens=after,catalogue_tokens_saved=before-after,
            all_location_IDs_exactly_roundtrip=True,
            current_base_prompt_tokens=tokens(raw['prompt']),
            hypothetical_base_prompt_tokens=tokens(raw['prompt'])-before+after,
            current_model_request_changed=False))
    totals={k:sum(x[k]for x in results)for k in ['original_unified_diff_tokens','diff_citation_handle_tokens',
        'rendered_all_evidence_tokens','catalogue_tokens','lossless_run_catalogue_tokens',
        'catalogue_tokens_saved','current_base_prompt_tokens','hypothetical_base_prompt_tokens']}
    out=dict(status='offline_encoding_diagnostic_not_a_reviewer_or_F1_experiment',PRs=25,
        notes=['No source row, evidence ID, or location ID is removed by catalogue run encoding.',
               'Model usability and citation error rate of the encoding are untested.',
               'Hypothetical counts omit any decoder-instruction overhead and other final prompt additions.',
               'Diff source text already occurs once. The separately measured diff-citation handles are metadata, not a second copy of diff source.',
               'No savings from this audit are applied to the running frozen comparison.'],
        totals=totals,per_PR=results,new_model_calls=0)
    (ROOT/'audit/context-encoding.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(json.dumps(dict(totals=totals,largest_PR=max(results,key=lambda x:x['current_base_prompt_tokens'])),ensure_ascii=False))
if __name__=='__main__':main()
