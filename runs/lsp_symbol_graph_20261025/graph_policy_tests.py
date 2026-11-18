"""Non-evaluation synthetic real-Pyright tests for H; never calls a model.

Run from repository root with the existing parallel-review venv. Temporary
workspaces and native result records are isolated. JSON output includes query
coordinates/results, so it can be saved by the owning experiment harness.
"""
import ast
import copy
import json
import tempfile
from pathlib import Path
import graph_policy as p
import audit_score


def write(root, files):
    root.mkdir(parents=True)
    for f, text in files.items():
        target = root / f
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    (root / 'pyrightconfig.json').write_text(json.dumps({'include': ['**/*.py'], 'pythonVersion': '3.11', 'typeCheckingMode': 'off', 'extraPaths': ['.']}))


def seed(root, base_root, base, head, queries=0):
    old = p.g._structure._old.Navigator(base, head, root, 'Inspect the changed function.')
    trace = []
    if queries:
        client = p.Client(root)
        try:
            old.advance(client, queries)
        finally:
            client.close()
        trace = client.trace
    return p.prepare_seed(root, base_root, {'source': old.source(), 'plain': old.observations(False), 'audit': old.audit()}, ''), trace


def setup(tmp, name, head, base=None, queries=0):
    root, base_root = tmp / name / 'head', tmp / name / 'base'
    if base is None:
        base = {f: text.replace('+ 1', '+ 0') for f, text in head.items()}
    write(root, head);write(base_root, base)
    common, trace = seed(root, base_root, base, head, queries)
    return root, base_root, common, trace


def validate(common, result, root):
    packet = result['packet'];a = packet['audit']
    assert packet['source'].startswith(common['source'])
    assert packet['plain'].startswith(common['plain'])
    assert p.token_count(packet['source']) <= 5200 and p.token_count(packet['plain']) <= 800
    assert a['queries'][:len(common['audit']['queries'])] == common['audit']['queries']
    assert len(a['additional_queries']) <= 6 and len(a['queries']) <= 18
    assert all(q['depth'] <= 3 and q['error'] is None for q in a['additional_queries']), a['additional_queries']
    assert len(a['additional_queries']) == sum(t.get('method') in {'textDocument/definition', 'textDocument/references'} for t in result['actual_trace'])
    keys = [(q['operation'], q['file'], q['line'], q['character_utf16']) for q in a['queries']]
    assert len(keys) == len(set(keys))
    for f, lines in common['audit']['source_lines'].items():
        assert set(lines) <= set(a['source_lines'][f])
    assert a['base_lines'] == common['audit']['base_lines']
    if a.get('mode') != 'H':
        return
    graph = a['symbol_graph'];nodes = {n['id']: n for n in graph['nodes']}
    for edge in graph['edges']:
        assert edge['from'] in nodes and edge['to'] in nodes
        if edge['kind'].startswith('native_'):
            prov = edge['provenance']
            queries = common['audit']['queries'] if prov['phase'] == 'seed' else a['additional_queries']
            q = queries[prov['query_index'] - 1]
            assert prov['raw_sha256'] == p.digest(json.dumps(q['raw'], sort_keys=True))
            assert prov['query_key'] == list(p._qkey(q))
    assert all(x['admitted_packages'] <= 4 and len(x['candidate_window']) <= 32 for x in a['candidate_rankings'])
    assert all(len(x['packages']) <= 4 for x in a['native_admission_totals'])
    manifest = {'root': str(root.resolve()), 'sources': {f: p.digest((root / f).read_text()) for f in graph['snapshots']}}
    errors = audit_score.graph_checks(packet, common, manifest, 'synthetic')
    assert not errors, errors


def brief(result):
    a = result['packet']['audit']
    return {'queries': a['additional_queries'], 'source_lines': a['source_lines'],
            'symbol_graph': a.get('symbol_graph'), 'native_admission_totals': a.get('native_admission_totals'),
            'graph_frontier': a.get('graph_frontier'), 'candidate_rankings': a.get('candidate_rankings'),
            'actual_trace': result['actual_trace']}


def main():
    reports = {}
    with tempfile.TemporaryDirectory(prefix='typed-occurrence-fixture-') as directory:
        tmp = Path(directory)
        # Parameter definition -> explicit annotation -> native derived class
        # -> literal base -> native inherited __len__/__iter__ declarations.
        head = {'entry.py': 'from package import Crate\ndef review(value: Crate):\n    total = 0\n    for item in value:\n        total += item\n    return len(value) + total + 1\n',
                'package.py': '''class Collection:
    def __len__(self):
        return 2
    def __iter__(self):
        yield 1
        yield 2
class Crate(Collection):
    pass
'''}
        root, base, common, _ = setup(tmp, 'protocol', head)
        h = p.followup(root, base, common, '', [], True)
        control = p.g.followup(root, base, common, '', [], True)
        validate(common, h, root);validate(common, control, root)
        hc = {x['component'] for x in h['packet']['audit']['additional_admitted']}
        gc = {x['component'] for x in control['packet']['audit']['additional_admitted']}
        assert {'Collection.__len__', 'Collection.__iter__'} <= hc, brief(h)
        assert not {'Collection.__len__', 'Collection.__iter__'} <= gc
        graph = h['packet']['audit']['symbol_graph']
        assert any(len(s['occurrences']) >= 2 and {'__len__', '__iter__'} <= set(s['wanted_members']) for s in graph['canonical_symbols']), graph
        assert any(e['additional_anchor_queries'] == 0 and '__len__' in e['wanted_members'] for e in graph['canonical_closures']), graph
        reports['native_protocol_closure_changes_queries_and_bodies'] = {'H': brief(h), 'G': brief(control)}

        # Both constructor declarations are independently queried; class
        # inventory is not treated as body evidence. Conditional guards stay.
        head = {'entry.py': 'from objects import Vessel\ndef review():\n    item = Vessel()\n    return 1 + 1\n',
                'objects.py': '''ENABLED = True
class Vessel:
    def __new__(cls):
        return super().__new__(cls)
    if ENABLED:
        def __init__(self):
            self.value = 3
'''}
        root, base, common, _ = setup(tmp, 'constructor', head)
        req = {'operation': 'definition', 'file': 'entry.py', 'line': 3, 'column': 12, 'reason': 'constructor path'}
        h = p.followup(root, base, common, '', [req], True)
        validate(common, h, root)
        components = {x['component'] for x in h['packet']['audit']['additional_admitted']}
        assert {'Vessel.__new__', 'Vessel.__init__'} <= components, brief(h)
        assert 5 in h['packet']['audit']['source_lines']['objects.py']
        inv = h['packet']['audit']['class_inventories'][0]
        assert any('__init__' in e['names'] and e['conditions'] for e in inv['entries'])
        reports['native_constructors_and_conditional_inventory'] = brief(h)
        # A packaging failure after genuine native work must not discard the
        # attempted-query records/physical trace or silently retry the work.
        original_packet = p.Navigator.packet
        def fail_packet(_):
            raise RuntimeError('synthetic post-native packaging failure')
        p.Navigator.packet = fail_packet
        try:
            try:
                p.followup(root, base, common, '', [req], True)
                raise AssertionError('the controlled packaging error did not fire')
            except RuntimeError as exc:
                assert str(exc) == 'synthetic post-native packaging failure'
                native_trace = [t for t in exc.partial_trace if t.get('method') in {'textDocument/definition', 'textDocument/references'}]
                assert len(native_trace) == len(exc.partial_queries) > 0 and exc.initialization
                reports['post_native_exception_trace_preserved'] = {'actual_trace': exc.partial_trace,
                                                                    'partial_queries': exc.partial_queries,
                                                                    'initialization_present': True}
        finally:
            p.Navigator.packet = original_packet

        # A reference window with thirty writes before six reads: H prioritizes
        # actual AST consumers, and G retains lexical order. No names identify
        # equivalence; all candidates come from the same real LSP result.
        head = {'state.py': 'class State:\n    def __init__(self):\n        self.value = 0 + 1\n'}
        for i in range(36):
            body = 'obj.value = 7' if i < 30 else 'return obj.value'
            head['state.py'] += f'def use_{i}(obj: State):\n    {body}\n'
        root, base, common, _ = setup(tmp, 'consumer', head)
        req = {'operation': 'references', 'file': 'state.py', 'line': 3, 'column': 14, 'reason': 'attribute consumer'}
        h = p.followup(root, base, common, '', [req], True)
        control = p.g.followup(root, base, common, '', [req], True)
        validate(common, h, root);validate(common, control, root)
        first = h['packet']['audit']['candidate_rankings'][0]
        assert first['ranked_candidates'][0]['role'] == 'read', first
        assert first['admitted_packages'] == 4
        assert first['ranked_candidates'][0]['package'] == ['state.py', 'use_30']
        assert len(first['candidate_window']) == 32 and first['uninspected_native_locations'] == 4
        assert next(x['target'] for x in control['packet']['audit']['additional_queries'][0]['outcomes'] if 'target' in x) == 'use_0'
        replay = p.Navigator(root, base, common, '', True)
        saved_query = h['packet']['audit']['additional_queries'][0]
        replay.consume(saved_query, saved_query['raw'])
        first_source = replay.source()
        replay.consume(saved_query, saved_query['raw'])
        assert replay.source() == first_source
        assert len(replay.native_admissions[('additional', 1)]) == 4
        reports['native_attribute_consumers_rank_before_writes'] = {'H': brief(h), 'G': brief(control)}
        reports['replayed_real_native_result_cumulative_fanout'] = {'packages': 4, 'second_replay_source_unchanged': True,
                                                                 'semantics': 'packing-only replay of saved genuine native result; zero extra LSP calls'}

        # Repeated spelling in two modules is two positional symbols. One
        # snapshot change also changes identity, even at unchanged coordinates.
        head = {'entry.py': 'from left import same as first\nfrom right import same as second\ndef review():\n    return first() + second() + 1\n',
                'left.py': 'def same():\n    return 2\n', 'right.py': 'def same():\n    return 4\n'}
        root, base, common, _ = setup(tmp, 'identity', head, queries=2)
        h = p.followup(root, base, common, '', [], True);validate(common, h, root)
        symbols = [x for x in h['packet']['audit']['symbol_graph']['nodes'] if x['kind'] == 'native_definition_symbol' and x['file'] in {'left.py', 'right.py'}]
        assert len({x['file'] for x in symbols}) == 2 and len({x['id'] for x in symbols}) == 2
        assert h['packet']['audit']['symbol_graph']['seed_native_records']
        nav = p.Navigator(root, base, common, '', True)
        before = nav.occurrence('left.py', 1, 4)
        (root/'left.py').write_text('def same():\n    return 9\n')
        fresh = p.Navigator(root, base, common, '', True)
        assert fresh.occurrence('left.py', 1, 4) != before
        reports['positional_identity_snapshot_and_seed_edges'] = brief(h)

        # Cached graph results can be followed, but cannot cause repeat native
        # calls. A demand query needs a supplied identifier and depth <=3.
        budget_text = 'class Data:\n    def __init__(self):\n        self.value = 1\n'
        budget_text += ''.join(f'def helper_{i}(value):\n    return value\n' for i in range(12))
        budget_text += 'def review(value: Data):\n    return ' + ' + '.join(f'helper_{i}(value.value)' for i in range(12)) + ' + 1\n'
        root, base, common, trace = setup(tmp, 'budget', {'budget.py': budget_text}, queries=12)
        assert len(common['audit']['queries']) == 12
        h = p.followup(root, base, common, '', [], True);validate(common, h, root)
        assert len(h['packet']['audit']['queries']) == 18
        nav = p.Navigator(root, base, common, '', True)
        q = common['audit']['queries'][0]
        assert nav.query(q['operation'], q['file'], q['line']-1, q['character_utf16'], '', 0, (0,0,'',0), 'fixture') == 'already_queried'
        assert nav.query('definition', 'budget.py', q['line']-1, q['character_utf16'], '', 4, (0,0,'',0), 'fixture') == 'depth_budget'
        assert nav.query('definition', 'missing.py', 0, 0, '', 0, (0,0,'',0), 'fixture') == 'unsupported_invisible_position'
        # Exact source cap never evicts a baseline line.
        nav.seed = copy.deepcopy(common)
        while p.token_count(nav.seed['source'] + '\n0: value') < 5195:
            nav.seed['source'] += '\n0: value'
        protected = nav.seed['source']
        assert nav.add_lines('budget.py', set(range(1, 20)), 0, 'fixture', 'body', {}) == 'source_budget'
        assert nav.source() == protected
        reports['native_twelve_plus_six_and_preservation'] = {'H': brief(h), 'seed_trace': trace}

    print(json.dumps({'status': 'passed', 'scope': 'synthetic fixtures only; no models/evaluation cases', 'tests': reports}, indent=2))


if __name__ == '__main__':
    main()
