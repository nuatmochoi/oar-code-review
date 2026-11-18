"""Post-run author adjudication, not blind and not used by retrieval/reviewer."""
import json
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda name:json.loads((R/name).read_text())
a=read('audit_original.json');out={}
reason={
'E01':'Request-to-merge_setting binding and wire serialization are not visible. Local prepared-header witness is private and does not supply missing reviewer evidence.',
'E02':'Parsed host and public requests.get-to-prepare_url path are not shown in the packet; downstream result is unknown. Private preparation witness cannot rescue the packet.',
'E03':'Visible byte input, changed _encode_params branch and public failure reproduction establish binary-body regression. Local Request.prepare execution corroborates UnicodeDecodeError versus preserved bytes; no remote HTTP run is claimed.',
'E04':'For public bGET method input, builtin_str loses native method decoding; public problem reports the resulting incorrect request. Witness observes prepared method BGET representation versus GET only, no remote404 test. Forward reviewer correctly notes missing normalization helper evidence and abstains.',
'E05':'Visible EncodedFile.mode/underlying mode, delegation and write logic establish binary-mode reporting. Actual EncodedFile mode witness rb+ versus r+ corroborates this at the wrapper boundary, not a full youtube-dl run.',
'E06':'Visible TextIOWrapper newline change, fixture-to-FDCapture path and public carriage-return reproduction establish CR-to-LF regression. Actual FDCapture witness corroborates CR and CRLF outputs. One issue may validly have multiple checks.',
'E07':'LSP retrieves the actual handler.clear/reset bodies and shared-stage list relation; AST does not close the implementation link. Actual fixture and handler witness confirms stale call-stage records versus in-place clearing, with minimal item storage boundary.',
'E08':'Visible single set_level(42) public input and restoration lifecycle establish the target handler-level regression. Actual fixture witness uses41 instead of42 and establishes the same save/restore mechanism; no runner-wide result is inferred from the private witness alone.',
'E09':'Output-format diagnostic only: source mapping is visible but dispatcher/fallback is missing. Both versions can generate code in the actual fixed checkout; do not treat the public older unsupported output as current fixed-revision evidence.',
'E10':'Public Contains.as_set entry is named, but reviewer cannot establish base/head dispatch and output from the supplied source. Private witness sees NotImplementedError versus Reals; do not rescue review with private output.',
'E11':'Visible iterable-input reproduction and recursive symbols branch establish preservation/loss of cls=Function. Actual nested symbol witness corroborates UndefinedFunction versus Symbol. LSP forward abstention reflects its missing context, not an actual code regression.',
'E12':'Public traceback and visible _check_vector establish initial integer0 in sum dispatches to the changed add guard. Private operator witness corroborates TypeError versus identity. Distinguish sum start0 from the separate zero Vector produced by0*N.x.'}
for x in a['rows']:
 rid=x['run_id'];review=x['review'];status=review['status'] if review else 'operational_failure';idx=list(range(len(review['issues']))) if review else []
 judged={'supported_issue_indices':idx,'unsupported_issue_indices':[],'verdict_supported':status not in ['insufficient_evidence','operational_failure'],'abstention_justified':status=='insufficient_evidence','reason':reason[x['case_id']]}
 if rid=='E12_a_lsp':
  judged.update(supported_issue_indices=[],unsupported_issue_indices=idx,verdict_supported=False,target_location_recognized=True,reason='Correct target location, but causal scenario falsely says0*N.x supplies scalar integer0 to __add__. It actually produces a zero Vector; the failing scalar is sum default start0. Native static definitions do not establish the claimed operand. Preserve as target recognition but fail strict supported scenario. Posthoc vector_dispatch_check confirms both operand type and full sum result.')
 out[rid]=judged
(R/'annotations.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print('Adjudicated',len(out),'logical reviews.')
