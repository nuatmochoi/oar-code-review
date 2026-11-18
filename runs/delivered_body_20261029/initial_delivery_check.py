"""Read-only, model/native-free reproduction of all prepared seed deliveries.

``run()`` returns JSON-serializable results. The caller owns report persistence.
This renders the product's shared/packet/tail parts; it does not pretend those
parts include the completion client's system/schema/query-budget additions.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path

import delivery_rendering as delivery
import frontier_builder as builder
import oar_core as core
import oar_product_adapter as adapter


HERE = Path(__file__).resolve().parent


def _sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _visible_store(expected, observation, restrict_ids=None):
    """Reconstruct only exact-text lines already verified by the observer."""
    out = core.EvidenceStore()
    for span in observation['spans']:
        if restrict_ids is not None and span['evidence_id'] not in restrict_ids:
            continue
        ev = expected.get(span['evidence_id'])
        numbers = set(span['lines'])
        lines = tuple((n, text) for n, text in ev.lines if n in numbers)
        if lines:
            out.add(core.Evidence(ev.id, ev.snapshot, ev.file, lines=lines))
    return out


def _render(inputs, product, mode, expected, core_ids):
    patch = delivery.fixed_rendering(product) if mode == 'fixed' else contextlib.nullcontext()
    with patch, adapter.seed_rendering(inputs, product):
        items = product.prompt_context.select_prompt_context(inputs['packet'])
        shared, prompt, suffix = product.llm_reviewer._render_prompt_parts(
            inputs['diff'], inputs['packet'], review_context=inputs['review_context'])
        parts = dict(shared=shared, prompt=prompt, suffix=suffix)
        rendered_ids = adapter.check_rendered_seed_ids(inputs, '\n'.join(parts.values()))
        expected_ids = [e.id for e in inputs['seed_evidence']]
        assert len(rendered_ids) == len(set(rendered_ids)) and sorted(rendered_ids) == sorted(expected_ids)
    observed = delivery.observe_delivery(parts, expected)
    visible = _visible_store(expected, observed)
    visible_core = _visible_store(expected, observed, core_ids)
    expected_core = core.EvidenceStore()
    for eid in core_ids:
        expected_core.add(expected.get(eid))
    characters = sum(len(item.rendered) for item in items)
    cap = product.prompt_context.context_budget_chars(inputs['packet'])
    row = {
        'mode': mode,
        'seed_ids_exactly_once': True,
        'seed_evidence_ids': rendered_ids,
        'source_evidence_count': observed['source_evidence_count'],
        'complete_source_evidence_count': observed['complete_source_evidence_count'],
        'incomplete_source_evidence_ids': observed['incomplete_source_evidence_ids'],
        'delivery': observed,
        'expected_source_union_sha256': expected.union_hash(),
        'actual_visible_source_union_sha256': visible.union_hash(),
        'source_union_equal': expected.union_hash() == visible.union_hash(),
        'expected_core_ast_union_sha256': expected_core.union_hash(),
        'actual_visible_core_ast_union_sha256': visible_core.union_hash(),
        'core_ast_equal': expected_core.union_hash() == visible_core.union_hash(),
        'core_ast_evidence_ids': sorted(core_ids),
        'source_reference_tokens': expected.union_tokens(),
        'source_reference_token_cap': 5200,
        'rendered_context_chars': characters,
        'context_budget_chars': cap,
        'context_within_budget': characters <= cap,
        'actual_raw_parts_sha256': {name: _sha_bytes(text.encode()) for name, text in parts.items()},
        'actual_raw_parts_object_sha256': core._sha(parts),
        'rendered_prompt_sha256': _sha_bytes((shared + prompt + suffix).encode()),
        'raw_parts_scope': 'Actual product _render_prompt_parts output; excludes completion-client additions.',
    }
    return row


def run():
    """Validate old/fixed rendering of 24 copied direction packets, no writes."""
    report = {'schema_version': 1, 'passed': False, 'errors': [], 'directions': [],
              'model_calls': 0, 'native_lsp_calls': 0,
              'scope': 'Read-only copied prepared packets and actual product renderer; no preparation or review calls.'}
    try:
        product = adapter.load_product()
        paths = sorted((HERE / 'prepared' / 'inputs').glob('*.json'))
        before = {str(p.relative_to(HERE)): _sha_bytes(p.read_bytes()) for p in paths}
        if len(paths) != 24:
            report['errors'].append({'kind': 'direction_count', 'expected': 24, 'actual': len(paths)})
        for path in paths:
            record = json.loads(path.read_text())
            key = record['key']
            row = {'key': key, 'prepared_record_sha256': before[str(path.relative_to(HERE))]}
            try:
                packet = record['body_packet']
                row['prepared_body_packet_sha256'] = core._sha(packet)
                inputs = builder.build_inputs(key, 'body_lsp', prepared_packet=packet, product=product)
                expected = core.EvidenceStore()
                for ev in inputs['seed_evidence']:
                    expected.add(ev)
                core_ids = {x['id'] for x in packet['ast']['snippets']}
                descriptor_sha = packet['lsp']['source_union_sha256']
                row['prepared_source_union_sha256'] = descriptor_sha
                if expected.union_hash() != descriptor_sha:
                    report['errors'].append({'kind': 'prepared_source_hash', 'key': key})
                for mode in ('old', 'fixed'):
                    rendered = _render(inputs, product, mode, expected, core_ids)
                    row[mode] = rendered
                    if not rendered['core_ast_equal']:
                        report['errors'].append({'kind': 'core_ast_delivery', 'key': key, 'mode': mode})
                    if not rendered['context_within_budget'] or rendered['source_reference_tokens'] > 5200:
                        report['errors'].append({'kind': 'budget', 'key': key, 'mode': mode})
                    if mode == 'fixed' and (rendered['incomplete_source_evidence_ids'] or not rendered['source_union_equal']):
                        report['errors'].append({'kind': 'fixed_source_incomplete', 'key': key})
                if core._sha(packet) != row['prepared_body_packet_sha256']:
                    report['errors'].append({'kind': 'in_memory_packet_changed', 'key': key})
            except Exception as exc:
                report['errors'].append({'kind': 'direction_exception', 'key': key,
                                         'type': type(exc).__name__, 'message': str(exc)})
            report['directions'].append(row)
        after = {str(p.relative_to(HERE)): _sha_bytes(p.read_bytes()) for p in paths}
        report['prepared_records_unchanged'] = before == after
        report['prepared_record_hashes'] = before
        if before != after:
            report['errors'].append({'kind': 'prepared_records_changed'})
        summary = {}
        for mode in ('old', 'fixed'):
            rows = [x[mode] for x in report['directions'] if mode in x]
            partial = [x['key'] for x in report['directions'] if mode in x and x[mode]['incomplete_source_evidence_ids']]
            summary[mode] = {
                'directions_rendered': len(rows), 'incomplete_directions': partial,
                'incomplete_direction_count': len(partial),
                'incomplete_source_evidence_count': sum(len(x['incomplete_source_evidence_ids']) for x in rows),
                'source_evidence_count': sum(x['source_evidence_count'] for x in rows),
                'complete_source_evidence_count': sum(x['complete_source_evidence_count'] for x in rows),
                'max_context_chars': max((x['rendered_context_chars'] for x in rows), default=None),
                'max_source_reference_tokens': max((x['source_reference_tokens'] for x in rows), default=None),
                'all_core_ast_preserved': all(x['core_ast_equal'] for x in rows) and len(rows) == 24,
                'all_source_unions_equal': all(x['source_union_equal'] for x in rows) and len(rows) == 24,
            }
        report['summary'] = summary
        # Post-freeze diagnostic expectations, never fed to retrieval/model.
        if summary['old']['incomplete_direction_count'] != 4 or summary['old']['incomplete_source_evidence_count'] != 4:
            report['errors'].append({'kind': 'legacy_defect_reproduction', 'expected': 4})
        if summary['fixed']['incomplete_direction_count'] != 0:
            report['errors'].append({'kind': 'fixed_incomplete_directions'})
    except Exception as exc:
        report['errors'].append({'kind': 'check_exception', 'type': type(exc).__name__, 'message': str(exc)})
    report['passed'] = not report['errors']
    return report


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False))
