"""Frozen G0/H evidence audit and paired PR-cluster finding F1.

The response citation/file gates are copied verbatim from the arm-neutral
cumulative-ablation audit. Auxiliary context-accounting IDs are never promoted
to primary bug-finding gates. No function here performs native/model calls.
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import importlib.util
import json
import random
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parent
ROOT = R.parents[1]
REFERENCE = ROOT / "runs/lsp_agentic_preserved_recovery_20261023"
sys.path.insert(0, str(ROOT))
from ace_review.context_adaptive import digest, token_count
from ace_review.protocol_hypothesis import mask_source

HEADER = re.compile(r"^\[(E-(head|base)-[a-f0-9]+)\] (head|base) ([^;]+);")
LINE = re.compile(r"^(\d+): (.*)$")


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def diff_locations(common):
    """Text-only unified-diff coordinates; no AST, repository or native reads."""
    text = common.partition("[DIFF]\n")[2]
    files, lines = set(), {"head": collections.defaultdict(set), "base": collections.defaultdict(set)}
    old_file = new_file = None
    old_line = new_line = None
    for line in text.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            path = line[4:].split("\t", 1)[0]
            path = None if path == "/dev/null" else re.sub(r"^[ab]/", "", path)
            if line.startswith("--- "):
                old_file = path
            else:
                new_file = path
            if path:
                files.add(path)
            old_line = new_line = None
            continue
        match = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            old_line, new_line = map(int, match.groups())
            continue
        if old_line is None or not line or line.startswith("\\"):
            continue
        if line[0] in " -":
            if old_file:
                lines["base"][old_file].add(old_line)
            old_line += 1
        if line[0] in " +":
            if new_file:
                lines["head"][new_file].add(new_line)
            new_line += 1
    return files, lines


def evidence_view(packet):
    ids = set(re.findall(r"\[(E-[^\]]+)\]", packet["source"] + "\n" + packet["plain"])) | {"DIFF"}
    supplied = collections.defaultdict(dict)
    conflicts, current = [], None
    for line in packet["source"].splitlines():
        match = HEADER.match(line)
        if match:
            current = (match[2], match[4])
            continue
        if line.startswith("[E-"):
            current = None
            continue
        match = LINE.match(line)
        if current and match:
            number = int(match[1])
            if number in supplied[current] and supplied[current][number] != match[2]:
                conflicts.append([current, number])
            supplied[current][number] = match[2]
    lineids = {f"E-{version}-{digest(file)[:10]}": set(lines) for (version, file), lines in supplied.items()}
    return {"ids": ids, "lineids": lineids, "supplied": supplied, "conflicts": conflicts}


def valid_id(value, view):
    if value in view["ids"]:
        return True
    match = re.fullmatch(r"(E-(?:head|base)-[a-f0-9]+):(\d+)(?:-(\d+))?", value)
    return bool(match and match[1] in view["lineids"] and int(match[2]) <= int(match[3] or match[2])
                and set(range(int(match[2]), int(match[3] or match[2]) + 1)) <= view["lineids"][match[1]])


def response_checks(response, packet, common, *, allow_diff_files=True):
    """Same predecessor gates except accepting already-authorized DIFF files."""
    errors, warnings = [], []
    view = evidence_view(packet)
    audit = packet["audit"]
    diff_files, diff_lines = diff_locations(common)
    review = response["review"]
    if (review["status"] == "reported_issues") != bool(review["issues"]):
        errors.append("status_issue")
    source_files = set(audit.get("source_lines", {})) | set(audit.get("base_lines", {}))
    for section, items in [("issue", review["issues"]), ("check", response["checks"])]:
        for index, item in enumerate(items):
            if any(not valid_id(value, view) for value in item["evidence_ids"]):
                errors.append(f"{section}_evidence_{index}")
            if section == "issue":
                file = item["file"]
                if file not in source_files and not (allow_diff_files and file in diff_files):
                    errors.append(f"issue_file_{index}")
                visible = set(audit.get("source_lines", {}).get(file, [])) | set(audit.get("base_lines", {}).get(file, []))
                # Preserve the historical line warning even when the line is in
                # the diff. Text hunk membership is additionally recorded below.
                if item["line"] not in visible:
                    warnings.append(f"issue_line_diff_check_{index}")
            elif any(j < 0 or j >= len(review["issues"]) for j in item["issue_indices"]):
                errors.append(f"issue_index_{index}")
    assessment = response["context_assessment"]
    used = {x["evidence_id"] for x in assessment["used_evidence"]}
    dismissed = {x["evidence_id"] for x in assessment["dismissed_evidence"]}
    missing = {x["evidence_id"] for x in assessment["missing_connections"]}
    normalize = lambda values: {re.sub(r":\d+(?:-\d+)?$", "", value) for value in values}
    sourceids = set(re.findall(r"\[(E-[^\]]+)\]", packet["source"]))
    coverage = {
        "available_source_ids": sorted(sourceids), "used_ids": sorted(used), "dismissed_ids": sorted(dismissed),
        "missing_anchor_ids": sorted(missing), "invalid_assessment_ids": sorted(x for x in used | dismissed | missing if not valid_id(x, view)),
        "used_dismissed_overlap": sorted(normalize(used) & normalize(dismissed)),
        "unaccounted_source_ids": sorted(sourceids - normalize(used) - normalize(dismissed)),
        "cited_ids": sorted({x for item in review["issues"] + response["checks"] for x in item["evidence_ids"]}),
        "issue_diff_locations": [{"issue_index": i, "file_in_diff": x["file"] in diff_files,
                                  "head_hunk_line": x["line"] in diff_lines["head"].get(x["file"], set()),
                                  "base_hunk_line": x["line"] in diff_lines["base"].get(x["file"], set())}
                                 for i, x in enumerate(review["issues"])],
        "note": "Self-reported use and file-ID citation do not prove source understanding; location-only mismatches remain warnings."
    }
    return errors, warnings, coverage



def runner():
    sys.path.insert(0, str(R))
    import runner as module
    return module


def sealed_files(path):
    """A seal may store plain hashes or result-seal metadata values."""
    data = read(path)
    entries = data.get('files', data)
    errors = []
    for name, value in entries.items():
        expected = value.get('sha256') if isinstance(value, dict) else value
        location = value.get('path', name) if isinstance(value, dict) else name
        target = Path(location) if Path(location).is_absolute() else ROOT / location
        if not target.is_file() or sha(target) != expected:
            errors.append([str(target), 'sealed_hash'])
    return errors


def preparation_plan_checks():
    """Verify controls without running either preparation or model transport."""
    e = runner()
    plan = read(R / 'preparation_plan.json')
    old_ann = read(REFERENCE / 'author_annotations.json')
    old_seal = read(REFERENCE / 'result_seal.json')['files']
    errors, pairs = [], {}
    expected_pairs = {f'{cid}_{w}_r{rep}' for cid in read(R / 'labels.json') for w in ('a', 'b') for rep in (1, 2, 3)}
    for p in plan['pairs']:
        key, stem = p['pair_id'], f"{p['case_id']}_{p['world']}"
        if key in pairs:
            errors.append([key, 'duplicate_pair'])
        pairs[key] = p
        original = f"{stem}_G_r{p['repeat']}"
        if key != f"{stem}_r{p['repeat']}" or p['original_run_id'] != original:
            errors.append([key, 'pair_identity'])
        expected = {'raw_path': REFERENCE / 'raw' / f'{original}_s1.json',
                    'input_path': REFERENCE / 'inputs' / f'{original}_s1.json',
                    'trace_path': REFERENCE / 'raw' / f'{original}_s1.trace.json',
                    'schema_path': REFERENCE / 'schema_first.json'}
        for field, target in expected.items():
            value = old_seal.get(str(target.relative_to(REFERENCE)))
            sealed_hash = value.get('sha256') if isinstance(value, dict) else value
            if (p['shared_first'][field] != str(target.relative_to(ROOT))
                    or p['shared_first']['sha256'][field] != sha(target) or sha(target) != sealed_hash):
                errors.append([key, 'shared_first_origin_hash', field])
        first = read(expected['raw_path'])
        first_response_hash = digest(json.dumps(first['response'], ensure_ascii=False, sort_keys=True))
        if (first['status'] != 'ok' or p['first_response_sha256'] != first_response_hash
                or sha(expected['raw_path']) != old_ann[original]['raw_sha256']['1']):
            errors.append([key, 'first_response_or_annotation'])
        seed = R / 'packets/seed' / f'{stem}_initial.json'
        if p['seed_path'] != str(seed.relative_to(ROOT)) or p['seed_sha256'] != sha(seed):
            errors.append([key, 'seed_binding'])
        opposite = stem[:-1] + ('b' if p['world'] == 'a' else 'a')
        for field, expected_stem in [('workspace_manifest_path', stem), ('base_workspace_manifest_path', opposite)]:
            if p[field] != str((R / 'workspace_manifests' / f'{expected_stem}.json').relative_to(ROOT)):
                errors.append([key, field])
        gp = p['G0']
        target = R / 'packets/G0' / f'{key}_final.json'
        oldret = REFERENCE / 'retrieval' / f'{original}.json'
        oldinput = REFERENCE / 'inputs' / f'{original}_s2.json'
        for pathfield, hashfield, wanted in [('packet_path', 'packet_file_sha256', target),
                ('original_retrieval_path', 'original_retrieval_sha256', oldret),
                ('original_final_input_path', 'original_final_input_sha256', oldinput)]:
            if gp[pathfield] != str(wanted.relative_to(ROOT)) or gp[hashfield] != sha(wanted):
                errors.append([key, pathfield])
        packet, ret = read(target), read(oldret)
        if packet != {'source': ret['source'], 'plain': ret['observations'], 'audit': ret['audit']} or gp['packet_sha256'] != e.packet_digest(packet):
            errors.append([key, 'G0_packet_exactness'])
        meta = read(R / 'cases' / f'{stem}.json')
        prior = {k: v for k, v in first['response'].items() if k != 'context_requests'}
        if e.build_prompt(meta, packet, False, prior) != read(oldinput)['prompt']:
            errors.append([key, 'G0_prompt_exactness'])
    if set(pairs) != expected_pairs or len(plan['pairs']) != 72:
        errors.append(['plan_pair_inventory'])
    for field, expected in [('unique_shared_first_responses', 72), ('H_native_preparations', 72),
            ('native_workers', 2), ('maximum_fresh_native_queries', 432), ('seed_queries_cap', 12),
            ('new_queries_cap', 6), ('logical_queries_cap', 18), ('query_depth_cap', 3),
            ('source_tokens_cap', 5200), ('plain_tokens_cap', 800)]:
        if plan.get(field) != expected:
            errors.append([field, 'plan_budget'])
    return pairs, errors


def normalized_native_locations(raw, root):
    from urllib.parse import unquote, urlparse
    root, out = Path(root).resolve(), []
    for loc in ([raw] if isinstance(raw, dict) else raw or []):
        uri = loc.get('uri', loc.get('targetUri'))
        rg = loc.get('targetSelectionRange', loc.get('range'))
        try:
            path = Path(unquote(urlparse(uri).path)).resolve()
            file = str(path.relative_to(root))
        except (TypeError, ValueError):
            continue
        if path.suffix == '.py' and path.is_file():
            out.append({'file': file, 'line': rg['start']['line'] + 1,
                        'character_utf16': rg['start']['character'], 'range': rg})
    return out


def graph_checks(packet, seed, manifest, key):
    """Check typed native provenance, not semantic efficacy of AST hints."""
    errors, a = [], packet['audit']
    graph = a.get('symbol_graph', {})
    if graph.get('schema') != 1 or a.get('mode') != 'H':
        return [[key, 'graph_schema_or_mode']]
    nodes = {n['id']: n for n in graph.get('nodes', [])}
    if len(nodes) != len(graph.get('nodes', [])):
        errors.append([key, 'graph_duplicate_node'])
    for nid, node in nodes.items():
        identity = {k: node[k] for k in ('version', 'file', 'range', 'source_sha256')}
        prefix = 'S-' if node['kind'] == 'native_definition_symbol' else 'O-'
        if (node['version'] != 'head' or node['kind'] not in {'occurrence', 'native_definition_symbol'}
                or nid != prefix + digest(json.dumps(identity, sort_keys=True))[:24]
                or manifest['sources'].get(node['file']) != node['source_sha256']):
            errors.append([key, 'graph_node_identity', nid])
    for file, value in graph.get('snapshots', {}).items():
        if manifest['sources'].get(file) != value:
            errors.append([key, 'graph_snapshot_hash', file])
    query_groups = {'seed': seed['audit']['queries'], 'additional': a['additional_queries']}
    native_symbols, symbol_queries = set(), collections.defaultdict(set)
    def validate_provenance(p):
        phase, index = p.get('phase'), p.get('query_index', 0)
        if phase not in query_groups or not isinstance(index, int) or not 1 <= index <= len(query_groups[phase]):
            return None, 'graph_query_index'
        q = query_groups[phase][index - 1]
        qkey = [q['operation'], q['file'], q['line'] - 1, q['character_utf16']]
        if p.get('query_key') != qkey or p.get('raw_sha256') != digest(json.dumps(q.get('raw'), sort_keys=True)):
            return None, 'graph_query_provenance'
        valid = normalized_native_locations(q.get('raw'), manifest['root'])
        window = sorted(valid, key=lambda x: (x['file'] != q['file'], x['file'], x['line']))[:32]
        if p.get('returned_location') not in window:
            return None, 'graph_native_location'
        return q, None
    for edge in graph.get('edges', []):
        start, end = nodes.get(edge['from']), nodes.get(edge['to'])
        if not start or not end:
            errors.append([key, 'graph_edge_endpoint']); continue
        p, kind = edge['provenance'], edge['kind']
        if kind in {'same_file_ast_hint', 'same_file_lexical_member_hint'}:
            if start['file'] != end['file'] or p.get('source_sha256') != manifest['sources'].get(start['file']):
                errors.append([key, 'cross_file_or_unhashed_AST_hint'])
            continue
        if kind not in {'native_definition', 'native_references', 'native_definition_location'}:
            errors.append([key, 'graph_unknown_relation', kind]); continue
        q, problem = validate_provenance(p)
        if problem:
            errors.append([key, problem]); continue
        loc = p['returned_location']
        if end['file'] != loc['file'] or end['range'] != loc['range']:
            errors.append([key, 'graph_returned_endpoint'])
        if kind == 'native_definition_location':
            if q['operation'] != 'definition' or end['kind'] != 'native_definition_symbol' or start['file'] != loc['file'] or start['range'] != loc['range']:
                errors.append([key, 'graph_definition_symbol_origin'])
            native_symbols.add(end['id'])
            symbol_queries[end['id']].add(tuple(p['query_key']))
        else:
            pos = {'line': q['line'] - 1, 'character': q['character_utf16']}
            if kind != 'native_' + q['operation'] or start['file'] != q['file'] or start['range'] != {'start': pos, 'end': pos} or end['kind'] != 'occurrence':
                errors.append([key, 'graph_query_endpoint'])
    if {n for n, value in nodes.items() if value['kind'] == 'native_definition_symbol'} != native_symbols:
        errors.append([key, 'graph_unproven_symbol'])
    for row in graph.get('canonical_symbols', []):
        if row['symbol'] not in native_symbols or {tuple(x) for x in row['occurrences']} != symbol_queries[row['symbol']]:
            errors.append([key, 'canonical_native_union'])
    for row in a.get('candidate_rankings', []):
        index = row['query_index']
        if not 1 <= index <= len(query_groups['additional']):
            errors.append([key, 'candidate_query_index']); continue
        q = query_groups['additional'][index - 1]
        valid = normalized_native_locations(q.get('raw'), manifest['root'])
        window = sorted(valid, key=lambda x: (x['file'] != q['file'], x['file'], x['line']))[:32]
        if row['candidate_window'] != window or row['admitted_packages'] > 4:
            errors.append([key, 'candidate_window_or_fanout'])
        if row['uninspected_native_locations'] != max(0, len(valid) - 32):
            errors.append([key, 'candidate_window_accounting'])
        for candidate in row['ranked_candidates']:
            if {k: candidate[k] for k in ('file', 'line', 'character_utf16', 'range')} not in window:
                errors.append([key, 'ranked_candidate_not_native'])
    for row in a.get('native_admission_totals', []):
        phase, index = row['phase'], row['query_index']
        packages = [tuple(x) for x in row['packages']]
        if (phase not in query_groups or not 1 <= index <= len(query_groups[phase])
                or len(packages) > 4 or len(packages) != len(set(packages))):
            errors.append([key, 'cumulative_native_admission_cap'])
    for q in query_groups['additional']:
        if q['line'] not in a.get('source_lines', {}).get(q['file'], []):
            errors.append([key, 'queried_head_line_not_supplied', q['file'], q['line']])
    return errors


def reference_checks():
    """No output or reference artifact is mutated by this regression check."""
    old = read(REFERENCE / 'audit_all.json')
    ann = read(REFERENCE / 'author_annotations.json')
    scores = read(REFERENCE / 'scores.json')
    sealed = read(REFERENCE / 'result_seal.json')['files']
    artifact_errors = []
    for name in ('audit_all.json', 'author_annotations.json', 'scores.json', 'grading_rules.json'):
        value = sealed.get(name)
        expected = value.get('sha256') if isinstance(value, dict) else value
        if sha(REFERENCE / name) != expected:
            artifact_errors.append([name, 'reference_result_seal'])
    changed = []
    totals = collections.defaultdict(lambda: collections.Counter(TP=0, FP=0, FN=0))
    for row in old['rows']:
        rid = row['run_id']
        meta = read(REFERENCE / 'cases' / f"{row['case_id']}_{row['world']}.json")
        ret = read(REFERENCE / 'retrieval' / f'{rid}.json')
        packet = {'source': ret['source'], 'plain': ret['observations'], 'audit': ret['audit']}
        response = read(REFERENCE / 'raw' / f'{rid}_s2.json')['response']
        prior, _, _ = response_checks(response, packet, meta['common'], allow_diff_files=False)
        current, _, _ = response_checks(response, packet, meta['common'])
        if prior != current:
            changed.append([rid, prior, current])
        valid = row['status'] == 'ok' and not row['errors']
        n = len(response['review']['issues'])
        unsafe = row['label'] == 'unsafe'
        for metric_name, field in [('grounded', 'supported_issue_indices'), ('local', 'local_supported_issue_indices')]:
            tp = int(valid and unsafe and bool(ann[rid][field]))
            totals[row['arm'], metric_name].update(TP=tp, FP=n - tp, FN=int(unsafe) - tp)
    score_errors = []
    for (arm, name), counts in totals.items():
        expected = scores['summary'][arm][name + '_F1']
        if any(counts[k] != expected[k] for k in ['TP', 'FP', 'FN']):
            score_errors.append([arm, name, dict(counts), expected])
    return {'passed': len(old['rows']) == 144 and not changed and not score_errors and not artifact_errors,
            'reference_rows': len(old['rows']), 'changed_response_gates': changed,
            'artifact_errors': artifact_errors,
            'score_errors': score_errors, 'reference_audit_sha256': sha(REFERENCE / 'audit_all.json'),
            'reference_scores_sha256': sha(REFERENCE / 'scores.json'),
            'reference_annotations_sha256': sha(REFERENCE / 'author_annotations.json')}


def snapshot_checks():
    manifests = {p.stem: read(p) for p in (R / 'workspace_manifests').glob('*.json')}
    errors = []
    if len(manifests) != 24:
        errors.append(['manifest_inventory', len(manifests)])
    for stem, manifest in manifests.items():
        if sha(R / 'workspace_manifests' / f'{stem}.json') != sha(REFERENCE / 'workspace_manifests' / f'{stem}.json'):
            errors.append([stem, 'workspace_manifest_changed'])
        root = Path(manifest['root'])
        if read(root / 'pyrightconfig.json') != manifest['pyrightconfig']:
            errors.append([stem, 'pyrightconfig'])
        for file, expected in manifest['sources'].items():
            target = root / file
            if not target.is_file() or digest(target.read_text()) != expected:
                errors.append([stem, file, 'source_hash'])
    return manifests, errors


def packet_checks(packet, stem, key, manifests, masked, seed=None):
    errors = []
    a, view = packet['audit'], evidence_view(packet)
    if view['conflicts']:
        errors.append([key, 'source_conflicts', view['conflicts']])
    if token_count(packet['source']) > 5200 or token_count(packet['plain']) > 800:
        errors.append([key, 'token_budget'])
    queries, extra = a.get('queries', []), a.get('additional_queries', [])
    if len(queries) > 18 or len(extra) > 6 or any(q.get('depth', 0) > 3 for q in queries):
        errors.append([key, 'native_budget_depth'])
    if not a.get('root_coverage') or not a.get('has_roots'):
        errors.append([key, 'changed_roots_missing'])
    if seed is not None:
        if not packet['source'].startswith(seed['source']):
            errors.append([key, 'protected_seed_prefix'])
        for field in ('source_lines', 'base_lines'):
            for file, lines in seed['audit'][field].items():
                if not set(lines) <= set(a.get(field, {}).get(file, [])):
                    errors.append([key, 'protected_seed_lines', field, file])
        if queries[:len(seed['audit']['queries'])] != seed['audit']['queries']:
            errors.append([key, 'cached_seed_queries_changed'])
        if queries[len(seed['audit']['queries']):] != extra:
            errors.append([key, 'additional_query_suffix'])
    for version, field in [('head', 'source_lines'), ('base', 'base_lines')]:
        worldstem = stem if version == 'head' else stem[:-1] + ('b' if stem[-1] == 'a' else 'a')
        manifest = manifests[worldstem]
        for file, numbers in a.get(field, {}).items():
            if file not in manifest['sources']:
                errors.append([key, 'unsealed_source', version, file]); continue
            cachekey = (worldstem, file)
            if cachekey not in masked:
                masked[cachekey] = mask_source((Path(manifest['root']) / file).read_text()).splitlines()
            actual, rendered = masked[cachekey], view['supplied'][version, file]
            if set(rendered) != set(numbers):
                errors.append([key, 'source_line_accounting', version, file])
            if any(not 1 <= n <= len(actual) or rendered.get(n) != actual[n - 1] for n in numbers):
                errors.append([key, 'source_content', version, file])
    for version, file in view['supplied']:
        if file not in a.get('source_lines' if version == 'head' else 'base_lines', {}):
            errors.append([key, 'untracked_source', version, file])
    for inv in a.get('class_inventories', []):
        source = (Path(manifests[stem]['root']) / inv['file']).read_text()
        excerpt = '\n'.join(source.splitlines()[inv['line'] - 1:inv['end_line']])
        if (digest(source) != inv['full_file_source_sha256'] or digest(excerpt) != inv['full_class_source_sha256']
                or inv['evidence_id'] not in view['ids']):
            errors.append([key, 'inventory_hash_or_id', inv['file']])
    return errors


def native_checks(trace, queries, root, key):
    """Match native requests/results, including URI and UTF-16 coordinate."""
    from urllib.parse import unquote, urlparse
    actual = [x for x in trace if x.get('method') in {'textDocument/definition', 'textDocument/references'}]
    errors = []
    if len(actual) != len(queries):
        errors.append([key, 'native_trace_count', len(actual), len(queries)])
    for index, (event, q) in enumerate(zip(actual, queries)):
        params = event['params']
        uri = params['textDocument']['uri']
        path = Path(unquote(urlparse(uri).path))
        expected_path = Path(root) / q['file']
        expected_position = {'line': q['line'] - 1, 'character': q['character_utf16']}
        if (path.resolve() != expected_path.resolve() or params['position'] != expected_position
                or event['method'] != 'textDocument/' + q['operation']
                or event['response'].get('result') != q.get('raw')):
            errors.append([key, 'native_query_binding', index])
    return errors, len(actual)


def preparation_record_checks(job, pair, packet, seed, manifests):
    e = runner()
    key, arm = job['run_id'], job['arm']
    path = R / 'preparation' / arm / f"{pair['pair_id']}.json"
    record, errors = read(path), []
    if (record['pair_id'] != pair['pair_id'] or record['arm'] != arm
            or record['shared_first_stage_id'] != pair['original_run_id'] + '_s1'
            or record['first_response_sha256'] != pair['first_response_sha256']):
        errors.append([key, 'preparation_identity'])
    if record['status'] == 'ok':
        if packet is None or (record['packet_path'] != str(e.packet_path(job, 2).relative_to(ROOT))
                or record['packet_file_sha256'] != sha(e.packet_path(job, 2))
                or record['packet_sha256'] != e.packet_digest(packet)):
            errors.append([key, 'preparation_packet_binding'])
    elif record['status'] != 'error' or arm != 'H':
        errors.append([key, 'preparation_status'])
    if arm == 'G0':
        if any(record.get(k) != v for k, v in pair['G0'].items()):
            errors.append([key, 'G0_preparation_lineage'])
        expected_trace = pair['G0']['original_retrieval_path']
        if (record['native_trace_path'] != expected_trace or record['native_trace_sha256'] != sha(ROOT / expected_trace)
                or record.get('fresh_native_query_count') != 0 or not record['native_trace_is_historical']):
            errors.append([key, 'G0_historical_trace'])
        trace = read(ROOT / expected_trace)['actual_additional_native_trace']
        native_errors, count = native_checks(trace, packet['audit']['additional_queries'],
                                             manifests[f"{job['case_id']}_{job['world']}"]['root'], key)
        errors.extend(native_errors)
        if record['native_query_count'] != count or not record['trace_complete']:
            errors.append([key, 'G0_native_count'])
        return errors, 0, True, record
    expected_seal = sha(R / 'algorithm_seal.json')
    claim = read(R / 'native_claims' / f"{pair['pair_id']}.json")
    if (record.get('algorithm_seal_sha256') != expected_seal or claim.get('algorithm_seal_sha256') != expected_seal
            or claim.get('first_response_sha256') != pair['first_response_sha256']
            or claim.get('pair_id') != pair['pair_id'] or claim.get('maximum_queries') != 6):
        errors.append([key, 'native_algorithm_claim'])
    trace, count, complete, doc = [], 0, bool(record.get('trace_complete')), None
    if 'native_trace_path' in record:
        target = R / 'native_traces/H' / f"{pair['pair_id']}.json"
        if record['native_trace_path'] != str(target.relative_to(ROOT)) or record['native_trace_sha256'] != sha(target):
            errors.append([key, 'native_trace_file_binding'])
        doc = read(target)
        trace = doc['actual_trace']
        if doc.get('algorithm_seal_sha256') != expected_seal or bool(doc.get('trace_complete')) != complete:
            errors.append([key, 'native_trace_algorithm_or_completeness'])
        if record['status'] == 'ok' and doc.get('error'):
            errors.append([key, 'successful_trace_has_error'])
    elif record['status'] == 'ok' or complete:
        errors.append([key, 'missing_native_trace'])
    count = sum(x.get('method') in {'textDocument/definition', 'textDocument/references'} for x in trace)
    attempted = record.get('native_query_count')
    if record.get('attempted_native_query_count') != attempted:
        errors.append([key, 'attempt_count_alias'])
    if count > 6 or (attempted is not None and (not count <= attempted <= 6)) or (complete and attempted != count):
        errors.append([key, 'native_count_or_cap'])
    if record.get('observed_native_trace_count') != count:
        errors.append([key, 'observed_native_trace_count'])
    if doc is not None:
        if doc.get('native_query_count') != attempted or doc.get('observed_native_trace_count') != count:
            errors.append([key, 'trace_attempt_accounting'])
        attempted_queries = doc.get('attempted_queries')
        if attempted_queries is not None and len(attempted_queries) != attempted:
            errors.append([key, 'attempted_query_inventory'])
        if record['status'] == 'ok' and attempted_queries != packet['audit']['additional_queries']:
            errors.append([key, 'successful_attempted_query_binding'])
    if complete != (attempted is not None and count == attempted):
        errors.append([key, 'trace_completeness_accounting'])
    if record['status'] == 'ok':
        if not complete:
            errors.append([key, 'successful_trace_incomplete'])
        native_errors, _ = native_checks(trace, packet['audit']['additional_queries'],
                                         manifests[f"{job['case_id']}_{job['world']}"]['root'], key)
        errors.extend(native_errors)
        errors.extend(graph_checks(packet, seed, manifests[f"{job['case_id']}_{job['world']}"], key))
    return errors, attempted if attempted is not None else count, complete, record


def metric(tp, fp, fn):
    return {'TP': tp, 'FP': fp, 'FN': fn,
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'F1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0}


def graded_row(row, ann):
    rev = row['final']['review'] if row.get('final') else None
    n = len(rev['issues']) if rev else 0
    assert sorted(ann['supported_issue_indices'] + ann['unsupported_issue_indices']) == list(range(n)), row['run_id']
    assert set(ann['supported_issue_indices']) <= set(ann['local_supported_issue_indices']) <= set(range(n)), row['run_id']
    valid, unsafe = row['status'] == 'ok' and not row['errors'], row['label'] == 'unsafe'
    tp = int(valid and unsafe and bool(ann['supported_issue_indices']))
    local = int(valid and unsafe and bool(ann['local_supported_issue_indices']))
    stp, sfp = int(unsafe and n > 0), int(not unsafe and n > 0)
    safe = bool(valid and not unsafe and rev and rev['status'] == 'no_supported_issue' and ann['safe_verdict_supported'])
    return {k: row[k] for k in ['run_id', 'case_id', 'world', 'arm', 'repeat', 'label']} | {
        'valid': valid, 'grounded_tp': tp, 'grounded_fp': n - tp, 'grounded_fn': int(unsafe) - tp,
        'local_tp': local, 'local_fp': n - local, 'local_fn': int(unsafe) - local,
        'status_tp': stp, 'status_fp': sfp, 'status_fn': int(unsafe) - stp,
        'reported_issues': n, 'safe_false_alarm_reviews': sfp, 'safe_false_alarm_issues': n if not unsafe else 0,
        'unsafe_unmatched_issues': n - tp if unsafe else 0, 'safe_grounded': safe,
        'raw_safe_no_issue': bool(not unsafe and rev and rev['status'] == 'no_supported_issue'),
        'abstained': bool(rev and rev['status'] == 'insufficient_evidence'),
        'operational_failure': row['status'] != 'ok',
        'correct': safe or bool(tp and not ann['unsupported_issue_indices'] and n == 1),
        'first_target_state': ann['first_target_state'], 'final_target_state': ann['final_target_state'],
        'source_tokens': row.get('source_tokens', 0), 'observation_tokens': row.get('observation_tokens', 0),
        'fresh_calls': row.get('physical_calls', 0), 'new_native_queries': row.get('new_native_queries', 0),
        'native_trace_incomplete': not row.get('native_trace_complete', True),
        'native_count_unknown': not row.get('native_query_count_known', True),
        'seconds': row.get('seconds', 0), 'total_prompt_tokens': row.get('total_prompt_tokens', 0),
        'fresh_prompt_tokens': row.get('fresh_prompt_tokens', 0),
        'native_preparation_seconds': row.get('native_preparation_seconds', 0),
        'source_plain_equals_G0': row.get('source_plain_equals_G0'),
        'cohort': 'fresh_final_conditioned_on_shared_historical_first'}


def summary(rows):
    keys = ['valid', 'reported_issues', 'safe_false_alarm_reviews', 'safe_false_alarm_issues',
            'unsafe_unmatched_issues', 'safe_grounded', 'raw_safe_no_issue', 'abstained', 'operational_failure']
    out = {k: sum(x.get(k, 0) for x in rows) for k in keys}
    for name in ('grounded', 'local', 'status'):
        out[name + '_F1'] = metric(*(sum(x[name + '_' + k] for x in rows) for k in ('tp', 'fp', 'fn')))
    pairs = {(x['case_id'], x['repeat']) for x in rows}
    out['pair_success'] = sum(all(x['correct'] for x in rows if (x['case_id'], x['repeat']) == p) for p in pairs)
    out.update(trajectories=len(rows), unsafe_reviews=sum(x['label'] == 'unsafe' for x in rows),
               safe_reviews=sum(x['label'] == 'safe' for x in rows), fresh_calls=sum(x.get('fresh_calls', 0) for x in rows),
               new_native_queries=sum(x.get('new_native_queries', 0) for x in rows))
    out['native_query_count_is_lower_bound'] = any(x.get('native_count_unknown') for x in rows)
    out['native_incomplete_response_traces'] = sum(x.get('native_trace_incomplete', False) for x in rows)
    for field in ('source_tokens', 'observation_tokens', 'seconds', 'total_prompt_tokens', 'fresh_prompt_tokens', 'native_preparation_seconds'):
        out['mean_' + field] = sum(x.get(field, 0) for x in rows) / len(rows) if rows else 0
    out['unsafe_first_to_final'] = dict(collections.Counter(x['first_target_state'] + ' -> ' + x['final_target_state']
                                                          for x in rows if x['label'] == 'unsafe'))
    return out


def score(audit):
    e = runner()
    required = ['algorithm_hashes', 'freeze_hashes', 'workspace_hashes', 'packet_and_native_integrity',
                'shared_common_prompts', 'complete_trajectories', 'reuse_integrity', 'legacy_gate_regression',
                'exact_model_record_inventory', 'shared_first_annotations']
    assert all(audit['checks'].get(k) is True for k in required), {k: audit['checks'].get(k) for k in required}
    annotations = {}
    for arm in e.ARMS:
        part = read(R / f'author_annotations_{arm}.json')
        assert not set(part) & set(annotations)
        annotations.update(part)
    assert set(annotations) == {x['run_id'] for x in audit['rows']}
    old_ann = read(REFERENCE / 'author_annotations.json')
    rows = []
    for x in audit['rows']:
        g = annotations[x['run_id']]
        for stage in (1, 2):
            path = e.resolve_stage(x, stage)['raw_path']
            assert (sha(path) if path.exists() else None) == g['raw_sha256'].get(str(stage)), x['run_id']
        original = f"{x['case_id']}_{x['world']}_G_r{x['repeat']}"
        assert g['first_target_state'] == old_ann[original]['first_target_state'], (x['run_id'], 'shared first state changed')
        rows.append(graded_row(x, g))
    assert len(rows) == 144 and len({x['run_id'] for x in rows}) == 144
    cases = sorted({x['case_id'] for x in rows})
    assert len(cases) == 12
    totals = {arm: summary([x for x in rows if x['arm'] == arm]) for arm in e.ARMS}
    assert all(x['trajectories'] == 72 and x['unsafe_reviews'] == x['safe_reviews'] == 36 for x in totals.values())
    per_case = {c: {a: summary([x for x in rows if x['case_id'] == c and x['arm'] == a]) for a in e.ARMS} for c in cases}
    rules = read(R / 'grading_rules.json')
    rng = random.Random(rules['bootstrap_seed'])
    draws = [[rng.choice(cases) for _ in cases] for _ in range(rules['bootstrap_samples'])]
    contrasts = {}
    for name in ('grounded_F1', 'local_F1', 'status_F1'):
        samples = []
        for draw in draws:
            values = {a: metric(*(sum(per_case[c][a][name][k] for c in draw) for k in ('TP', 'FP', 'FN')))['F1'] for a in e.ARMS}
            samples.append(values['H'] - values['G0'])
        samples.sort()
        contrasts[name] = {'delta_percentage_points': 100 * (totals['H'][name]['F1'] - totals['G0'][name]['F1']),
                           'PR_cluster_percentile_95CI_pp': [100 * samples[249], 100 * samples[9749]],
                           'bootstrap_samples': len(draws), 'bootstrap_seed': rules['bootstrap_seed']}
    pairs = collections.defaultdict(dict)
    for row in rows:
        pairs[row['case_id'], row['world'], row['repeat']][row['arm']] = row
    transitions = {}
    for name in ('grounded', 'local', 'status'):
        transitions[name] = dict(collections.Counter(f"{p['G0'][name + '_tp']} -> {p['H'][name + '_tp']}"
                                                      for p in pairs.values() if p['G0']['label'] == 'unsafe'))
    hist = read(REFERENCE / 'scores.json')
    output = {'summary': totals, 'contrasts': {'H_minus_G0': contrasts}, 'per_case': per_case, 'rows': rows,
              'unsafe_matched_transitions': transitions,
              'unchanged_context_positions': [list(k) for k, p in pairs.items() if p['H']['source_plain_equals_G0']],
              'historical_G': {'cohort': 'historical_reference_not_primary_comparison', 'summary': hist['summary']['G'],
                               'scores_path': str(REFERENCE / 'scores.json'), 'scores_sha256': sha(REFERENCE / 'scores.json')},
              'notes': ['One shared historical first response per position; 72 original firsts reused in 144 logical slots, not extra samples.',
                        'Both G0/H finals are fresh; all failures remain in the 72-per-arm denominator.',
                        'total_prompt_tokens includes the logical reused first; fresh_prompt_tokens and usage count only fresh final transport.',
                        'Primary estimates conditional follow-up package effect; graph, protocol closure and candidate ranking are not separately identified.',
                        'Same12 previously analyzed Python development PRs, nonblind AI grading, no holdout or production-generalization claim.',
                        'Unsupported reported findings count as unmatched issues; this does not mean every unmatched issue is nonexistent.',
                        'Identical source/plain stochastic differences are not retrieval gains; new line use requires manual causal attribution.']}
    save(R / 'author_annotations.json', annotations)
    save(R / 'scores.json', output)
    return output

def preparation_preflight():
    """Run after algorithm sealing and before any real H native preparation."""
    manifests, workspace_errors = snapshot_checks()
    common_errors, reuse_errors = [], []
    plan_pairs, plan_errors = preparation_plan_checks()
    reuse_errors.extend(plan_errors)
    packet_errors, masked = [], {}
    for pair in plan_pairs.values():
        stem = f"{pair['case_id']}_{pair['world']}"
        job = {'run_id': f"{stem}_G0_r{pair['repeat']}", 'arm': 'G0',
               'case_id': pair['case_id'], 'world': pair['world'], 'repeat': pair['repeat']}
        seed, packet = read(ROOT / pair['seed_path']), read(ROOT / pair['G0']['packet_path'])
        packet_errors.extend(packet_checks(packet, stem, pair['pair_id'], manifests, masked, seed))
        packet_errors.extend(preparation_record_checks(job, pair, packet, seed, manifests)[0])
    for stem in sorted(manifests):
        meta = read(R / 'cases' / f'{stem}.json')
        if meta['common'] != read(REFERENCE / 'cases' / f'{stem}.json')['common']:
            common_errors.append([stem, 'common_changed'])
        seed = R / 'packets/seed' / f'{stem}_initial.json'
        old = REFERENCE / 'packets/seed' / seed.name
        if not seed.exists() or seed.read_bytes() != old.read_bytes():
            reuse_errors.append([stem, 'seed_bytes_changed'])
    for name in ('schema_first.json', 'schema_final.json', 'labels.json'):
        if sha(R / name) != sha(REFERENCE / name):
            reuse_errors.append([name, 'reference_changed'])
    original_annotations = read(REFERENCE / 'author_annotations.json')
    for case_id in sorted({s.rsplit('_', 1)[0] for s in manifests}):
        for world in ('a', 'b'):
            for rep in (1, 2, 3):
                rid = f'{case_id}_{world}_G_r{rep}'
                first = REFERENCE / 'raw' / f'{rid}_s1.json'
                if sha(first) != original_annotations[rid]['raw_sha256']['1']:
                    reuse_errors.append([rid, 'original_first_annotation_raw_hash'])
    original_rules = read(REFERENCE / 'grading_rules.json')
    rules = read(R / 'grading_rules.json')
    for key in ('primary_f1', 'f1_formula', 'f1_secondary', 'grounded_detection', 'local_causal_support',
                'safe_supported', 'safe_false_positive', 'abstention', 'pair_success', 'inventory_evidence', 'context_assessment'):
        if rules[key] != original_rules[key]:
            reuse_errors.append([key, 'causal_rubric_changed'])
    if rules.get('bootstrap_seed') != 2026102501 or rules.get('bootstrap_samples') != 10000:
        reuse_errors.append(['bootstrap_plan_changed'])
    algorithm_errors = sealed_files(R / 'algorithm_seal.json')
    reference_errors = sealed_files(R / 'reference_seals.json')
    regression = reference_checks()
    output = {'checks': {'algorithm_hashes': not algorithm_errors, 'workspace_hashes': not workspace_errors,
                         'reference_hashes': not reference_errors, 'shared_common_prompts': not common_errors,
                         'reuse_integrity': not reuse_errors, 'legacy_gate_regression': regression['passed'],
                         'G0_packet_and_native_integrity': not packet_errors},
              'algorithm_errors': algorithm_errors, 'workspace_errors': workspace_errors,
              'reference_errors': reference_errors, 'common_errors': common_errors,
              'reuse_errors': reuse_errors, 'packet_errors': packet_errors, 'legacy_regression': regression,
              'scope': 'Pre-native algorithm/reference/snapshot/rubric audit; H outcomes do not exist yet.'}
    save(R / 'audit_preparation_preflight.json', output)
    return output


def audit_cohort(preflight=False):
    e = runner()
    from jsonschema import Draft202012Validator
    frozen = read(R / 'freeze.json')
    manifest = read(R / 'stage_reuse_manifest.json')
    labels = read(R / 'labels.json')
    old_ann = read(REFERENCE / 'author_annotations.json')
    manifests, workspace_errors = snapshot_checks()
    algorithm_errors = sealed_files(R / 'algorithm_seal.json')
    freeze_errors = sealed_files(R / 'freeze.json')
    reference_errors = sealed_files(R / 'reference_seals.json')
    checks = {'algorithm_hashes': not algorithm_errors, 'freeze_hashes': not freeze_errors,
              'workspace_hashes': not workspace_errors}
    plan_pairs, plan_errors = preparation_plan_checks()
    packet_errors, reuse_errors, common_errors = [], list(reference_errors) + plan_errors, []
    masked, seeds = {}, {}
    for stem in sorted(manifests):
        meta = read(R / 'cases' / f'{stem}.json')
        if meta['common'] != read(REFERENCE / 'cases' / f'{stem}.json')['common']:
            common_errors.append([stem, 'common_changed'])
        seedpath = R / 'packets/seed' / f'{stem}_initial.json'
        if seedpath.read_bytes() != (REFERENCE / 'packets/seed' / seedpath.name).read_bytes():
            reuse_errors.append([stem, 'seed_bytes_changed'])
        seed = read(seedpath)
        seeds[stem] = seed
        packet_errors.extend(packet_checks(seed, stem, 'seed:' + stem, manifests, masked))
        if len(seed['audit']['queries']) > 12:
            packet_errors.append([stem, 'seed_query_cap'])
    validators, settings = {}, {}
    for s, name in [(1, 'schema_first.json'), (2, 'schema_final.json')]:
        if sha(R / name) != sha(REFERENCE / name):
            reuse_errors.append([name, 'schema_changed'])
        validators[s] = Draft202012Validator(read(R / name))
        settings[s] = e.settings(R / name)
    jobs = frozen['jobs']
    expected = {e.stage_key(j, s) for j in jobs for s in (1, 2)}
    if set(manifest['entries']) != expected:
        reuse_errors.append(['stage_manifest_inventory'])
    if len(jobs) != 144 or collections.Counter(j['arm'] for j in jobs) != {'G0': 72, 'H': 72}:
        reuse_errors.append(['job_inventory'])
    originals = read(REFERENCE / 'result_seal.json')['files']
    rows, coverage, fresh_records, reused_keys, pair_first = [], [], set(), set(), collections.defaultdict(dict)
    final_packets = {}
    new_native_total, native_observed_total, native_incomplete, native_count_unknown, preparation_end_times = 0, 0, [], [], []
    shared_first_annotation_errors = []
    for job in jobs:
        rid, stem = job['run_id'], f"{job['case_id']}_{job['world']}"
        original_rid = f"{stem}_G_r{job['repeat']}"
        meta, seed = read(R / 'cases' / f'{stem}.json'), seeds[stem]
        trajectory_path = R / 'trajectories' / f'{rid}.json'
        trajectory = read(trajectory_path) if trajectory_path.exists() else {'status': 'missing'}
        final_path = e.packet_path(job, 2)
        final_entry = e.resolve_stage(job, 2)['entry']
        preparation_failed = final_entry.get('preparation_status') == 'error'
        final_packet = read(final_path) if final_path.exists() else None
        prep_path = R / 'preparation' / job['arm'] / f"{stem}_r{job['repeat']}.json"
        prep = read(prep_path)
        if preparation_failed and final_packet is not None:
            if prep.get('ineligible_orphan_packet_path') == str(final_path.relative_to(ROOT)) and prep.get('ineligible_orphan_packet_sha256') == sha(final_path):
                final_packet = None
        if (prep['status'] == 'error') != preparation_failed or final_entry['preparation_record_path'] != str(prep_path.relative_to(ROOT)):
            reuse_errors.append([rid, 'preparation_manifest_binding'])
        errors, warnings, records, fresh_seconds, usage, prompt_tokens, fresh_prompt_tokens = [], [], {}, 0.0, [], [], 0
        native_count, native_complete = 0, True
        if preparation_failed:
            if job['arm'] != 'H' or final_packet is not None or trajectory['status'] != 'preparation_error':
                packet_errors.append([rid, 'preparation_failure_contract'])
            errors.append('preparation_error')
        elif final_packet is None:
            packet_errors.append([rid, 'successful_preparation_packet_missing'])
        else:
            final_packets[rid] = final_packet
            packet_errors.extend(packet_checks(final_packet, stem, rid, manifests, masked, seed))
            if job['arm'] == 'G0':
                oldret = read(REFERENCE / 'retrieval' / f'{original_rid}.json')
                expected_packet = {'source': oldret['source'], 'plain': oldret['observations'], 'audit': oldret['audit']}
                if final_packet != expected_packet:
                    packet_errors.append([rid, 'G0_original_G_packet_changed'])
            if any(q.get('error') for q in final_packet['audit'].get('additional_queries', [])):
                errors.append('native_query_error')
        prep_errors, native_count, native_complete, prep = preparation_record_checks(job, plan_pairs[f"{stem}_r{job['repeat']}"], final_packet, seed, manifests)
        packet_errors.extend(prep_errors)
        new_native_total += native_count
        if job['arm'] == 'H':
            native_observed_total += prep.get('observed_native_trace_count', 0)
            if prep.get('native_query_count') is None:
                native_count_unknown.append(rid)
        if not native_complete:
            native_incomplete.append(rid)
        if job['arm'] == 'H':
            preparation_end_times.append(prep['ended_unix'])
        fresh_calls = 0
        for stage, packet in [(1, seed), (2, final_packet)]:
            resolved = e.resolve_stage(job, stage)
            entry, key = resolved['entry'], e.stage_key(job, stage)
            if entry['job'] != job or entry['stage'] != stage or entry['settings'] != settings[stage]:
                reuse_errors.append([key, 'logical_binding_or_settings'])
            expected_paths = ({'raw_path': REFERENCE / 'raw' / f'{original_rid}_s1.json',
                               'input_path': REFERENCE / 'inputs' / f'{original_rid}_s1.json',
                               'trace_path': REFERENCE / 'raw' / f'{original_rid}_s1.trace.json',
                               'schema_path': REFERENCE / 'schema_first.json'} if stage == 1 else
                              {'raw_path': R / 'raw' / f'{key}.json', 'input_path': R / 'inputs' / f'{key}.json',
                               'trace_path': R / 'raw' / f'{key}.trace.json', 'schema_path': R / 'schema_final.json'})
            if any(resolved[field] != wanted for field, wanted in expected_paths.items()):
                reuse_errors.append([key, 'physical_path_binding'])
            if packet is not None:
                if entry['logical_packet_file_sha256'] != sha(e.packet_path(job, stage)) or entry['logical_packet_sha256'] != e.packet_digest(packet):
                    reuse_errors.append([key, 'logical_packet_binding'])
                first_response = read(REFERENCE / 'raw' / f'{original_rid}_s1.json')['response']
                prior = {k: v for k, v in first_response.items() if k != 'context_requests'} if stage == 2 else None
                expected_prompt = e.build_prompt(meta, packet, stage == 1, prior)
                if digest(expected_prompt) != entry['expected_prompt_sha256']:
                    reuse_errors.append([key, 'manifest_prompt_reconstruction'])
            elif not preparation_failed or entry.get('logical_packet_file_sha256') is not None or entry.get('logical_packet_sha256') is not None:
                reuse_errors.append([key, 'unexplained_missing_packet'])
            if stage == 2 and preparation_failed and entry.get('expected_prompt_sha256') is not None:
                reuse_errors.append([key, 'failed_preparation_has_prompt'])
            if bool(resolved['reused']) != (stage == 1):
                reuse_errors.append([key, 'stage_reuse_contract'])
            if stage == 1:
                reused_keys.add(key)
                if resolved['original_stage_id'] != original_rid + '_s1':
                    reuse_errors.append([key, 'wrong_original_G_first'])
                for field, expected_hash in entry['original_file_sha256'].items():
                    origin = resolved[field]
                    sealed = originals.get(str(origin.relative_to(REFERENCE)), {})
                    sealed = sealed.get('sha256') if isinstance(sealed, dict) else sealed
                    if sha(origin) != expected_hash or expected_hash != sealed:
                        reuse_errors.append([key, 'original_file_hash', field])
                if sha(resolved['raw_path']) != old_ann[original_rid]['raw_sha256']['1']:
                    shared_first_annotation_errors.append([key, 'original_annotation_hash'])
                if (R / 'raw' / f'{key}.json').exists() or (R / 'inputs' / f'{key}.json').exists():
                    reuse_errors.append([key, 'counterfeit_first_stage'])
                pair_first[stem, job['repeat']][job['arm']] = sha(resolved['raw_path'])
            else:
                fresh_calls = int((R / 'actual_cli_starts' / f'{key}.json').exists())
                if preparation_failed and fresh_calls:
                    reuse_errors.append([key, 'called_failed_preparation'])
                retrieval_path = R / 'retrieval' / f'{rid}.json'
                if retrieval_path.exists():
                    ret = read(retrieval_path)
                    if final_packet is None or {k: ret[v] for k, v in [('source', 'source'), ('plain', 'observations'), ('audit', 'audit')]} != final_packet:
                        packet_errors.append([rid, 'model_loop_packet_changed'])
                    expected_ret = {'packet_sha256': e.packet_digest(final_packet) if final_packet else None,
                                    'actual_additional_native_trace': [], 'model_loop_fresh_native_queries': 0,
                                    'native_trace_prepared_once': True, 'prepared_before_model_calls': True,
                                    'preparation_record': str(prep_path.relative_to(ROOT)), 'preparation_record_sha256': sha(prep_path),
                                    'shared_first_stage_id': original_rid + '_s1',
                                    'native_trace_reference': prep.get('native_trace_path'),
                                    'native_trace_reference_sha256': prep.get('native_trace_sha256'),
                                    'native_trace_is_historical': job['arm'] == 'G0'}
                    if any(ret.get(k) != v for k, v in expected_ret.items()):
                        packet_errors.append([rid, 'model_loop_preparation_provenance'])
                elif fresh_calls:
                    packet_errors.append([rid, 'called_without_stored_retrieval'])
            if not resolved['raw_path'].exists():
                if not preflight:
                    errors.append(f'missing_stage_{stage}')
                continue
            record = read(resolved['raw_path'])
            records[stage] = record
            if stage == 2:
                fresh_records.add(key)
            if not resolved['input_path'].exists() or not resolved['trace_path'].exists():
                errors.append(f'missing_input_or_trace_{stage}'); continue
            inp, trace = read(resolved['input_path']), read(resolved['trace_path'])
            if record['run_id'] != resolved['original_stage_id'] or inp['run_id'] != resolved['original_stage_id']:
                errors.append(f'stage_identity_{stage}')
            if record['model'] != frozen['model'] or e.normalized_command(trace['command']) != settings[stage]['command']:
                errors.append(f'model_or_command_{stage}')
            if record['status'] != 'ok':
                errors.append(f'transport_or_schema_{stage}')
            if record.get('tool_audit', {}).get('tool_call_count') != 0:
                errors.append(f'model_tools_{stage}')
            if digest(inp['prompt']) != inp['prompt_sha256'] or inp['prompt_sha256'] != record['prompt_sha256']:
                errors.append(f'prompt_hash_{stage}')
            if packet is None or inp['packet_sha256'] != e.packet_digest(packet) or record['packet_sha256'] != inp['packet_sha256']:
                errors.append(f'packet_binding_{stage}')
            if packet is not None:
                prior = {k: v for k, v in records[1]['response'].items() if k != 'context_requests'} if stage == 2 else None
                expected_prompt = e.build_prompt(meta, packet, stage == 1, prior)
                if inp['prompt'] != expected_prompt or digest(expected_prompt) != entry['expected_prompt_sha256']:
                    errors.append(f'prompt_prior_reconstruction_{stage}')
                if stage == 2 and job['arm'] == 'G0' and inp['prompt'] != read(REFERENCE / 'inputs' / f'{original_rid}_s2.json')['prompt']:
                    errors.append('G0_original_final_prompt_changed')
            if stage == 2:
                fresh_seconds += record.get('duration_seconds', 0)
                usage.extend(record.get('usage', []))
                fresh_prompt_tokens = token_count(inp['prompt'])
                if not fresh_calls:
                    errors.append('fresh_start_missing')
            prompt_tokens.append(token_count(inp['prompt']))
            response = record.get('response')
            if not response:
                if stage == 1:
                    reuse_errors.append([key, 'reused_response_absent'])
                continue
            if list(validators[stage].iter_errors(response)):
                errors.append(f'response_schema_{stage}'); continue
            local_errors, local_warnings, accounting = response_checks(response, packet, meta['common'])
            if stage == 2:
                errors.extend(local_errors)
            else:
                warnings.extend('first_stage:' + x for x in local_errors)
            warnings.extend(f's{stage}:' + x for x in local_warnings)
            coverage.append({'run_id': rid, 'arm': job['arm'], 'stage': stage, 'reused': stage == 1, **accounting})
        if trajectory_path.exists() and trajectory.get('physical_calls') != fresh_calls:
            errors.append('trajectory_call_accounting')
        rows.append({**job, 'label': labels[job['case_id']]['worlds'][job['world']],
                     'status': trajectory['status'], 'errors': errors, 'warnings': warnings,
                     'first': records.get(1, {}).get('response'), 'final': records.get(2, {}).get('response'),
                     'physical_calls': fresh_calls, 'logical_stages': 2, 'reused_stages': 1,
                     'source_tokens': token_count(final_packet['source']) if final_packet else 0,
                     'observation_tokens': token_count(final_packet['plain']) if final_packet else 0,
                     'seconds': fresh_seconds, 'usage': usage, 'total_prompt_tokens': sum(prompt_tokens),
                     'fresh_prompt_tokens': fresh_prompt_tokens,
                     'native_preparation_seconds': prep.get('elapsed_seconds', 0) if job['arm'] == 'H' else 0,
                     'new_native_queries': native_count, 'native_trace_complete': native_complete,
                     'native_query_count_known': job['arm'] == 'G0' or prep.get('native_query_count') is not None,
                     'original_first_run_id': original_rid})
    by_position = collections.defaultdict(dict)
    for row in rows:
        by_position[row['case_id'], row['world'], row['repeat']][row['arm']] = row
    for pair in by_position.values():
        p0, ph = (final_packets.get(pair[a]['run_id']) for a in ('G0', 'H'))
        same = bool(p0 is not None and ph is not None and p0['source'] == ph['source'] and p0['plain'] == ph['plain'])
        pair['G0']['source_plain_equals_G0'] = True
        pair['H']['source_plain_equals_G0'] = same
    if len(pair_first) != 72 or any(set(p) != {'G0', 'H'} or len(set(p.values())) != 1 for p in pair_first.values()):
        reuse_errors.append(['shared_first_pair_identity'])
    actual_raw = {p.stem for p in (R / 'raw').glob('*_s[12].json')}
    actual_starts = {p.stem for p in (R / 'actual_cli_starts').glob('*.json')}
    allowed_fresh = {e.stage_key(j, 2) for j in jobs if e.resolve_stage(j, 2)['entry'].get('preparation_status') != 'error'}
    if not actual_starts <= allowed_fresh or actual_starts & reused_keys:
        reuse_errors.append(['CLI_inventory', sorted(actual_starts - allowed_fresh)])
    if len(actual_starts) != sum(x['physical_calls'] for x in rows):
        reuse_errors.append(['unattributed_CLI_start'])
    if actual_starts and preparation_end_times:
        if min(read(R / 'actual_cli_starts' / f'{key}.json')['started_unix'] for key in actual_starts) < max(preparation_end_times):
            packet_errors.append(['native_preparation_overlaps_model_calls'])
    started_at = sorted(read(R / 'actual_cli_starts' / f'{key}.json')['started_unix'] for key in actual_starts)
    if any(b - a < 2.99 for a, b in zip(started_at, started_at[1:])):
        reuse_errors.append(['actual_CLI_start_spacing'])
    regression = reference_checks()
    checks.update(packet_and_native_integrity=not packet_errors, shared_common_prompts=not common_errors,
                  complete_trajectories=len(rows) == 144 and all(x['status'] != 'missing' for x in rows),
                  reuse_integrity=not reuse_errors, legacy_gate_regression=regression['passed'],
                  shared_first_annotations=not shared_first_annotation_errors,
                  exact_model_record_inventory=actual_raw == fresh_records,
                  all_operational_valid=all(x['status'] == 'ok' and not x['errors'] for x in rows))
    output = {'checks': checks, 'preflight': preflight, 'algorithm_errors': algorithm_errors, 'freeze_errors': freeze_errors,
              'workspace_errors': workspace_errors, 'packet_errors': packet_errors, 'reuse_errors': reuse_errors,
              'common_errors': common_errors, 'first_annotation_errors': shared_first_annotation_errors,
              'legacy_regression': regression, 'logical_trajectories': len(rows), 'logical_stages': len(rows) * 2,
              'reused_first_slots': len(reused_keys), 'unique_original_first_observations': len(pair_first),
              'fresh_physical_calls': len(actual_starts), 'actual_new_native_requests': new_native_total,
              'actual_new_native_response_traces_observed': native_observed_total,
              'native_requests_count_is_lower_bound': bool(native_count_unknown), 'native_count_unknown_rows': native_count_unknown,
              'native_trace_incomplete_rows': native_incomplete,
              'rows': rows}
    save(R / ('audit_preflight.json' if preflight else 'audit_all.json'), output)
    if not preflight:
        save(R / 'context_consumption_audit.json', {'rows': coverage, 'assessment_errors_are_auxiliary': True})
    return output

def selfcheck():
    """Synthetic gate/count regressions only; no retrieval or model calls."""
    packet = {'source': '', 'plain': '', 'audit': {'source_lines': {}, 'base_lines': {}}}
    common = '[DIFF]\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-old\n+new\n'
    response = {'checks': [], 'review': {'status': 'reported_issues', 'issues': [
        {'file': 'a.py', 'line': 1, 'evidence_ids': ['DIFF']}], 'unresolved_questions': []},
        'context_assessment': {'used_evidence': [{'evidence_id': 'UNKNOWN', 'conclusion': 'test'}],
                               'dismissed_evidence': [], 'missing_connections': []}}
    errors, warnings, accounting = response_checks(response, packet, common)
    assert errors == [] and warnings and accounting['invalid_assessment_ids'] == ['UNKNOWN']
    response['review']['issues'][0]['file'] = 'unseen.py'
    assert 'issue_file_0' in response_checks(response, packet, common)[0]
    response['review']['issues'][0]['file'] = 'a.py'
    response['review']['issues'][0]['evidence_ids'] = ['UNKNOWN']
    assert 'issue_evidence_0' in response_checks(response, packet, common)[0]
    response['review']['issues'][0]['evidence_ids'] = ['DIFF']
    base = {'run_id': 'synthetic', 'case_id': 'S', 'world': 'a', 'arm': 'H', 'repeat': 1,
            'label': 'unsafe', 'status': 'ok', 'errors': [], 'final': response}
    ann = {'supported_issue_indices': [], 'unsupported_issue_indices': [0], 'local_supported_issue_indices': [],
           'safe_verdict_supported': False, 'first_target_state': 'unresolved', 'final_target_state': 'reported_unsupported'}
    row = graded_row(base, ann)
    assert (row['grounded_tp'], row['grounded_fp'], row['grounded_fn']) == (0, 1, 1)
    assert metric(0, 1, 1)['precision'] == 0
    assert metric(0, 0, 1)['precision'] is None
    ann['local_supported_issue_indices'] = [0]
    assert graded_row(base, ann)['local_tp'] == 1
    failure = {**base, 'status': 'preparation_error', 'final': None}
    empty = {**ann, 'unsupported_issue_indices': [], 'local_supported_issue_indices': []}
    failed = graded_row(failure, empty)
    assert failed['grounded_fn'] == failed['local_fn'] == failed['status_fn'] == 1
    assert failed['abstained'] is False and failed['operational_failure'] is True
    safe = graded_row({**base, 'label': 'safe', 'status': 'transport_error'}, ann)
    assert safe['safe_false_alarm_reviews'] == safe['grounded_fp'] == 1
    q = {'file': 'a.py', 'line': 3, 'character_utf16': 4, 'operation': 'definition', 'raw': []}
    event = {'method': 'textDocument/definition', 'params': {'textDocument': {'uri': 'file:///tmp/a.py'},
             'position': {'line': 2, 'character': 4}}, 'response': {'result': []}}
    assert native_checks([event], [q], '/tmp', 'fixture') == ([], 1)
    changed = json.loads(json.dumps(event)); changed['params']['position']['character'] = 5
    assert native_checks([changed], [q], '/tmp', 'fixture')[0]
    # A static fixture uses an existing sealed source file; it makes no native
    # requests and never writes to the snapshot. Native provenance is fabricated
    # solely to test the audit's acceptance/rejection boundaries.
    manifest = read(next((R / 'workspace_manifests').glob('*.json')))
    file = next(iter(manifest['sources']))
    location_range = {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 1}}
    location = {'file': file, 'line': 1, 'character_utf16': 0, 'range': location_range}
    raw = [{'uri': (Path(manifest['root']) / file).as_uri(), 'range': location_range}]
    query = {'operation': 'definition', 'file': file, 'line': 1, 'character_utf16': 0, 'raw': raw}
    qkey = ['definition', file, 0, 0]
    provenance = {'phase': 'additional', 'query_index': 1, 'query_key': qkey,
                  'raw_sha256': digest(json.dumps(raw, sort_keys=True)), 'returned_location': location}
    def node(kind, rg):
        identity = {'version': 'head', 'file': file, 'range': rg, 'source_sha256': manifest['sources'][file]}
        nid = ('S-' if kind == 'native_definition_symbol' else 'O-') + digest(json.dumps(identity, sort_keys=True))[:24]
        return {'id': nid, 'kind': kind, **identity}
    anchor = node('occurrence', {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 0}})
    target, symbol = node('occurrence', location_range), node('native_definition_symbol', location_range)
    graphpacket = {'audit': {'mode': 'H', 'source_lines': {file: [1]}, 'additional_queries': [query], 'symbol_graph': {
        'schema': 1, 'snapshots': {file: manifest['sources'][file]}, 'nodes': [anchor, target, symbol],
        'edges': [{'from': anchor['id'], 'to': target['id'], 'kind': 'native_definition', 'provenance': provenance},
                  {'from': target['id'], 'to': symbol['id'], 'kind': 'native_definition_location', 'provenance': provenance}],
        'canonical_symbols': [{'symbol': symbol['id'], 'occurrences': [qkey]}]},
        'candidate_rankings': [{'query_index': 1, 'candidate_window': [location], 'ranked_candidates': [location],
                                'admitted_packages': 1, 'uninspected_native_locations': 0}],
        'native_admission_totals': [{'phase': 'additional', 'query_index': 1, 'packages': [[file, 'x']]}]}}
    assert not graph_checks(graphpacket, {'audit': {'queries': []}}, manifest, 'fixture')
    corrupted = json.loads(json.dumps(graphpacket))
    corrupted['audit']['symbol_graph']['edges'][0]['provenance']['raw_sha256'] = 'wrong'
    assert graph_checks(corrupted, {'audit': {'queries': []}}, manifest, 'fixture')
    corrupted = json.loads(json.dumps(graphpacket))
    corrupted['audit']['native_admission_totals'][0]['packages'] = [[file, str(i)] for i in range(5)]
    assert graph_checks(corrupted, {'audit': {'queries': []}}, manifest, 'fixture')
    assert not sealed_files(R / 'reference_seals.json')
    pairs, plan_errors = preparation_plan_checks()
    assert len(pairs) == 72 and not plan_errors, plan_errors
    # Timeout accounting: six attempted requests, five saved response events,
    # no eligible final packet. Keep attempts known while trace is incomplete.
    import tempfile
    original_root = R
    pair = next(iter(pairs.values()))
    job = {'run_id': pair['pair_id'].replace('_r', '_H_r'), 'case_id': pair['case_id'],
           'world': pair['world'], 'repeat': pair['repeat'], 'arm': 'H'}
    seed = read(ROOT / pair['seed_path'])
    manifests, _ = snapshot_checks()
    with tempfile.TemporaryDirectory(prefix='audit-count-fixture-', dir=original_root) as folder:
        temporary = Path(folder)
        for name in ('native_claims', 'native_traces/H', 'preparation/H'):
            (temporary / name).mkdir(parents=True, exist_ok=True)
        globals()['R'] = temporary
        try:
            save(R / 'algorithm_seal.json', {'files': {}})
            sealhash = sha(R / 'algorithm_seal.json')
            save(R / 'native_claims' / f"{pair['pair_id']}.json", {
                'algorithm_seal_sha256': sealhash, 'first_response_sha256': pair['first_response_sha256'],
                'pair_id': pair['pair_id'], 'maximum_queries': 6})
            tracepath = R / 'native_traces/H' / f"{pair['pair_id']}.json"
            save(tracepath, {'actual_trace': [event] * 5, 'attempted_queries': [q] * 6,
                'native_query_count': 6, 'observed_native_trace_count': 5, 'trace_complete': False,
                'algorithm_seal_sha256': sealhash, 'error': 'synthetic timeout'})
            prep = {'pair_id': pair['pair_id'], 'arm': 'H', 'shared_first_stage_id': pair['original_run_id'] + '_s1',
                'first_response_sha256': pair['first_response_sha256'], 'status': 'error', 'algorithm_seal_sha256': sealhash,
                'native_trace_path': str(tracepath.relative_to(ROOT)), 'native_trace_sha256': sha(tracepath),
                'native_query_count': 6, 'attempted_native_query_count': 6, 'observed_native_trace_count': 5, 'trace_complete': False}
            save(R / 'preparation/H' / f"{pair['pair_id']}.json", prep)
            failure_errors, attempts, complete, _ = preparation_record_checks(job, pair, None, seed, manifests)
            assert not failure_errors and attempts == 6 and not complete, failure_errors
            prep['observed_native_trace_count'] = 6
            save(R / 'preparation/H' / f"{pair['pair_id']}.json", prep)
            assert preparation_record_checks(job, pair, None, seed, manifests)[0]
        finally:
            globals()['R'] = original_root
    regression = reference_checks()
    assert regression['passed'], regression
    result = {'status': 'passed', 'actual_model_calls': 0, 'actual_native_calls': 0,
              'checks': ['DIFF-only file citation allowed', 'source-line mismatch remains warning',
                         'unknown assessment ID auxiliary', 'unknown issue ID rejected', 'unseen file rejected',
                         'unsupported reports retain FP', 'local support does not change strict',
                         'preparation failure retains FN and is not abstention', 'safe reports count even if transport invalid',
                         'native URI/UTF16/result match', 'typed graph provenance and cumulative fanout rejection',
                         'named reference seal support', '72 first/seed/G0 exact lineage and prompt bindings',
                         'timeout: six known attempts versus five observed responses, no model packet',
                         'legacy F/G144 gates and count regression'],
              'legacy_regression': regression}
    save(R / 'audit_selfcheck_results.json', result)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--preparation-preflight', action='store_true')
    p.add_argument('--preflight', action='store_true')
    p.add_argument('--score', action='store_true')
    p.add_argument('--selfcheck', action='store_true')
    args = p.parse_args()
    if args.selfcheck:
        out = selfcheck()
    elif args.preparation_preflight:
        out = preparation_preflight()
    else:
        out = audit_cohort(preflight=args.preflight)
        if args.score:
            assert not args.preflight
            out = score(out)
    print(json.dumps({k: v for k, v in out.items() if k not in {'rows', 'per_case'}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
