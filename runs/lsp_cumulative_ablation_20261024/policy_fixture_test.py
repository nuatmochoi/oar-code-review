"""Synthetic policy fixtures only; no evaluation labels or native/model calls."""
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import packet_policy as p

R = Path(__file__).resolve().parent


class PolicyTests(unittest.TestCase):
    def test_diff_has_no_selection_inputs(self):
        self.assertEqual(list(inspect.signature(p.diff_only).parameters), [])
        with patch.object(p.old, 'Client', side_effect=AssertionError('native forbidden')), patch.object(p.fixed_policy, 'Client', side_effect=AssertionError('native forbidden')), patch.object(Path, 'read_text', side_effect=AssertionError('cached/source read forbidden')):
            packet = p.diff_only()
        self.assertEqual(packet['source'], '')
        self.assertEqual(packet['plain'], '')
        self.assertEqual(packet['audit']['queries'], [])

    def test_ast_native_cache_poison_and_guards(self):
        base = {'m.py': 'import math as calc\nif ENABLED:\n    class Box:\n        def changed(self, x):\n            return calc.sqrt(x)\n'}
        head = {'m.py': base['m.py'].replace('sqrt(x)', 'sqrt(x + 1)')}
        with patch.object(p.old, 'Client', side_effect=AssertionError('native forbidden')), patch.object(p.fixed_policy, 'Client', side_effect=AssertionError('native forbidden')), patch.object(Path, 'read_text', side_effect=AssertionError('cached/source read forbidden')):
            packet = p.ast_only(base, head, 'irrelevant public symbol')
            other_scope = p.ast_only(base, head, 'changed Box calc')
        self.assertEqual(packet, other_scope)
        for literal in ['1: import math as calc', '2: if ENABLED:', '3:     class Box:', '5:             return calc.sqrt(x + 1)']:
            self.assertIn(literal, packet['source'])
        self.assertEqual(packet['audit']['queries'], [])
        self.assertEqual(packet['audit']['ast_parsed_files'], ['m.py'])

    def test_ast_deleted_only_keeps_base(self):
        base = {'m.py': 'class Box:\n    def removed(self, x):\n        return x + 1\n'}
        head = {'m.py': 'class Box:\n    pass\n'}
        packet = p.ast_only(base, head)
        self.assertEqual(packet['audit']['deleted_roots'], [('m.py', 'Box.removed')])
        self.assertIn('] base m.py;', packet['source'])
        self.assertIn('3:         return x + 1', packet['source'])
        self.assertTrue(packet['audit']['root_coverage'])

    def test_ast_conditional_import_is_not_promoted(self):
        base = {'m.py': 'if FLAG:\n    import math as calc\n\ndef changed(x):\n    return calc.sqrt(x)\n'}
        head = {'m.py': base['m.py'].replace('sqrt(x)', 'sqrt(x + 1)')}
        packet = p.ast_only(base, head)
        # Legacy package imports only direct module imports. Conditional import
        # omission stays unknown; it must not appear as an unconditional line.
        self.assertNotIn('import math as calc', packet['source'])
        self.assertIn('Missing source is unknown', packet['plain'])

    def test_replay_requires_exact_query_position(self):
        query = {'operation': 'definition', 'file': 'm.py', 'line': 2, 'character_utf16': 4, 'raw': [], 'error': None}
        client = p.ReplayClient([query])
        with self.assertRaises(AssertionError):client.references('m.py', 1, 4)
        self.assertEqual(client.definition('m.py', 1, 4), [])
        with self.assertRaises(AssertionError):client.definition('m.py', 1, 4)

    def test_budget_evicts_reverse_optional_and_preserves_shared_lines(self):
        text = ('import math\n\ndef changed(x):\n    return math.sqrt(x)\n\n'
                'def optional(x):\n    values = [' + ', '.join(str(i) for i in range(200)) + ']\n    return values[x]\n\n'
                'def additional(x):\n    return math.floor(x)\n')
        packages = [
            {'key': ['head', 'm.py', 'changed'], 'origin': 'changed_root', 'lines': [1, 3, 4]},
            {'key': ['head', 'm.py', 'optional'], 'origin': 'native_references', 'lines': [1, 6, 7, 8]},
        ]
        lines = {1, 3, 4, 6, 7, 8}
        source = '\n' + p.old.render({'m.py': text}, {'m.py': lines})
        audit = {'admitted': packages, 'source_lines': {'m.py': sorted(lines)}, 'base_lines': {}, 'queries': [], 'additional_queries': [], 'root_coverage': True, 'has_roots': True}
        seed = {'source': source, 'plain': '[E-COVERAGE] Static source only.', 'audit': audit}
        fixed = json.loads(json.dumps(seed))
        candidate = {'sequence': 0, 'kind': 'lines', 'file': 'm.py', 'lines': [1, 10, 11], 'depth': 1, 'origin': 'native_definition', 'component': 'additional', 'provenance': {'kind': 'synthetic fixed native answer'}, 'f_outcome': 'source_budget'}
        with tempfile.TemporaryDirectory(prefix='policy_fixture_', dir=R) as tmp:
            root = Path(tmp);(root / 'm.py').write_text(text)
            packet, manifest = p.pack_fixed_history(root, root, seed, fixed, [candidate], cap=p.token_count(source))
        self.assertEqual([x['seed_admission_index'] for x in manifest['evicted_seed_packages']], [1])
        self.assertEqual(packet['audit']['source_lines']['m.py'], [1, 3, 4, 10, 11])
        self.assertIn('1: import math', packet['source'])
        self.assertIn('4:     return math.sqrt(x)', packet['source'])
        self.assertIn('11:     return math.floor(x)', packet['source'])
        self.assertNotIn('7:     values', packet['source'])
        self.assertEqual(packet['audit']['queries'], fixed['audit']['queries'])


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PolicyTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    artifact = {'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'all_passed': result.wasSuccessful(), 'native_calls': 0, 'model_calls': 0, 'fixtures_are_synthetic': True}
    (R / 'policy_fixture_results.json').write_text(json.dumps(artifact, indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
