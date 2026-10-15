import unittest

from scripts.test_retrieval.augment_oracle_relaxed import RelaxedIndex, retrieve_relaxed


def selection(sources, target, **kwargs):
    index = RelaxedIndex(sources)
    changes = [{"id": target, "base_targets": [{"id": target, "reason": "existing_symbol", "confidence": 1.0}]}]
    tests, evidence, audit = retrieve_relaxed(index, changes, **kwargs)
    return {t['name'] for t in tests}, evidence, audit


class RelaxedOracleTests(unittest.TestCase):
    def test_two_and_three_edges_are_allowed_but_four_are_not(self):
        sources = {
            'core.py': 'def target(): pass\ndef one(): target()\ndef two(): one()\ndef three(): two()\n',
            'tests/test_core.py': 'from core import target, one, two, three\ndef test_direct(): target()\ndef test_two(): one()\ndef test_three(): two()\ndef test_four(): three()\n',
        }
        names, evidence, _ = selection(sources, 'core.py::target')
        self.assertEqual(names, {'test_direct', 'test_two', 'test_three'})
        self.assertEqual({e['distance'] for t in evidence for e in t['evidence']}, {1, 2, 3})
        self.assertEqual(selection(sources, 'core.py::target', max_depth=2)[0], {'test_direct', 'test_two'})

    def test_no_same_file_reference_or_registry_fallback(self):
        sources = {'core.py': 'def target(): pass\ndef other(): pass\nREGISTRY = {"target": target}\n',
                   'tests/test_core.py': 'from core import other, target, REGISTRY\ndef test_other(): other()\ndef test_ref(): assert target\ndef test_registry(): assert REGISTRY\n'}
        self.assertFalse(selection(sources, 'core.py::target')[0])

    def test_registered_builtin_fixture_resolves_caplog_receiver(self):
        sources = {
            'src/_pytest/config/__init__.py': 'default_plugins = ("logging",)\nbuiltin_plugins = set(default_plugins)\n',
            'src/_pytest/logging.py': 'import pytest\nclass LogCaptureFixture:\n    def clear(self): pass\n@pytest.fixture\ndef caplog():\n    result = LogCaptureFixture()\n    yield result\n',
            'unused.py': 'import pytest\n@pytest.fixture\ndef caplog(): return None\n',
            'testing/test_fixture.py': 'import pytest\ndef test_clear(caplog): caplog.clear()\n@pytest.mark.parametrize("caplog", [1])\ndef test_param(caplog): caplog.clear()\n',
        }
        self.assertEqual(selection(sources, 'src/_pytest/logging.py::LogCaptureFixture.clear')[0], {'test_clear'})

    def test_unregistered_fixture_is_not_guessed_by_name(self):
        sources = {'core.py': 'def target(): pass\n',
                   'plugins/unused.py': 'import pytest\nfrom core import target\n@pytest.fixture\ndef value(): target()\n',
                   'tests/test_core.py': 'def test_use(value): assert value\n'}
        self.assertFalse(selection(sources, 'core.py::target')[0])

    def test_explicit_plugin_fixture_can_reach_target(self):
        sources = {'core.py': 'def target(): pass\n',
                   'plugins/enabled.py': 'import pytest\nfrom core import target\n@pytest.fixture\ndef value(): target()\n',
                   'tests/conftest.py': 'pytest_plugins = ["plugins.enabled"]\n',
                   'tests/test_core.py': 'def test_use(value): assert value\n'}
        names, evidence, _ = selection(sources, 'core.py::target')
        self.assertEqual(names, {'test_use'})
        self.assertEqual(evidence[0]['evidence'][0]['path'][0]['kind'], 'fixture')

    def test_known_early_exit_prevents_help_false_positive(self):
        sources = {
            'core.py': 'def target(): pass\ndef Run(argv):\n    if "--help" in argv: raise SystemExit\n    target()\n',
            'tests/test_core.py': 'from core import Run\ndef test_help(): Run(["--help"])\ndef test_run(): Run([])\n',
        }
        self.assertEqual(selection(sources, 'core.py::target')[0], {'test_run'})

    def test_explicit_fixture_dependency_paths_and_usefixtures(self):
        sources = {'core.py': 'def target(): pass\n',
                   'tests/conftest.py': 'import pytest\nfrom core import target\n@pytest.fixture\ndef inner(): target()\n@pytest.fixture\ndef outer(inner): return 1\n@pytest.fixture(autouse=True)\ndef unrelated(): pass\n',
                   'tests/test_core.py': 'import pytest\ndef test_dep(outer): assert outer\n@pytest.mark.usefixtures("inner")\ndef test_marker(): assert True\ndef test_unrelated(): assert True\n'}
        names, evidence, _ = selection(sources, 'core.py::target')
        self.assertEqual(names, {'test_dep', 'test_marker'})
        self.assertEqual({e['distance'] for t in evidence for e in t['evidence']}, {2, 3})
        self.assertEqual(selection(sources, 'core.py::target', max_depth=2)[0], {'test_marker'})

    def test_getopt_usage_exit_prevents_help_false_positive(self):
        sources = {
            'core.py': 'import sys\nfrom getopt import getopt\ndef target(): pass\ndef usage(): sys.exit(0)\ndef Run(argv):\n    opts, args = getopt(argv, "h", ["help"])\n    for opt, val in opts:\n        if opt in ("-h", "--help"):\n            usage()\n    target()\n',
            'tests/test_core.py': 'from core import Run\ndef test_help(): Run(["--help"])\ndef test_run(): Run([])\n',
        }
        self.assertEqual(selection(sources, 'core.py::target')[0], {'test_run'})

    def test_property_read_is_a_getter_invocation(self):
        sources = {'core.py': 'class A:\n    @property\n    def value(self): return 1\n',
                   'tests/test_core.py': 'from core import A\ndef test_value(): assert A().value == 1\n'}
        names, evidence, _ = selection(sources, 'core.py::A.value')
        self.assertEqual(names, {'test_value'})
        self.assertEqual(evidence[0]['evidence'][0]['path'][0]['kind'], 'property_read')

    def test_ambiguous_and_unknown_receivers_are_excluded(self):
        sources = {'core.py': 'class A:\n    def work(self): pass\nclass B:\n    def work(self): pass\n',
                   'tests/test_core.py': 'from core import A, B\ndef test_unknown(x): x.work()\ndef test_ambiguous(flag):\n    obj = A() if flag else B()\n    obj.work()\n'}
        self.assertFalse(selection(sources, 'core.py::A.work')[0])

    def test_original_unrelated_tests_cannot_crowd_out_matches(self):
        sources = {'core.py': 'def target(): pass\n',
                   'tests/test_core.py': 'from core import target\ndef test_match(): target()\ndef test_unrelated(): assert True\n'}
        index = RelaxedIndex(sources)
        original = [index.tests['tests/test_core.py::test_unrelated']]
        changes = [{'id': 'core.py::target', 'base_targets': [{'id': 'core.py::target', 'reason': 'existing_symbol', 'confidence': 1.0}]}]
        tests, _, audit = retrieve_relaxed(index, changes, original=original, top_k=1)
        self.assertEqual([t['name'] for t in tests], ['test_match'])
        self.assertEqual(audit['original_tests_without_relation'][0]['name'], 'test_unrelated')

    def test_new_gold_code_only_ranks_existing_candidates(self):
        sources = {'core.py': 'def target(arg): pass\n',
                   'tests/test_core.py': 'from core import target\ndef test_other(): target("--other")\ndef test_relevant(): target("--desired")\n'}
        gold = [{'file': 'tests/test_new.py', 'name': 'test_new', 'code_content': 'def test_new(): target("--desired")'}]
        names, _, _ = selection(sources, 'core.py::target', gold=gold, top_k=1)
        self.assertEqual(names, {'test_relevant'})

    def test_framework_dispatch_is_conditional_and_optional(self):
        sources = {
            'src/_pytest/pytester.py': 'import pytest\nclass Pytester:\n    def runpytest_inprocess(self, *args): pass\n    def makepyfile(self, text): pass\n@pytest.fixture\ndef pytester(): return Pytester()\n',
            'src/_pytest/runner.py': 'def pytest_runtest_call(item): item.runtest()\n',
            'src/_pytest/unittest.py': 'class TestCaseFunction:\n    def runtest(self): pass\n',
            'testing/conftest.py': 'pytest_plugins = ["_pytest.pytester"]\n',
            'testing/test_core.py': 'def test_unittest(pytester):\n    pytester.makepyfile("import unittest\\nclass Case(unittest.TestCase): pass")\n    pytester.runpytest_inprocess("--pdb")\ndef test_ordinary(pytester):\n    pytester.makepyfile("def test_a(): pass")\n    pytester.runpytest_inprocess()\n',
        }
        target = 'src/_pytest/unittest.py::TestCaseFunction.runtest'
        names, evidence, _ = selection(sources, target)
        self.assertEqual(names, {'test_unittest'})
        self.assertEqual(evidence[0]['evidence'][0]['distance'], 3)
        self.assertFalse(selection(sources, target, framework_models=False)[0])


if __name__ == '__main__':
    unittest.main()
