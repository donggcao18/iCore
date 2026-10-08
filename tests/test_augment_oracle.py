import ast
import difflib
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.test_retrieval.augment_oracle import (
    ProductionChange, augment_instance, changed_symbols, collection_settings, merge_tests,
    production_changes, retrieve, safe_path, _save,
)
from scripts.test_retrieval.static_dependencies import DependencyIndex


def diff(path, before, after):
    return f"diff --git a/{path} b/{path}\n" + "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))


def selection(sources, target, **kwargs):
    index = DependencyIndex(sources).build()
    changed = [{"id": target, "base_targets": [{"id": target, "reason": "existing_symbol", "confidence": 1.0}]}]
    tests, evidence = retrieve(index, changed, fallback=False, **kwargs)
    return {item["name"] for item in tests}, evidence, index


class DependencyTests(unittest.TestCase):
    def test_custom_collection_patterns_and_ignored_example_directories(self):
        sources = {
            "core.py": "def target():\n    pass\n",
            "testing/python/fixtures.py": "from core import target\ndef test_custom():\n    target()\n",
            "testing/example_scripts/test_example.py": "from core import target\ndef test_example():\n    target()\n",
        }
        settings, errors = collection_settings({"tox.ini": "[pytest]\npython_files = test_*.py *_test.py testing/*/*.py\nnorecursedirs = testing/example_scripts\n"})
        index = DependencyIndex(sources, test_patterns=settings["test_patterns"],
                                ignore_patterns=settings["ignore_patterns"]).build()
        self.assertFalse(errors)
        self.assertEqual(set(index.tests), {"testing/python/fixtures.py::test_custom"})
        self.assertEqual(set(index.reverse_paths("core.py::target", 8)), set(index.tests))

    def test_pyproject_collection_configuration_and_known_oracle_files(self):
        settings, errors = collection_settings({"pyproject.toml": '[tool.pytest.ini_options]\npython_files = ["spec_*.py"]\n'})
        self.assertEqual(settings["test_patterns"], ["spec_*.py"])
        self.assertFalse(errors)
        index = DependencyIndex({"suite.py": "def test_known():\n    pass\n"},
                                test_patterns=settings["test_patterns"], extra_test_files={"suite.py"}).build()
        self.assertIn("suite.py::test_known", index.tests)

    def test_aliases_relative_imports_helpers_and_same_named_methods(self):
        sources = {
            "src/pkg/core.py": "class Engine:\n    def run(self):\n        return 1\n",
            "src/pkg/__init__.py": "from .core import Engine as PublicEngine\n",
            "tests/test_api.py": (
                "from pkg import PublicEngine as E\n"
                "class Other:\n    def run(self):\n        return 2\n"
                "def helper():\n    engine = E()\n    return engine.run()\n"
                "def test_relevant():\n    assert helper() == 1\n"
                "def test_unrelated():\n    assert Other().run() == 2\n"
            ),
        }
        names, evidence, _ = selection(sources, "src/pkg/core.py::Engine.run")
        self.assertEqual(names, {"test_relevant"})
        self.assertGreater(evidence[0]["evidence"][0]["distance"], 1)

    def test_contextual_inherited_setup_without_cross_class_contamination(self):
        sources = {
            "core.py": "class A:\n    def open(self):\n        pass\nclass B:\n    def open(self):\n        pass\n",
            "support.py": (
                "class Base:\n    CHECKER_CLASS = None\n"
                "    def setup_method(self):\n        self.checker = self.CHECKER_CLASS()\n        self.checker.open()\n"
            ),
            "tests/test_checkers.py": (
                "from core import A, B\nfrom support import Base\n"
                "class TestA(Base):\n    CHECKER_CLASS = A\n    def test_a(self):\n        assert True\n"
                "class TestB(Base):\n    CHECKER_CLASS = B\n    def test_b(self):\n        assert True\n"
            ),
        }
        names, evidence, _ = selection(sources, "core.py::A.open")
        self.assertEqual(names, {"TestA.test_a"})
        self.assertIn("lifecycle", {edge["kind"] for edge in evidence[0]["evidence"][0]["path"]})

    def test_fixture_dependencies_autouse_overrides_and_receiver_return_types(self):
        sources = {
            "core.py": "class Engine:\n    def run(self):\n        return 1\ndef initialize():\n    return 1\n",
            "tests/conftest.py": (
                "import pytest\nfrom core import Engine, initialize\n"
                "@pytest.fixture\ndef engine():\n    return Engine()\n"
                "@pytest.fixture(autouse=True)\ndef bootstrap():\n    initialize()\n"
            ),
            "tests/test_api.py": "def test_engine(engine):\n    assert engine.run() == 1\n",
            "tests/isolated/conftest.py": "import pytest\n@pytest.fixture(autouse=True)\ndef bootstrap():\n    pass\n",
            "tests/isolated/test_api.py": "def test_isolated():\n    assert True\n",
        }
        names, _, _ = selection(sources, "core.py::Engine.run")
        self.assertEqual(names, {"test_engine"})
        names, _, _ = selection(sources, "core.py::initialize")
        self.assertEqual(names, {"test_engine"})

    def test_nested_fixtures_and_usefixtures(self):
        sources = {
            "core.py": "def target():\n    pass\n",
            "tests/conftest.py": "import pytest\nfrom core import target\n@pytest.fixture\ndef first():\n    target()\n@pytest.fixture\ndef second(first):\n    pass\n",
            "tests/test_api.py": "import pytest\n@pytest.mark.usefixtures('second')\ndef test_it():\n    pass\n",
        }
        names, _, _ = selection(sources, "core.py::target")
        self.assertEqual(names, {"test_it"})

    def test_direct_parametrization_does_not_invoke_same_named_fixture(self):
        sources = {
            "core.py": "def target():\n    pass\n",
            "tests/conftest.py": "import pytest\nfrom core import target\n@pytest.fixture\ndef value():\n    target()\n",
            "tests/test_api.py": "import pytest\n@pytest.mark.parametrize('value', [1])\ndef test_direct(value):\n    assert value\n@pytest.mark.parametrize('value', [1], indirect=True)\ndef test_indirect(value):\n    assert value\n",
        }
        names, _, _ = selection(sources, "core.py::target")
        self.assertEqual(names, {"test_indirect"})

    def test_class_attribute_and_module_constant_reads(self):
        sources = {
            "core.py": "LIMIT = 2\nclass Settings:\n    SIZE = 3\n",
            "tests/test_values.py": "from core import LIMIT, Settings\ndef test_limit():\n    assert LIMIT == 2\ndef test_size():\n    assert Settings.SIZE == 3\n",
        }
        self.assertEqual(selection(sources, "core.py::LIMIT")[0], {"test_limit"})
        self.assertEqual(selection(sources, "core.py::Settings.SIZE")[0], {"test_size"})

    def test_ast_visitor_dispatch_is_marked_approximate(self):
        sources = {
            "core.py": "import ast\nclass Visitor(ast.NodeVisitor):\n    def visit_Call(self, node):\n        pass\ndef rewrite(node):\n    Visitor().visit(node)\n",
            "tests/test_visit.py": "from core import rewrite\ndef test_visit():\n    rewrite(None)\n",
        }
        names, evidence, _ = selection(sources, "core.py::Visitor.visit_Call")
        self.assertEqual(names, {"test_visit"})
        self.assertEqual(evidence[0]["evidence"][0]["confidence"], 0.65)

    def test_cycles_depth_limit_and_unresolved_receiver(self):
        sources = {
            "core.py": "def a():\n    b()\ndef b():\n    a()\n",
            "tests/test_cycles.py": "from core import a\ndef helper():\n    a()\ndef test_cycle():\n    helper()\ndef test_unknown(obj):\n    obj.a()\n",
        }
        self.assertEqual(selection(sources, "core.py::a", max_depth=1)[0], set())
        names, _, index = selection(sources, "core.py::a", max_depth=4)
        self.assertEqual(names, {"test_cycle"})
        self.assertTrue(index.report()["unresolved_attribute_calls"])

    def test_nested_function_body_requires_invocation(self):
        sources = {
            "core.py": "def target():\n    pass\n",
            "tests/test_nested.py": "from core import target\ndef test_unused():\n    def inner():\n        target()\n    assert True\ndef test_used():\n    def inner():\n        target()\n    inner()\n",
        }
        self.assertEqual(selection(sources, "core.py::target")[0], {"test_used"})

    def test_bad_source_is_reported_without_aborting_other_files(self):
        index = DependencyIndex({"broken.py": "def ?", "good.py": "def ok():\n    pass\n"}).build()
        self.assertEqual(len(index.report()["parse_errors"]), 1)
        self.assertIn("good.py::ok", index.symbols)


class ChangedSymbolsTests(unittest.TestCase):
    def test_plain_unified_diff_supports_multiple_files_additions_and_deletions(self):
        patch_text = ("--- a/core.py\n+++ b/core.py\n@@ -1,2 +1,2 @@\n def work():\n-    return 1\n+    return 2\n"
                      "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+def added():\n+    pass\n"
                      "--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-def removed():\n-    pass\n")
        changes = production_changes(patch_text)
        self.assertEqual([c.path for c in changes], ["core.py", "new.py", "old.py"])
        self.assertEqual(changes[0].old_lines, {2})
        self.assertEqual(changes[0].new_lines, {2})
        self.assertTrue(changes[1].new_file)
        self.assertTrue(changes[2].deleted_file)

    def test_header_like_changed_lines_are_not_mistaken_for_file_headers(self):
        patch_text = "--- a/core.txt\n+++ b/core.txt\n@@ -1,2 +1,2 @@\n--- a/example\n+++ b/example\n context\n"
        changes = production_changes(patch_text)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].old_lines, {1})
        self.assertEqual(changes[0].new_lines, {1})

    def test_plain_unified_diff_rejects_unsafe_and_mismatched_paths(self):
        for text in ("--- a/../outside.py\n+++ b/core.py\n",
                     "diff --git a/core.py b/core.py\n--- a/other.py\n+++ b/core.py\n"):
            with self.assertRaises(ValueError):
                production_changes(text)

    def test_atomic_save_retries_transient_windows_file_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "output.json"
            original_replace = Path.replace
            attempts = []
            def replace(path, target):
                attempts.append(target)
                if len(attempts) == 1:
                    raise PermissionError("file is temporarily open")
                return original_replace(path, target)
            with patch.object(Path, "replace", replace), patch("scripts.test_retrieval.augment_oracle.time.sleep"):
                _save(destination, {"instance": ["test"]})
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), {"instance": ["test"]})
            self.assertEqual(len(attempts), 2)
            self.assertFalse(destination.with_suffix(".json.tmp").exists())

    def map_changes(self, before, after):
        changes = production_changes(diff("core.py", before, after))
        return changed_symbols(DependencyIndex({"core.py": before}).build(),
                               DependencyIndex({"core.py": after}).build(), changes)

    def test_insertion_only_maps_to_existing_function(self):
        before = "def work(x):\n    return x\n"
        after = "def work(x):\n    x = abs(x)\n    return x\n"
        result = self.map_changes(before, after)
        self.assertEqual(result[0]["name"], "work")
        self.assertEqual(result[0]["base_targets"][0]["id"], "core.py::work")

    def test_added_helper_maps_to_existing_caller(self):
        before = "def work(x):\n    return x\n"
        after = "def work(x):\n    return helper(x)\ndef helper(x):\n    return abs(x)\n"
        helper = next(item for item in self.map_changes(before, after) if item["name"] == "helper")
        self.assertEqual(helper["change"], "added")
        self.assertEqual(helper["base_targets"][0]["reason"], "existing_caller")

    def test_new_module_has_no_invented_base_target(self):
        changes = [ProductionChange("new.py", "new.py", new_lines={1, 2}, new_file=True)]
        result = changed_symbols(DependencyIndex({}).build(),
                                 DependencyIndex({"new.py": "def added():\n    pass\n"}).build(), changes)
        self.assertEqual(result[0]["base_targets"], [])
        self.assertIn("unresolved_reason", result[0])

    def test_deleted_function_and_rename_are_supported(self):
        self.assertEqual(self.map_changes("def gone():\n    pass\n", "")[0]["change"], "deleted")
        patch = "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n"
        changes = production_changes(patch)
        result = changed_symbols(DependencyIndex({"old.py": "VALUE = 1\n"}).build(),
                                 DependencyIndex({"new.py": "VALUE = 1\n"}).build(), changes)
        self.assertEqual(result[0]["base_targets"][0]["id"], "old.py::<module>")

    def test_patch_paths_cannot_escape_workspace(self):
        for path in ("../outside.py", "C:/outside.py", "/outside.py", "x/../../bad.py", "x\\..\\bad.py"):
            with self.assertRaises(ValueError):
                safe_path(path)

    def test_merging_preserves_original_priority_deduplicates_and_caps(self):
        a = {"file": "tests/test_a.py", "name": "test_a"}
        b = {"file": "tests/test_b.py", "name": "test_b"}
        self.assertEqual(merge_tests([a], [a, b], 10), [a, b])
        self.assertEqual(merge_tests([a], [b], 1), [a])


class SnapshotIntegrationTests(unittest.TestCase):
    def test_new_gold_test_retrieves_only_historical_tests_and_keeps_checkout_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", *args],
                                      cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            git("init")
            (root / "tests").mkdir()
            core = "def work(x):\n    return x\n"
            tests = "from core import work\ndef test_existing():\n    assert work(1) == 1\n"
            (root / "core.py").write_text(core, encoding="utf-8")
            (root / "tests/test_core.py").write_text(tests, encoding="utf-8")
            git("add", ".")
            git("-c", "user.name=Oracle Test", "-c", "user.email=oracle@example.invalid", "commit", "-m", "base")
            commit = git("rev-parse", "HEAD")
            # The live checkout deliberately differs from the historical snapshot.
            (root / "core.py").write_text("LIVE_CHECKOUT = True\n", encoding="utf-8")
            status = git("status", "--porcelain")
            row = {"instance_id": "example__repo-1", "base_commit": commit,
                   "patch": diff("core.py", core, "def work(x):\n    return abs(x)\n"),
                   "test_patch": diff("tests/test_core.py", tests, tests + "def test_gold():\n    assert work(-1) == 1\n"),
                   "FAIL_TO_PASS": json.dumps(["tests/test_core.py::test_gold"])}
            retrieved, combined, manifest = augment_instance(row, root, fallback=False)
            self.assertEqual([item["name"] for item in retrieved], ["test_existing"])
            self.assertEqual(retrieved, combined)
            self.assertEqual(manifest["counts"]["original_base"], 0)
            self.assertNotIn("test_gold", json.dumps(combined))
            self.assertEqual(git("status", "--porcelain"), status)
            for item in combined:
                ast.parse(item["code_content"])


if __name__ == "__main__":
    unittest.main()
