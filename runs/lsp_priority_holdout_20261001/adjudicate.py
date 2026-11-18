"""Explicit author grading after inspecting every saved response; no model input mutations."""
import json,sys
from pathlib import Path
R=Path(__file__).resolve().parent;stage=sys.argv[1] if len(sys.argv)>1 else 'all';read=lambda p:json.loads(p.read_text());a=read(R/f'audit_{stage}.json');notes=read(R/'adjudication_notes.json');inspected=set(read(R/'reviewed_ids.json'));out={}
reasons={'H01':'Request.prepare GET/HEAD Content-Length default contract and directly visible body/method conditional.','H02':'Skipped unittest class with --pdb contract; actual skip predicate and teardown control flow must be visible, not assumed from hidden witness.','H03':'Public is_zero query must not falsely conclude False for an expression simplifying to zero; visible complex-number assumption branch.','H04':'kernS rational expression public API; branch-specific variable initialization and direct use establish UnboundLocalError.'}
for x in a['rows']:
 rid=x['run_id'];assert rid in inspected,rid;v=x['review'];idx=list(range(len(v['issues']))) if v else [];supported=idx;extra=[];verdict=v is not None and v['status']!='insufficient_evidence';reason=reasons[x['case_id']]
 if rid in notes:
  note=notes[rid];extra=note['unsupported_issue_indices'];supported=[i for i in idx if i not in extra];verdict=note['verdict_supported'];reason=note['reason']
 out[rid]={'supported_issue_indices':supported,'unsupported_issue_indices':extra,'verdict_supported':verdict,'reason':reason,'author_visible':True,'operational_valid':x['status']=='ok' and not x['errors']}
(R/f'annotations_{stage}.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print('Adjudicated',len(out),'logical reviews.')
