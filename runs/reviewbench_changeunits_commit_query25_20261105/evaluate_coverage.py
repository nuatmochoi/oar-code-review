"""Private post-hoc audit, executed only after retrieval/prompt/code freeze."""
import collections,json
from pathlib import Path
from experiment import ROOT,load,write
from freeze_run import verify
from report import covered
def main():
 verify();old=ROOT.parent/'reviewbench_official25_lsp_20261105';found=covered(load(old/'scoring/results.details.json'));rows=[]
 for ref in load(ROOT/'public/manifest.json'):
  p=load(ref['path']);gold=load(ROOT/'upstream/golden'/f'{p["key"]}.json')['findings']
  for i,g in enumerate(gold):
   if g['tp_fp']!='tp':continue
   path=g['file'];a=g['start_line'];b=g['end_line'];valid=type(a)is int and type(b)is int and 1<=a<=b
   spans=[u for u in p['units']if u['path']==path]
   in_diff=valid and any(u['head_start']<=a<=b<=u.get('head_end',-1)for u in spans)
   body=valid and any(e.get('snapshot')=='HEAD'and e.get('path')==path and e.get('mechanical_checks',{}).get('complete_unit')and e['start_line']<=a<=b<=e['end_line']for e in p['prefix']['evidence'])
   hit=(p['key'],i)in found
   bucket='additional_complete_body_at_anchor'if body else'diff_at_anchor_only'if in_diff else'anchor_not_in_diff_or_additional_complete_body'
   rows.append(dict(key=p['key'],gold_index=i,covered=hit,category=g['category'],severity=g['severity'],scope=g.get('scope'),file=path,start_line=a,end_line=b,anchor_in_diff=in_diff,complete_body_at_anchor=body,anchor_bucket=bucket))
 assert len(rows)==352 and sum(r['covered']for r in rows)==37
 summary=dict(gold=352,covered=37,missed=315,missed_anchor_buckets=dict(collections.Counter(r['anchor_bucket']for r in rows if not r['covered'])),missed_categories=dict(collections.Counter(r['category']for r in rows if not r['covered'])),interpretation='Anchor availability only; does not establish all behavior evidence was present or prove why the model missed it.',private_not_reviewer_input=True)
 write(ROOT/'private/old_gold_coverage.json',dict(summary=summary,rows=rows));print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
