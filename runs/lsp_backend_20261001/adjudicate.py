"""Post-run, unblinded author adjudication; does not modify frozen inputs."""
import json
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda name:json.loads((R/name).read_text())
a=read('audit.json'); labels=read('labels.json'); out={}
reasons={
'F01':'Visible supplied index and changed bounds check establish the scoped behavior.',
'F02':'Factory body, supplied inputs and changed slice bound are visible in the packet.',
'F07':'Optional regex group can be None for the supplied input; changed operation raises TypeError.',
'H01':'Visible fixed provider configuration and removal of vertexai=False establish the change.',
'H02':'Visible supplied thought-token count and response fields establish the token-count change.',
'H03':'Visible Alpha/alpha inputs and case flag establish the scoped equality result.',
'H04':'Visible end=2 and string establish exclusive versus inclusive slice output.',
'N01':'Visible Both mode and whitespace input establish strip versus lstrip output.',
'N02':'Visible repeated match input establishes replace-all versus replace-first output.',
'N03':'Visible lowercase mode and input establish lower versus upper output.',
'N04':'Visible Starts With mode and abc/a establish startswith versus endswith output.',
'W01':'Visible conditional factory body, argument function and execute body establish dispatch and concatenation output; function-body witnesses corroborate outputs.',
'W02':'Visible inheritance header, fixed arguments and execute body establish the scoped search/match difference; function-body witnesses corroborate outputs.',
'W03':'LSP native observations resolve the reexport to StringLength; visible input and execute body establish length including whitespace. Safe input produces the same length before/after stripping. Function-body witnesses corroborate outputs. W03_b_lsp omits E-NAV from citations for the binding fact, recorded as a citation-completeness limitation, not absence of input evidence.',
}
for x in a['rows']:
 rid=x['run_id']; cid=x['case_id']; status=x['review']['status']; indices=list(range(len(x['review']['issues'])))
 judged={'supported_issue_indices':indices,'unsupported_issue_indices':[], 'verdict_supported':status!='insufficient_evidence', 'abstention_justified':status=='insufficient_evidence', 'reason':reasons.get(cid,'The safe-world no-issue conclusion is supported within the fixed patch/input scope.')}
 if status=='insufficient_evidence':
  judged['reason']={'F03':'ORM empty-query truthiness and evaluation behavior are not supplied; a hypothetical None witness does not establish actual database behavior.', 'F04':'Actual generic connection receiver is unresolved; an unused import does not prove backend dispatch.', 'P08':'Explicit PNG path alone does not supply IMG_EXTENSIONS and ONE_HOUR globals required to establish the changed branch/header.', 'W03':'AST does not establish the reexport binding in the supplied evidence.', 'W04':'Property getter body and actual receiver dispatch are absent; static return-type candidate alone is insufficient.'}.get(cid,'Evidence does not close the claimed execution path.')
 if rid=='F03_b_lsp':
  judged.update(supported_issue_indices=[],unsupported_issue_indices=indices,verdict_supported=False,reason='The review assumes an empty ORM query is false and returns None. Native APIToken.query resolution does not establish this unrelated database behavior; the assumption is not supported by the supplied packet.')
 if cid=='F08' and x['world']=='a':
  judged.update(supported_issue_indices=[],unsupported_issue_indices=indices,verdict_supported=False,reason='The review itself says request.path and IMG_EXTENSIONS are unknown but asserts the image-extension branch and max-age=3600. Request path, extension-set membership and ONE_HOUR value are not established; an ungrounded alarm is not a grounded detection.')
 if rid=='W04_a_lsp':
  judged.update(verdict_supported=False,reason='No issue was reported, so this is not a false-positive issue. However, the unchanged-output ledger assumes actual property receiver dispatch from a static definition candidate while the getter body is absent; mark the positive safety assertion unsupported, consistently with W04_b abstention.',unsupported_ledger_assertions=['Actual Wrapper.engine receiver invokes RegexReplace.execute.'])
 out[rid]=judged
(R/'annotations.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print('Adjudicated',len(out),'logical reviews; author-visible, not blinded.')
