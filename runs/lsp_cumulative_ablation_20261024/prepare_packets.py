"""Read sealed controls and prepare fixed B0/B1/B2 evidence, without model calls."""
import argparse
import concurrent.futures
import copy
import hashlib
import json
from pathlib import Path

import packet_policy as policy

R = Path(__file__).resolve().parent
ROOT = R.parents[1]
PREVIOUS = ROOT / 'runs/lsp_agentic_preserved_recovery_20261023'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_equal(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        assert read(path) == json.loads(json.dumps(value)), ('existing preparation differs', str(path))
    else:
        with path.open('x') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)


def copy_equal(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        assert target.read_bytes() == Path(source).read_bytes()
    else:
        with target.open('xb') as stream:
            stream.write(Path(source).read_bytes())


def verify(path):
    path = Path(path)
    candidates = []
    for sealname in ['freeze.json', 'algorithm_seal.json', 'result_seal.json']:
        seal = read(PREVIOUS / sealname)
        keys = [str(path.relative_to(ROOT))]
        if path.is_relative_to(PREVIOUS):keys.append(str(path.relative_to(PREVIOUS)))
        for key in keys:
            if key in seal.get('files', {}):
                expected = seal['files'][key]
                if isinstance(expected, dict):expected = expected['sha256']
                assert isinstance(expected, str) and sha(path) == expected, ('sealed dependency changed', str(path))
                candidates.append({'seal': str((PREVIOUS / sealname).relative_to(ROOT)), 'seal_sha256': sha(PREVIOUS / sealname), 'key': key, 'sha256': expected})
    assert candidates, ('dependency not found in predecessor seals', str(path))
    return candidates


def controls():
    records = []
    for name in ['selected.json', 'scopes.json', 'labels.json', 'witness_validation.json']:
        src = PREVIOUS / name
        seals = verify(src)
        copy_equal(src, R / name)
        records.append({'source': str(src.relative_to(ROOT)), 'destination': name, 'sha256': sha(src), 'seals': seals})
    for folder in ['cases', 'workspace_manifests']:
        for src in sorted((PREVIOUS / folder).glob('*.json')):
            seals = verify(src)
            target = R / folder / src.name
            copy_equal(src, target)
            records.append({'source': str(src.relative_to(ROOT)), 'destination': str(target.relative_to(R)), 'sha256': sha(src), 'seals': seals})
    save_equal(R / 'control_provenance.json', {'controls_copied_byte_for_byte': True,
        'selection_never_reads_labels_witness_or_case_root_audit': True,
        'case_common_is_the_only_model_visible_case_content': True, 'files': records})


def verify_workspace_files(manifest, files):
    root = Path(manifest['root'])
    records = {}
    for f in sorted(set(files)):
        assert f in manifest['sources'], ('file outside sealed production corpus', f)
        actual = policy.digest((root / f).read_text())
        assert actual == manifest['sources'][f], ('workspace source changed', f)
        records[f] = actual
    return records


def prepare_one(stem):
    # Builders receive explicit narrow inputs, never this metadata dictionary.
    meta = read(R / 'cases' / f'{stem}.json')
    cid, world = stem.rsplit('_', 1)
    other = 'b' if world == 'a' else 'a'
    head_manifest = read(R / 'workspace_manifests' / f'{stem}.json')
    base_manifest = read(R / 'workspace_manifests' / f'{cid}_{other}.json')
    root, base_root = Path(head_manifest['root']), Path(base_manifest['root'])
    scope = read(R / 'scopes.json')[meta['instance_id']]['scope']
    changed = meta['changed_files']
    head = {f: (root / f).read_text() for f in changed if f in head_manifest['sources']}
    base = {f: (base_root / f).read_text() for f in changed if f in base_manifest['sources']}
    head_checked = verify_workspace_files(head_manifest, head)
    base_checked = verify_workspace_files(base_manifest, base)
    for arm, packet in [('B0', policy.diff_only()), ('B1', policy.ast_only(base, head))]:
        for phase in ['initial', 'final']:
            save_equal(R / 'packets' / arm / f'{stem}_{phase}.json', packet)
    seed_path = PREVIOUS / 'packets/seed' / f'{stem}_initial.json'
    fixed_path = PREVIOUS / 'packets/F' / f'{stem}_fixed.json'
    trace_path = PREVIOUS / 'lsp_fixed' / f'{stem}.json'
    evidence_seals = {str(p.relative_to(ROOT)): verify(p) for p in [seed_path, fixed_path, trace_path]}
    seed, fixed, trace = read(seed_path), read(fixed_path), read(trace_path)
    cached_answers = [x for x in trace['actual_trace'] if x.get('method') in ['textDocument/definition', 'textDocument/references']]
    queries = fixed['audit']['additional_queries']
    assert len(cached_answers) == len(queries) <= 6
    for recorded, q in zip(cached_answers, queries):
        assert recorded['method'] == 'textDocument/' + q['operation']
        assert recorded['params']['position'] == {'line': q['line'] - 1, 'character': q['character_utf16']}
        assert recorded['response'].get('result') == q['raw']
    head_files = set(fixed['audit']['lazy_parsed_files']) | set(seed['audit']['source_lines'])
    base_files = set(seed['audit']['base_lines']) | {f for f, _ in seed['audit'].get('deleted_roots', [])}
    head_checked.update(verify_workspace_files(head_manifest, head_files))
    base_checked.update(verify_workspace_files(base_manifest, base_files))
    candidates = policy.capture_fixed_history(root, base_root, seed, fixed, scope)
    final, packing = policy.pack_fixed_history(root, base_root, seed, fixed, candidates)
    copy_equal(seed_path, R / 'packets/B2' / f'{stem}_initial.json')
    if packing['same_f_source_plain']:
        copy_equal(fixed_path, R / 'packets/B2' / f'{stem}_final.json')
    else:
        save_equal(R / 'packets/B2' / f'{stem}_final.json', final)
    save_equal(R / 'candidate_history' / f'{stem}.json', {'source': str(fixed_path.relative_to(ROOT)),
        'source_sha256': sha(fixed_path), 'fixed_native_queries': queries,
        'fresh_native_queries': 0, 'candidates': candidates, 'packing': packing})
    row = {'world': stem, 'changed_files': changed, 'fixed_query_replay_verified': True,
        'B2_initial_source_plain_exact_F': True, 'B2_final_source_plain_exact_F': packing['same_f_source_plain'],
        'B2_initial_file_sha256': sha(seed_path), 'B2_final_reference_sha256': sha(fixed_path),
        'source_tokens': {arm: policy.token_count(read(R / 'packets' / arm / f'{stem}_final.json')['source']) for arm in ['B0', 'B1', 'B2']},
        'fresh_native_queries': 0, 'cached_seed_native_queries': len(seed['audit']['queries']),
        'cached_fixed_followup_native_queries': len(queries), 'evicted_seed_packages': packing['evicted_seed_packages'],
        'dropped_seed_lines': packing.get('dropped_seed_lines', {}),
        'verified_head_file_hashes': head_checked, 'verified_base_file_hashes': base_checked,
        'evidence_seals': evidence_seals}
    save_equal(R / 'preparation' / f'{stem}.json', row)
    return row


def check():
    rows = [read(p) for p in sorted((R / 'preparation').glob('*.json'))]
    assert len(rows) == 24
    for row in rows:
        stem = row['world']
        for arm in ['B0', 'B1', 'B2']:
            initial = read(R / 'packets' / arm / f'{stem}_initial.json')
            final = read(R / 'packets' / arm / f'{stem}_final.json')
            for packet in [initial, final]:
                assert policy.token_count(packet['source']) <= 5200
                assert policy.token_count(packet['plain']) <= 800
            if arm in ['B0', 'B1']:
                assert initial == final and initial['audit']['queries'] == []
            if arm == 'B0':assert initial['source'] == initial['plain'] == ''
            if arm == 'B1':assert initial['audit']['root_coverage'] and initial['audit']['has_roots']
            if arm == 'B2':
                seed = read(PREVIOUS / 'packets/seed' / f'{stem}_initial.json')
                fixed = read(PREVIOUS / 'packets/F' / f'{stem}_fixed.json')
                assert initial == seed and final['audit']['queries'] == fixed['audit']['queries']
                if row['B2_final_source_plain_exact_F']:assert final == fixed
                else:assert final['audit']['changed_deleted_roots_preserved']
    summary = {'all_checks_passed': True, 'worlds': len(rows), 'packet_files': 144,
        'fresh_native_queries': 0,
        'cached_seed_native_queries_by_world': sum(x['cached_seed_native_queries'] for x in rows),
        'cached_fixed_followup_native_queries_by_world': sum(x['cached_fixed_followup_native_queries'] for x in rows),
        'B2_final_identical_worlds': sum(x['B2_final_source_plain_exact_F'] for x in rows),
        'B2_changed_worlds': [x['world'] for x in rows if not x['B2_final_source_plain_exact_F']],
        'B0_B1_same_evidence_both_stages': True,
        'B2_fixed_query_history_and_candidate_order': True,
        'policy_selects_without_labels_witnesses': True,
        'rows': rows}
    save_equal(R / 'packet_preflight.json', summary)
    save_equal(R / 'preparation_summary.json', {k: v for k, v in summary.items() if k != 'rows'})
    print(json.dumps({k: v for k, v in summary.items() if k != 'rows'}), flush=True)
    return summary


def prepare():
    assert not list((R / 'raw').glob('*_s[12].json')), 'prepare only before fresh evaluation calls'
    controls()
    stems = [p.stem for p in sorted((R / 'cases').glob('*.json'))]
    assert len(stems) == 24
    with concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='packet_prepare') as pool:
        for row in pool.map(prepare_one, stems):
            print(json.dumps({'prepared': row['world'], 'B2_changed': not row['B2_final_source_plain_exact_F'], 'source_tokens': row['source_tokens']}), flush=True)
    return check()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'check'])
    args = parser.parse_args()
    prepare() if args.command == 'prepare' else check()
