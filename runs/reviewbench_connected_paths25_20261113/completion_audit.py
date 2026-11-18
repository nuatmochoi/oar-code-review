"""Read-only post-run checks. Does not modify frozen inputs or scored outputs."""
from pathlib import Path
import collections, copy, hashlib, json, socket, time
from experiment import ROOT, load, render_evidence
from freeze_run import verify


def main():
    frozen = verify()
    old = load(ROOT / 'audit/preservation.json')['files']
    changed = [p for p, h in old.items()
               if hashlib.sha256(Path(p).read_bytes()).hexdigest() != h]
    assert not changed, changed
    assert load(ROOT / 'status.json')['phase'] == 'complete'
    arms = {}
    keys = None
    for arm in ('FLAT', 'PATH', 'GROUPED'):
        folder = ROOT / 'arms' / arm
        completed = [load(p) for p in sorted((folder / 'review').glob('*/complete.json'))]
        assert len(completed) == 25
        assert all(x['token_cap_respected'] and x['logical_tokens'] <= 750000
                   and x['logical_active_wall_seconds'] <= 3600 for x in completed)
        scored = load(folder / 'scoring/results.json')['per_pr']
        this_keys = sorted(x['pr_key'] for x in scored)
        assert len(this_keys) == 25 and len(set(this_keys)) == 25
        if keys is None:
            keys = this_keys
        assert this_keys == keys
        arms[arm] = dict(review_completed=25, official_scored=25,
                         max_logical_model_tokens=max(x['logical_tokens'] for x in completed),
                         max_logical_active_wall_seconds=max(x['logical_active_wall_seconds'] for x in completed))

    order_checks = []
    for i in range(25):
        flat = ROOT / 'arms/FLAT/review' / f'{i:02d}'
        grouped = ROOT / 'arms/GROUPED/review' / f'{i:02d}'
        for name in ('delivered_evidence.json', 'locations.json', 'context_identity.json'):
            assert load(flat / name) == load(grouped / name), (i, name)
        evidence = load(flat / 'delivered_evidence.json')
        byid = {x['id']: x for x in evidence}
        assert len(byid) == len(evidence)
        normalized = []
        for folder in (flat, grouped):
            order = load(folder / 'delivery_order.json')['evidence_ids']
            assert len(order) == len(byid) and set(order) == set(byid)
            body = copy.deepcopy(load(folder / 'request.json'))
            rendered = render_evidence([byid[x] for x in order])
            user = body['input'][1]['content']
            assert user.count(rendered) == 1
            body['input'][1]['content'] = user.replace(rendered, '[SAME EVIDENCE FACT SET]', 1)
            body.pop('metadata', None)
            normalized.append(body)
        assert normalized[0] == normalized[1], i
        order_checks.append(dict(index=i, facts_identical=True, only_evidence_order_differs=True,
                                  order_changed=load(grouped / 'delivery_order.json')['order_changed']))

    costs = load(ROOT / 'audit/cost.json')
    receipts = []
    for admission in (ROOT / 'api').glob('*/admission.json'):
        receipt = admission.parent / 'receipt.json'
        assert receipt.is_file(), str(admission)
        receipts.append(load(receipt))
    assert len(receipts) == costs['physical_calls']
    assert not any(r['status'] in ('admitted_unknown', 'running') for r in receipts)
    assert costs['upper'] <= 70
    with socket.socket() as s:
        s.settimeout(.5)
        proxy_closed = s.connect_ex(('127.0.0.1', 8795)) != 0
    assert proxy_closed

    errors = load(ROOT / 'audit/diff-handle-validation.json')
    invalid = set(errors['invalid_diff_handles']) | set(errors['invalid_body_handles'])
    handle_occurrences = []
    for arm in ('FLAT', 'PATH', 'GROUPED'):
        for p in sorted((ROOT / 'arms' / arm / 'review').glob('*/delivered_evidence.json')):
            bad = sorted({x['id'] for x in load(p)} & invalid)
            if bad:
                handle_occurrences.append(dict(arm=arm, index=int(p.parent.name), evidence_ids=bad))
    result = dict(completed_at=time.time(), all_conditions_complete=True,
                  frozen_files_verified=len(frozen['files']), old_files_unchanged=len(old),
                  arms=arms, same_fact_actual_request_checks=order_checks,
                  GROUPED_order_changed_PRs=sum(x['order_changed'] for x in order_checks),
                  physical_cost={k:v for k,v in costs.items() if k != 'records'},
                  settled_receipt_statuses=dict(collections.Counter(r['status'] for r in receipts)),
                  active_admissions=0, proxy_closed=True,
                  invalid_diff_handles=len(errors['invalid_diff_handles']),
                  invalid_body_handles=len(errors['invalid_body_handles']),
                  affected_PR_indices=sorted({x['index'] for x in handle_occurrences}),
                  invalid_handle_occurrences=handle_occurrences,
                  citation_handles_repaired=False, model_or_scorer_outputs_changed=False,
                  transport_recovery='One exact-request retry; original HTTP 520 and unknown cost reservation retained.',
                  report_repairs='Separate adapter removes duplicate deferred field and counts actual visible diff/body source rows.',
                  human_review=0, new_model_calls=0)
    (ROOT / 'completion.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in result.items()
                      if k not in ('same_fact_actual_request_checks', 'invalid_handle_occurrences')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
