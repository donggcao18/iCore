import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.test_retrieval.augment_oracle import augment_instance, retrieve
from scripts.test_retrieval.static_dependencies import DependencyIndex


def select(sources, targets, **kwargs):
    index = DependencyIndex(sources)
    changes = [{"id": target, "base_targets": [{"id": target, "reason": "existing_symbol", "confidence": 1.0}]}
               for target in targets]
    audit = {}
    tests, evidence = retrieve(index, changes, audit=audit, **kwargs)
    return {item["name"] for item in tests}, evidence, audit


class StrictOracleTests(unittest.TestCase):
    def test_exact_calls_resolve_aliases_and_distinguish_same_named_methods(self):
        sources = {
            "pkg/core.py": "class A:\n    def run(self): pass\nclass B:\n    def run(self): pass\n",
            "pkg/__init__.py": "from .core import A as PublicA\n",
            "tests/test_core.py": (
                "from pkg import PublicA as Engine\nfrom pkg.core import B\n"
                "def test_exact():\n    engine = Engine()\n    engine.run()\n"
                "def test_other():\n    B().run()\n"
                "def helper():\n    Engine().run()\n"
                "def test_indirect():\n    helper()\n"),
        }
        names, evidence, _ = select(sources, ["pkg/core.py::A.run"])
        self.assertEqual(names, {"test_exact"})
        site = evidence[0]["evidence"][0]
        self.assertEqual(site["distance"], 1)
        self.assertEqual(site["location"]["line"], 5)
        self.assertEqual(site["location"]["expression"], "engine.run()")

    def test_help_wrapper_does_not_count_as_direct_use(self):
        sources = {
            "core.py": "def changed(): pass\ndef Run(argv):\n    if '--help' in argv: raise SystemExit\n    changed()\n",
            "tests/test_help.py": "from core import Run, changed\ndef test_help():\n    Run(['--help'])\ndef test_direct():\n    changed()\n",
        }
        self.assertEqual(select(sources, ["core.py::changed"])[0], {"test_direct"})

    def test_fixture_and_lifecycle_execution_are_excluded_but_direct_method_calls_are_kept(self):
        sources = {
            "core.py": "def initialize(): pass\nclass Engine:\n    def run(self): pass\n",
            "tests/conftest.py": "import pytest\nfrom core import Engine, initialize\n@pytest.fixture(autouse=True)\ndef setup(): initialize()\n@pytest.fixture\ndef engine(): return Engine()\n",
            "tests/test_core.py": "from core import initialize\nclass TestCore:\n    def setup_method(self): initialize()\n    def test_setup_only(self): assert True\ndef test_fixture_method(engine): engine.run()\n",
        }
        self.assertEqual(select(sources, ["core.py::initialize"])[0], set())
        self.assertEqual(select(sources, ["core.py::Engine.run"])[0], {"test_fixture_method"})

    def test_same_file_and_bare_function_references_are_rejected(self):
        sources = {
            "core.py": "def changed(): pass\ndef formatter(): pass\n",
            "tests/test_core.py": "from core import changed, formatter\ndef test_formatter(): formatter()\ndef test_reference():\n    callback = changed\n    assert callback\n",
        }
        self.assertEqual(select(sources, ["core.py::changed"])[0], set())
        _, _, audit = select(sources, ["core.py::<module>"])
        self.assertEqual(audit["rejected_targets"][0]["reason"], "module_is_not_an_exact_object")

    def test_variable_reads_are_exact_and_do_not_require_value_expansion(self):
        sources = {
            "core.py": "LIMIT = 2\nclass Settings:\n    SIZE = 3\n",
            "tests/test_values.py": "from core import LIMIT as L, Settings\ndef test_limit(): assert L == 2\ndef test_size(): assert Settings.SIZE == 3\n",
        }
        self.assertEqual(select(sources, ["core.py::LIMIT"])[0], {"test_limit"})
        names, evidence, _ = select(sources, ["core.py::Settings.SIZE"])
        self.assertEqual(names, {"test_size"})
        self.assertEqual(evidence[0]["evidence"][0]["path"][0]["kind"], "read")

    def test_class_construction_and_constructor_calls_are_direct(self):
        sources = {
            "core.py": "class A:\n    def __new__(cls): return super().__new__(cls)\n    def __init__(self): pass\n",
            "tests/test_core.py": "from core import A\ndef test_construct(): A()\ndef test_reference(): assert A\n",
        }
        for target in ("core.py::A", "core.py::A.__init__", "core.py::A.__new__"):
            with self.subTest(target=target):
                self.assertEqual(select(sources, [target])[0], {"test_construct"})

    def test_callable_instance_is_not_mistaken_for_constructor(self):
        sources = {
            "core.py": "class A:\n    def __init__(self): pass\n    def __call__(self): pass\n",
            "tests/test_core.py": "import pytest\nfrom core import A\n@pytest.fixture\ndef obj(): return A()\ndef test_call(obj): obj()\n",
        }
        self.assertEqual(select(sources, ["core.py::A.__init__"])[0], set())
        self.assertEqual(select(sources, ["core.py::A.__call__"])[0], {"test_call"})

    def test_inherited_direct_method_resolves_to_original_implementation(self):
        sources = {
            "core.py": "class Base:\n    def work(self): pass\nclass Derived(Base): pass\nclass Other:\n    def work(self): pass\n",
            "tests/test_core.py": "from core import Derived, Other\ndef test_inherited(): Derived().work()\ndef test_other(): Other().work()\n",
        }
        self.assertEqual(select(sources, ["core.py::Base.work"])[0], {"test_inherited"})

    def test_ambiguous_and_unknown_receivers_are_rejected(self):
        sources = {
            "core.py": "class A:\n    def work(self): pass\nclass B:\n    def work(self): pass\n",
            "tests/test_core.py": "from core import A, B\ndef test_ambiguous(flag):\n    obj = A() if flag else B()\n    obj.work()\ndef test_unknown(obj): obj.work()\n",
        }
        names, _, audit = select(sources, ["core.py::A.work"])
        self.assertFalse(names)
        self.assertEqual(audit["rejected_uses"][0]["reason"], "ambiguous_resolution")

    def test_external_alternative_does_not_make_an_internal_call_unambiguous(self):
        sources = {
            "core.py": "def changed(): pass\n",
            "tests/test_core.py": "from core import changed\nfrom external import other\ndef test_ambiguous(flag):\n    callback = changed if flag else other\n    callback()\n",
        }
        self.assertEqual(select(sources, ["core.py::changed"])[0], set())

    def test_known_receiver_mixed_with_unknown_possibility_is_rejected(self):
        sources = {
            "core.py": "class A:\n    def work(self): pass\n",
            "tests/test_core.py": (
                "from core import A\nfrom external import factory\n"
                "def maybe(flag):\n    if flag: return A()\n    return None\n"
                "def test_conditional(flag):\n    obj = A() if flag else factory()\n    obj.work()\n"
                "def test_reassigned(unknown):\n    obj = A()\n    obj = unknown\n    obj.work()\n"
                "def test_nullable_factory(flag):\n    maybe(flag).work()\n"),
        }
        names, _, audit = select(sources, ["core.py::A.work"])
        self.assertFalse(names)
        self.assertEqual(len(audit['rejected_uses']), 3)

    def test_runtime_strings_are_not_receiver_types_but_annotations_are(self):
        sources = {
            "core.py": "class Engine:\n    def __init__(self): pass\n    def work(self): pass\n    def __call__(self): pass\ndef class_name(): return 'Engine'\n",
            "tests/test_core.py": (
                "from core import Engine, class_name\n"
                "def test_string():\n    obj = 'Engine'\n    obj.work()\n"
                "def test_string_factory(): class_name().work()\n"
                "def test_typed(engine: 'Engine'): engine.work()\n"
                "def test_typed_callable(engine: 'Engine'): engine()\n"),
        }
        self.assertEqual(select(sources, ['core.py::Engine.work'])[0], {'test_typed'})
        self.assertFalse(select(sources, ['core.py::Engine.__init__'])[0])
        self.assertEqual(select(sources, ['core.py::Engine.__call__'])[0], {'test_typed_callable'})

    def test_added_object_substitutions_do_not_qualify(self):
        index = DependencyIndex({"core.py": "def caller(): pass\nclass Owner: pass\n",
                                 "tests/test_core.py": "from core import caller, Owner\ndef test_caller(): caller()\ndef test_owner(): Owner()\n"})
        changes = [{"id": "core.py::new_helper", "base_targets": [
            {"id": "core.py::caller", "reason": "existing_caller", "confidence": .8},
            {"id": "core.py::Owner", "reason": "class_fallback", "confidence": .35}]}]
        audit = {}
        self.assertEqual(retrieve(index, changes, audit=audit)[0], [])
        self.assertEqual(len(audit["rejected_targets"]), 2)

    def test_decorators_nested_definitions_and_lambda_bodies_do_not_qualify(self):
        sources = {
            "core.py": "def changed(): return lambda f: f\n",
            "tests/test_core.py": (
                "from core import changed\n@changed()\ndef test_decorated(): pass\n"
                "def test_nested():\n    def inner(): changed()\n    inner()\n"
                "def test_lambda():\n    callback = lambda: changed()\n    callback()\n"
                "def test_string():\n    source = 'changed()'\n"),
        }
        self.assertFalse(select(sources, ["core.py::changed"])[0])

    def test_local_annotations_and_deferred_generators_do_not_qualify(self):
        sources = {
            "core.py": "LIMIT = 2\ndef changed(): return int\n",
            "tests/test_core.py": ("from core import LIMIT, changed\n"
                                   "def test_annotation():\n    x: changed()\n    y: LIMIT\n"
                                   "def test_generator():\n    unused = (changed() for _ in [1])\n"),
        }
        self.assertFalse(select(sources, ['core.py::changed', 'core.py::LIMIT'])[0])

    def test_top_k_is_a_ceiling_without_padding(self):
        sources = {"core.py": "def changed(): pass\ndef other(): pass\n",
                   "tests/test_core.py": "from core import changed, other\ndef test_exact(): changed()\ndef test_other(): other()\n"}
        names, _, audit = select(sources, ["core.py::changed"], top_k=10)
        self.assertEqual(names, {"test_exact"})
        self.assertEqual(audit["matching_tests_before_cap"], 1)

    def test_original_oracle_is_preserved_and_auxiliary_stays_outside_primary(self):
        sources = {
            "core.py": "def work():\n    return 1\n",
            "tests/test_core.py": ("from core import work\ndef helper(): work()\n"
                                   "def test_direct(): work()\ndef test_indirect(): helper()\n"
                                   "def test_gold(): assert True\n"),
        }
        original = DependencyIndex(sources).tests["tests/test_core.py::test_gold"]
        row = {"base_commit": "abc", "test_patch": "", "patch":
               "diff --git a/core.py b/core.py\n--- a/core.py\n+++ b/core.py\n@@ -1,2 +1,2 @@\n def work():\n-    return 1\n+    return 2\n"}
        with patch('scripts.test_retrieval.augment_oracle.base_sources', return_value=(sources, [], {})), \
             patch('scripts.test_retrieval.augment_oracle.apply_production_patch', return_value={"core.py": "def work():\n    return 2\n"}), \
             patch('scripts.test_retrieval.augment_oracle.extract_instance', return_value=([], [original], {})):
            retrieved, combined, manifest = augment_instance(row, Path('.'), include_auxiliary=True)
        self.assertEqual([item['name'] for item in retrieved], ['test_direct'])
        self.assertEqual([item['name'] for item in combined], ['test_gold', 'test_direct'])
        self.assertEqual(manifest['selected_tests'][0]['sources'], ['test_patch_base'])
        self.assertEqual(manifest['auxiliary_candidates'][0]['name'], 'test_indirect')
        self.assertFalse(manifest['auxiliary_candidates'][0]['primary_eligible'])
        self.assertEqual(manifest['selection_policy'], 'strict_direct_existing_object_v1')


if __name__ == '__main__':
    unittest.main()
