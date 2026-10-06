import ast
import difflib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.code_retrieval.extract_oracle import (
    OUTPUT_FILES, default_paths, extract_instance, serialize_code_oracle, store_instance,
)
from scripts.test_retrieval.augment_oracle import changed_symbols, production_changes
from scripts.test_retrieval.static_dependencies import DependencyIndex


def diff(path, before, after, *, new_file=False, deleted_file=False):
    header = f"diff --git a/{path} b/{path}\n"
    if new_file:
        header += "new file mode 100644\n"
    if deleted_file:
        header += "deleted file mode 100644\n"
    return header + "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="/dev/null" if new_file else f"a/{path}",
        tofile="/dev/null" if deleted_file else f"b/{path}",
    ))


def serialize(before_source, after_source):
    before = DependencyIndex({"core.py": before_source})
    after = DependencyIndex({"core.py": after_source}).build()
    symbols = changed_symbols(before, after, production_changes(diff("core.py", before_source, after_source)))
    return serialize_code_oracle(before, after, symbols, "example")


class CodeOracleTests(unittest.TestCase):
    def test_insertion_only_uses_buggy_source_with_generator_fields(self):
        before = "def work(x):\n    return x\n"
        after = "def work(x):\n    x = abs(x)\n    return x\n"
        base, patched, manifest = serialize(before, after)
        doc = base["core.py::work"]
        self.assertEqual(doc["code_content"], before.rstrip())
        self.assertEqual(patched["core.py::work"]["code_content"], after.rstrip())
        self.assertEqual(doc["node_type"], "top-level function")
        self.assertEqual((doc["code_start_line"], doc["code_end_line"]), (1, 2))
        self.assertEqual(manifest["counts"], {"changed_symbols": 1, "base_objects": 1, "patched_objects": 1})
        self.assertFalse(manifest["base_contains_developer_fix"])

    def test_class_method_decorator_parent_and_constant(self):
        before = "LIMIT = 1\nclass Engine:\n    @staticmethod\n    def run():\n        return LIMIT\n"
        after = before.replace("LIMIT = 1", "LIMIT = 2").replace("return LIMIT", "return LIMIT + 1")
        base, _, _ = serialize(before, after)
        self.assertEqual(set(base), {"core.py::LIMIT", "core.py::Engine.run"})
        method = base["core.py::Engine.run"]
        self.assertEqual(method["node_type"], "class_function")
        self.assertEqual(method["obj_name"], "run")
        self.assertEqual(method["parent"], "Engine")
        self.assertTrue(method["code_content"].startswith("    @staticmethod"))

    def test_added_helper_is_only_in_patched_and_base_caller_is_deduplicated(self):
        before = "def work(x):\n    return x\n"
        after = "def work(x):\n    return helper(x)\ndef helper(x):\n    return abs(x)\n"
        base, patched, manifest = serialize(before, after)
        self.assertEqual(set(base), {"core.py::work"})
        self.assertEqual(set(patched), {"core.py::work", "core.py::helper"})
        self.assertEqual(len(base["core.py::work"]["selection_evidence"]), 2)
        helper = next(item for item in manifest["changed_symbols"] if item["name"] == "helper")
        self.assertEqual(helper["selected_base"], ["core.py::work"])

    def test_new_module_does_not_invent_old_code(self):
        after = DependencyIndex({"added.py": "def added():\n    return 1\n"}).build()
        before = DependencyIndex({})
        changes = production_changes(diff("added.py", "", after.sources["added.py"], new_file=True))
        base, patched, _ = serialize_code_oracle(before, after, changed_symbols(before, after, changes), "example")
        self.assertFalse(base)
        self.assertIn("added.py::added", patched)

    def test_default_directories_parallel_test_oracle(self):
        self.assertEqual(default_paths("lite", "pylint-dev/pylint")[1].parts[-4:], ("code", "oracle", "lite", "pylint"))
        self.assertEqual(default_paths("swt-verified", "pylint-dev/pylint")[1].parts[-2:], ("swt-bench-verified", "pylint"))

    def test_store_preserves_other_instances_and_updates_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store_instance(root, "a", ({"old": {}}, {}, {"version": 1}))
            store_instance(root, "b", ({"b": {}}, {}, {}))
            store_instance(root, "a", ({"new": {}}, {}, {"version": 2}))
            output = json.loads((root / OUTPUT_FILES[0]).read_text(encoding="utf-8"))
            self.assertEqual(output, {"a": {"new": {}}, "b": {"b": {}}})
            self.assertFalse(list(root.glob("*.tmp")))


class CodeOracleIntegrationTests(unittest.TestCase):
    def test_git_snapshot_deletion_addition_and_non_python_hunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", *args], cwd=root,
                                      capture_output=True, check=True, text=True).stdout.strip()
            git("init")
            old = "def removed():\n    return 1\n"
            (root / "old.py").write_text(old, encoding="utf-8")
            (root / "notes.txt").write_text("old\n", encoding="utf-8")
            git("add", ".")
            git("-c", "user.name=Oracle Test", "-c", "user.email=oracle@example.invalid", "commit", "-m", "base")
            commit = git("rev-parse", "HEAD")
            (root / "old.py").write_text("LIVE_CHECKOUT = True\n", encoding="utf-8")
            status = git("status", "--porcelain")
            row = {"base_commit": commit, "patch": diff("old.py", old, "", deleted_file=True)
                   + diff("new.py", "", "def added():\n    return 2\n", new_file=True)
                   + diff("notes.txt", "old\n", "new\n")}
            base, patched, manifest = extract_instance(row, root)
            self.assertEqual(set(base), {"old.py::removed"})
            self.assertEqual(set(patched), {"new.py::added"})
            self.assertEqual(base["old.py::removed"]["code_content"], old.rstrip())
            self.assertEqual(manifest["non_python_files"], ["notes.txt"])
            self.assertEqual(git("status", "--porcelain"), status)

    def test_unparseable_changed_file_is_explicit_file_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", *args], cwd=root,
                                      capture_output=True, check=True, text=True).stdout.strip()
            git("init")
            old = "print 'old Python syntax'\n"
            (root / "legacy.py").write_text(old, encoding="utf-8")
            git("add", ".")
            git("-c", "user.name=Oracle Test", "-c", "user.email=oracle@example.invalid", "commit", "-m", "legacy")
            base, patched, manifest = extract_instance({"base_commit": git("rev-parse", "HEAD"),
                                                        "patch": diff("legacy.py", old, old.replace("old", "new"))}, root)
            self.assertEqual(base["legacy.py::<module>"]["code_content"], old)
            self.assertEqual(patched["legacy.py::<module>"]["revision"], "patched")
            self.assertTrue(manifest["base_parse_errors"])

    def test_generator_can_format_stored_code_without_live_checkout_reads(self):
        # Exercise the actual formatter without importing unrelated evaluation
        # dependencies; its only dependency for embedded code is json.
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "scripts/generator/make_prompt_util.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "get_retrieval_docs")
        namespace = {"json": json}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "make_prompt_util.py", "exec"), namespace)
        base, patched, manifest = serialize("class Engine:\n    def run(self):\n        return 1\n",
                                            "class Engine:\n    def run(self):\n        return 2\n")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            store_instance(output, "example", (base, patched, manifest))
            formatted = namespace["get_retrieval_docs"]("example", output / OUTPUT_FILES[0])
            self.assertIn("Engine.run", formatted)
            self.assertIn("return 1", formatted)
            self.assertNotIn("return 2", formatted)


if __name__ == "__main__":
    unittest.main()
