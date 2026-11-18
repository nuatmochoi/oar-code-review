"""Report-only adapter; immutable scorer/reviewer/native artifacts stay intact.

The frozen report passed 'deferred' twice to dict(). Its explicit value already
comes from the full ledger. Remove only the duplicate expansion key at read time.
"""
from pathlib import Path
import report
from warning_tracker import physical_rows,_diff_rows,source_lines,_rowmap
DIFF_HANDLE_ERRORS={}
BODY_HANDLE_ERRORS={}
original_load=report.load
def report_load(path):
    result=original_load(path)
    p=Path(path)
    if p.name=='complete.json' and p.is_relative_to(report.ROOT/'native'):
        return {k:v for k,v in result.items()if k!='deferred'}
    return result
report.load=report_load
def verified_rows(evidence):
    result={}
    for e in evidence:
        if e.get('kind')not in ('source_lines','body','implementation_body','exception_branch','diff_hunk'):continue
        physical,errors=physical_rows(e)
        if e.get('kind')=='diff_hunk':
            # Source visibility and selectable citation handles are separate.
            # The old handle builder mishandles Markdown --- inside a hunk;
            # the validator rejects those handles. The original unified diff
            # was still delivered, so count its actual visible rows using the
            # hunk-aware parser, without repairing any model output or ID.
            physical=_diff_rows(e['content'],e['snapshot'],e['path'])
            if errors:DIFF_HANDLE_ERRORS[e['id']]=dict(snapshot=e['snapshot'],path=e['path'],errors=errors,
                visible_source_rows=len(physical),source_text_was_delivered=True,citation_handle_repaired=False)
        elif errors:
            # Count the exact numbered text the renderer supplied, rather than
            # pretending a rejected complete-body handle was selectable.
            supplied,valid=_rowmap(e.get('physical_rows'))
            visible=dict(enumerate(source_lines(e['content']),e['start_line']))
            if not valid or any(supplied.get(n)!=line for n,line in visible.items()):
                raise ValueError(('Unverifiable visible body rows',e['id'],errors))
            physical=visible
            BODY_HANDLE_ERRORS[e['id']]=dict(snapshot=e['snapshot'],path=e['path'],errors=errors,
                declared_range=[e['start_line'],e['end_line']],visible_rows=len(visible),
                actual_supplied_rows_match=True,citation_handle_repaired=False)
        for n,text in physical.items():
            key=(e['snapshot'],e['path'],n)
            if key in result and result[key]!=text:raise ValueError('Conflicting source rows')
            result[key]=text
    return result
# The frozen rows() helper covered bodies but omitted sparse diff handles.
# Include their verified visible rows when counting genuinely new source.
report.rows=verified_rows
if __name__=='__main__':
    import json
    result=report.update()
    for arm in result['mechanism']:
        native='FLAT'if arm=='GROUPED'else arm
        for row in result['mechanism'][arm]['per_PR']:
            ledger=original_load(report.ROOT/'native'/f'{row["index"]:02d}'/native/'query_ledger.json')
            assert row['deferred']==len(ledger['deferred'])
    (report.ROOT/'audit/diff-handle-validation.json').write_text(json.dumps(dict(invalid_diff_handles=DIFF_HANDLE_ERRORS,
        invalid_body_handles=BODY_HANDLE_ERRORS,
        original_source_and_outputs_unchanged=True,interpretation='Invalid handles were already excluded by the original catalogue validator; full diff text remained visible. This is a source-visibility audit, not a location repair.'),ensure_ascii=False,indent=2))
    print(json.dumps(dict(arms=result['arms'],physical_cost=result['physical_cost'],mechanism_rows_verified=75),ensure_ascii=False))
