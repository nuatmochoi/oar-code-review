"""Focused fixtures use a real Pyright subprocess; no reviewer/model calls."""
import json
import ast
import shutil
import tempfile
from pathlib import Path
import context_policy as policy

R = Path(__file__).resolve().parent


def fixture_sources():
    return {
        'model.py': '''class Root:
    mode = "strict"

    def __new__(cls, value):
        return super().__new__(cls)

    def __init__(self, value):
        self.flag = value + 1

    def untouched(self):
        return self.flag

    def __deepcopy__(self, memo):
        return self
''',
        'public.py': '''from model import Root as Exported
Alias = Exported
''',
        'consumer.py': '''from model import Root

def consume(value: Root):
    return value.flag
''',
        'child.py': '''from model import Root

class Child(Root):
    optional = True
    if optional:
        def conditional(self):
            return 1
    other = 2
''',
        'guarded.py': '''try:
    from model import Root as Selected
except ImportError:
    Selected = None
''',
        'matched.py': '''mode = 1
match mode:
    case 1 if mode:
        from model import Root as Conditional
''',
    }


def static_boundaries():
    special = '''class Special:
    global exported
    exported = 2
    handler = lambda: (local := 1)
    def method(self, value=(default_binding := 3)):
        return value
'''
    inv = policy.class_inventory(special, 'special.py', 'Special', ast.parse(special).body[0])
    assert not inv['complete_explicit_lexical_inventory']
    assert inv['unsupported_binding_syntax']
    assert 'local' not in {n for e in inv['entries'] for n in e['names']}
    conditions = '''class Conditions:
    if True:
        first = 1
    else:
        second = 2
    try:
        third = 3
    finally:
        fourth = 4
'''
    inv = policy.class_inventory(conditions, 'conditions.py', 'Conditions', ast.parse(conditions).body[0])
    assert 4 in inv['signature_lines'] and 8 in inv['signature_lines']
    body = ''.join(f'        value_{i} = {i}\n' for i in range(6000))
    large = 'class Big:\n    def __new__(cls):\n' + body + '        return object.__new__(cls)\n    def changed(self):\n        return 2\n'
    nav = policy.Navigator({'big.py': large.replace('return 2\n', 'return 1\n')}, {'big.py': large}, R, 'changed returns an integer')
    before = nav.audit()['source_tokens']
    nav.admit(('big.py', 'Big'), 'native_definition')  # Unit-level packaging boundary only; not a claimed native query.
    assert nav.audit()['root_coverage']
    assert nav.audit()['source_tokens'] <= 5200
    assert any(x['member'][1] == 'Big.__new__' and not x['supplied'] for x in nav.constructor_components)
    assert before > 0 and not nav.queries
    deleted = policy.Navigator({'removed.py': 'def vanished():\n    return 1\n'}, {'removed.py': ''}, R, 'vanished returns one')
    assert deleted.deleted == [('removed.py', 'vanished')]
    assert not deleted.lines and not deleted.queue and not deleted.queries
    assert deleted.audit()['root_coverage']
    return {'global_nonlocal_walrus_inventories_marked_partial': True,
            'lambda_bindings_not_claimed_as_class_members': True,
            'else_finally_literal_headers_retained': True,
            'oversized_constructor_omission_explicit_and_changed_root_preserved': True,
            'full_function_deletion_base_only_unchanged': True}


def main():
    boundary_results = static_boundaries()
    head = fixture_sources()
    base = dict(head)
    base['model.py'] = base['model.py'].replace('self.flag = value + 1', 'self.flag = value')
    root = Path(tempfile.mkdtemp(prefix='policy-fixture-'))
    try:
        for f, s in head.items():
            (root / f).write_text(s)
        (root / 'pyrightconfig.json').write_text(json.dumps({'include': ['**/*.py'], 'pythonVersion': '3.11', 'typeCheckingMode': 'off', 'extraPaths': ['.']}))
        nav = policy.Navigator(base, head, root, 'Given an integer input, a consumer reads the stored flag.')
        client = policy.Client(root)
        try:
            nav.advance(client, 6)
            first = json.loads(json.dumps(nav.audit()))
            nav.advance(client, 12)
        finally:
            client.close()
        audit = nav.audit()
        assert first['root_coverage'] and audit['root_coverage']
        assert len(first['queries']) == 6 and len(audit['queries']) <= 12, {'first': first['queries'], 'final': audit['queries'], 'pending': audit['pending'], 'skipped': audit['skipped_queries']}
        assert all(q['error'] is None for q in audit['queries'])
        assert audit['source_tokens'] <= 5200
        assert policy.token_count(nav.observations(False)) <= 800
        assert any(q['origin'] == 'attribute_consumers' and q['raw'] for q in audit['queries'])
        assert 'consumer.py' in audit['source_lines'], audit['source_lines']
        assert any(x['file'] == 'public.py' for x in audit['module_sites']), audit['module_sites']
        assert any(x['file'] == 'public.py' and x['statement_kind'] == 'Assign' for x in audit['module_sites']), audit['module_sites']
        assert any(q['origin'] == 'native_module_binding_definition' and q['raw'] for q in audit['queries'])
        assert any(x['member'][1] == 'Root.__new__' and x['supplied'] for x in audit['constructor_components'])
        child = next(x for x in audit['class_inventories'] if x['qualname'] == 'Child')
        assert child['complete_explicit_lexical_inventory']
        assert {name for e in child['entries'] for name in e['names']} == {'optional', 'conditional', 'other'}
        assert next(e for e in child['entries'] if e['names'] == ['conditional'])['conditions']
        assert '__deepcopy__' not in {name for e in child['entries'] for name in e['names']}
        assert all(q['line'] in audit['source_lines'][q['file']] for q in audit['queries'])
        assert any(x['file'] == 'guarded.py' and x['guards'] for x in audit['module_sites'])
        matched = next(x for x in audit['module_sites'] if x['file'] == 'matched.py')
        assert any(g['kind'] == 'match_case' for g in matched['guards'])
        assert {2, 3, 4} <= set(matched['lines'])
        assert all(q['depth'] <= 3 for q in audit['queries'])
        result = {'status': 'passed', 'real_native_calls': len(audit['queries']),
                  'initial_native_calls': len(first['queries']), 'source_tokens': audit['source_tokens'],
                  'assertions': ['native attribute consumer function admitted', 'native module import/alias statements admitted',
                                 'native follow-on alias definition executed', 'native class constructor __new__ full body admitted',
                                 'complete conditional lexical class inventory with no inherited-member claim',
                                 'guarded import keeps lexical suite header', 'query/depth/source/observation budgets',
                                 'queries target supplied numbered head source'],
                  'audit': audit, 'source': nav.source(), 'actual_native_trace': client.trace,
                  'pyright_stderr': client.errors, 'fixture_sources': head,
                  'static_boundary_checks': boundary_results}
        (R / 'policy_fixture_results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k in {'status', 'real_native_calls', 'initial_native_calls', 'source_tokens', 'assertions'}}, indent=2))
    finally:
        shutil.rmtree(root)


if __name__ == '__main__':
    main()
