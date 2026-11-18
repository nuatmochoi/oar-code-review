"""Fresh change-driven native packets over the unchanged legacy AST core.

The caller prepares the legacy policy separately. This module executes only the
enhanced policy. Its descriptor is shared by body_lsp/reactive/proactive. Physical
preparation costs remain separate from per-review query allocations.
"""
from __future__ import annotations

import copy
import dataclasses

import packet_builder as pb
from body_frontier import BodyFrontier

ARMS = ('diff', 'ast', 'legacy_lsp', 'body_lsp', 'reactive', 'proactive')
INITIAL_METADATA_CAP = 480
SOURCE_CAP = pb.SOURCE_CAP
METADATA_CAP = pb.METADATA_CAP
VERSION = 1


def _source_spans(desc):
    return [{'path': s['path'], 'snapshot': s['snapshot'], 'lines': s['lines']} for s in desc['snippets']]


def _enhanced(inputs, ast_desc, store, head_info, resolver_factory=None):
    root, key = inputs['data']['workspace_root'], inputs['key']
    frontier = BodyFrontier(root)
    frontier.supply(_source_spans(ast_desc))
    head_text = {path: text for path, text, *_ in head_info}
    for file in sorted(inputs['diff'].files, key=lambda f: f.path):
        if file.path not in head_text:
            continue
        head = head_text[file.path]
        added = sorted({x.new_lineno for h in file.hunks for x in h.lines if x.kind == 'add'})
        deleted = sorted({x.old_lineno for h in file.hunks for x in h.lines if x.kind == 'delete'})
        try:
            frontier.seed_file(file.path, head, pb.reverse_diff(head, file), added, deleted)
        except (ValueError, SyntaxError) as exc:
            frontier.data['events'].append({'kind': 'changed_file_parse_unavailable', 'path': file.path,
                                             'error': type(exc).__name__})
    snippets = copy.deepcopy(ast_desc['snippets'])
    requests, resolver, meter, status = [], None, None, {}
    anchor = frontier.native_anchor()
    try:
        if anchor is not None:
            req = {'id': 'frontier-initial-locator', 'operation': anchor['operation'],
                   'anchor': copy.deepcopy(anchor), 'path': anchor['path'], 'query': anchor['name'],
                   'start': anchor['line'], 'end': anchor['line'], 'logical_charge': 1,
                   'locations': [], 'fallback_used': False}
            requests.append(req)
            try:
                resolver = (resolver_factory or pb._default_resolver)(root, sorted(inputs['data']['workspace_sources']))
                meter = pb._NativeRpcMeter(resolver)
                locations = getattr(resolver, anchor['operation'])(pb._symbol(anchor))
                status = resolver.status()
                req.update(state=pb._lsp_state(status, anchor['operation']),
                           locations=[dataclasses.asdict(x) if dataclasses.is_dataclass(x) else dict(x) for x in locations])
                req['result_count'] = len(locations)
                req['native_result_count'] = sum(pb.common.is_native(x.get('source', '')) for x in req['locations'])
                native = [x for x in req['locations'] if pb.common.is_native(x.get('source', ''))]
                frontier.add_locations(native, locator=anchor)
                anchor['state'] = 'located' if native else 'empty'
            except Exception as exc:
                req.update(state='failed', result_count=0, native_result_count=0,
                           error=f'{type(exc).__name__}: {str(exc)[:180]}')
                anchor['state'] = 'failed'
        # An acquisition is a complete unit, and is charged separately from its
        # locator. Units already indexed for structural selection do not require
        # another physical file read. These two costs are deliberately distinct.
        while len(requests) < 3:
            units_before = len(frontier.data['units'])
            candidates = sorted((u for u in frontier.data['units'] if u['state'] not in ('body_supplied', 'blocked')),
                                key=lambda u: (u['priority'], u['depth'], u['path'], u['start'], u['end']))
            selected = None
            for u in candidates:
                try:
                    data = frontier.index.index(u['path'])
                    row = pb._row(key, u['path'], 'head', u['start'], u['end'], pb._masked(data['text']),
                        f"body frontier complete {u['kind']} {u['qualname']}; relation={u['relations'][0]}; provenance={u['provenance']}",
                        provenance=u['provenance'], backend=u['backend'])
                    trial = copy.deepcopy(store)
                    allowed, reason = pb._admit(trial, row)
                    if not allowed:
                        # The whole class remains omitted, but its scoped AST can
                        # identify complete connected methods. This is structural
                        # selection only; no unseen method becomes evidence.
                        if reason == 'source_cap_complete_unit_omitted' and u['kind'] == 'class':
                            frontier.expand(u, changed_fields=u.get('focus_attrs', []))
                        u['state'] = 'blocked' if reason != 'already_supplied' else 'body_supplied'
                        frontier.data['events'].append({'kind': 'initial_unit_preflight_omitted', 'unit_id': u['id'],
                                                       'reason': reason, 'acquisition_dispatched': False})
                        continue
                    selected = u, row
                    break
                except (ValueError, SyntaxError, OSError) as exc:
                    u['state'] = 'blocked'
                    frontier.data['events'].append({'kind': 'unit_preflight_unavailable', 'unit_id': u['id'],
                                                   'error': type(exc).__name__})
            if selected is None:
                if len(frontier.data['units']) > units_before:
                    continue
                break
            u, row = selected
            request = {'id': f'frontier-initial-body-{len(requests) + 1}', 'operation': 'read_lines',
                       'path': u['path'], 'query': None, 'start': u['start'], 'end': u['end'],
                       'logical_charge': 1, 'locator_id': u['locator_id'],
                       'locator_provenance': u['provenance'], 'fallback_used': u['provenance'] != 'native_lsp',
                       'body_selection': 'complete Python AST unit; no partial body',
                       'unit_id': u['id'], 'relation': u['relations'][0]}
            requests.append(request)
            admitted, why = pb._admit(store, row)
            request.update(state='completed' if admitted else 'budget_blocked', admitted=admitted, omitted_reason=why)
            if admitted:
                snippets.append(row)
                frontier.observe(request, supplied_spans=[row], status='completed')
            else:
                u['state'] = 'blocked'
    finally:
        try:
            if resolver is not None:
                try:
                    resolver.close()
                finally:
                    status = resolver.status()
        finally:
            if meter is not None:
                meter.restore()
    rows = []
    for req in requests:
        if req['operation'] == 'read_lines':
            rows.append(f"read complete unit {req['path']}:{req['start']}-{req['end']} state={req['state']} provenance={req['locator_provenance']} relation={req['relation']}")
        else:
            a = req['anchor']
            rows.append(f"{req['operation']} {a['path']}:{a['line']}:{a['column']} {a['name']} relation={a['relation']} state={req['state']} native_locations_total={req['native_result_count']} fallback=false")
    metadata = []
    if rows:
        content, omitted = pb.common.fit_listing(
            'Change-driven lookup audit. Native positions are not bodies. Structural candidates are not native bindings. Full locator results remain in the local audit.',
            rows, INITIAL_METADATA_CAP)
        metadata = [{'id': f'agentic:{key}:body-frontier-initial', 'content': content,
                     'path': requests[0]['path'], 'provenance': 'native_lsp' if anchor is not None else 'ast_fallback',
                     'backend': 'lsp:pyright' if anchor is not None else 'python-ast',
                     'content_kind': 'reference_edge', 'omitted_rows': omitted}]
    source_reads = frontier.data['source_reads']
    acquisitions = [r for r in requests if r['operation'] == 'read_lines']
    resolver_requests = status.get('python', {}).get('requests', 0 if resolver is None else None)
    physical = {'preparation_only': True, 'normalized_queries': len(requests),
                'logical_locator_attempts': int(anchor is not None),
                'native_rpc_queries': meter.sent if meter is not None else (0 if resolver is None else None),
                'native_resolver_operation_requests': resolver_requests,
                'native_rpc_write_attempts': len(meter.events) if meter is not None and meter.supported else (0 if resolver is None else None),
                'native_rpc_write_events': meter.events if meter is not None else [],
                'native_rpc_meter_available': meter.supported if meter is not None else resolver is None,
                'local_source_file_read_attempts': len(source_reads),
                'local_source_file_reads_succeeded': sum(x['read_succeeded'] for x in source_reads),
                'local_source_file_reads': copy.deepcopy(source_reads),
                'local_body_acquisitions': len(acquisitions),
                'local_body_acquisitions_supplied': sum(x.get('admitted', False) for x in acquisitions),
                'native_returned_locations': sum(r.get('native_result_count', 0) for r in requests),
                'server_status': status,
                'notes': copy.deepcopy(frontier.data['events']),
                'cost_semantics': {
                    'normalized_queries': 'one native locator attempt plus separately charged complete-unit acquisitions; max3',
                    'native_rpc_queries': 'successfully transmitted definition/references only; shared once; unknown meter is null',
                    'local_source_file_read_attempts': 'explicit target AST indexing reads; excludes reused changed-file AST seed text and LSP didOpen',
                    'local_body_acquisitions': 'selected complete-unit supply attempts from cached indexed source, separately charged; no source truncation'}}
    queries = {'count': len(requests), 'requests': requests,
               'batches': [{'label': 'frontier_initial', 'count': len(requests),
                            'requests': [r['id'] for r in requests], 'operations': [r['operation'] for r in requests],
                            'running_total': len(requests)}] if requests else []}
    return pb._descriptor(snippets, metadata, store), queries, physical, frontier.to_dict()


def prepare_packet(key, product=None, *, resolver_factory=None):
    product = product or pb.common.load_product()
    inputs = pb.common.build_inputs(key, product=product, diff_only=True)
    ast_desc, store, audit, head_info = pb._prepare_ast(inputs)
    enhanced, queries, physical, frontier = _enhanced(inputs, ast_desc, copy.deepcopy(store), head_info, resolver_factory)
    return {'version': VERSION, 'key': key, 'authoritative': inputs['data']['authoritative'],
            'workspace_tree_sha256': inputs['data']['workspace_tree_sha256'], 'ast': ast_desc,
            'lsp': enhanced, 'initial_queries': queries, 'frontier': frontier,
            'audit': {'ast': audit, 'physical_preparation': physical,
                      'selection_policy': 'changed source AST and native locations only; no contracts, cases, labels or previous findings',
                      'source_cap': SOURCE_CAP, 'metadata_cap': METADATA_CAP,
                      'initial_metadata_cap': INITIAL_METADATA_CAP, 'retry_count': 0}}


def build_inputs(key, arm, prepared_packet=None, product=None):
    if arm not in ARMS:
        raise pb.PacketError(f'unknown frontier arm: {arm}')
    if arm in ('diff', 'ast'):
        inputs = pb.build_inputs(key, arm, prepared_packet, product)
    else:
        if prepared_packet is None:
            raise pb.PacketError('native arms require a freshly prepared shared packet')
        inputs = pb.build_inputs(key, 'lsp', prepared_packet, product)
    inputs['component_packet_audit']['arm'] = arm
    if arm in ('body_lsp', 'reactive', 'proactive'):
        inputs['component_frontier'] = copy.deepcopy(prepared_packet['frontier'])
    else:
        inputs['component_frontier'] = None
    return inputs
