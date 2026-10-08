import ast
import json
from pathlib import Path
import tempfile
import unittest

from scripts.code_retrieval.extract_oracle import OUTPUT_FILES, store_instance
from scripts.retrieval_formats import CODE_FIELDS, TEST_FIELDS, split_code_documents, test_retrieval
from tests.test_code_oracle import serialize


class RetrievalFormatTests(unittest.TestCase):
    def test_code_export_preserves_class_context_and_moves_annotations_to_manifest(self):
        before = "class Engine:\n    def run(self):\n        return 'café λ'\n"
        values = serialize(before, before.replace("café λ", "fixed"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store_instance(root, "example", values)
            code = json.loads((root / OUTPUT_FILES[0]).read_text(encoding="utf-8"))["example"]
            manifest = json.loads((root / OUTPUT_FILES[2]).read_text(encoding="utf-8"))["example"]
            self.assertEqual(set(code), {"core.py::Engine", "core.py::Engine.run"})
            for sid, doc in code.items():
                self.assertEqual(set(doc), set(CODE_FIELDS))
                self.assertEqual(doc["code_content"], values[0][sid]["code_content"])
            metadata = manifest["documents"]["base"]
            self.assertEqual(metadata["core.py::Engine.run"]["class_context_ids"], ["core.py::Engine"])
            self.assertEqual(metadata["core.py::Engine"]["content_kind"], "full_class")
            self.assertEqual(metadata["core.py::Engine.run"]["revision"], "base")
            # The unchanged formatter consumes the original code schema.
            formatter = self.formatters()["get_retrieval_docs"]
            rendered = formatter("example", root / OUTPUT_FILES[0])
            self.assertIn("class Engine:", rendered)
            self.assertIn("Engine.run", rendered)
            self.assertIn("café λ", rendered)
            self.assertNotIn("fixed", rendered)

    def test_subset_rerun_migrates_existing_documents_without_losing_metadata(self):
        values = serialize("def work():\n    return 1\n", "def work():\n    return 2\n")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename, value in zip(OUTPUT_FILES, values):
                (root / filename).write_text(json.dumps({"legacy": value}), encoding="utf-8")
            store_instance(root, "new", values)
            snapshots = [(root / filename).read_bytes() for filename in OUTPUT_FILES]
            code = json.loads(snapshots[0])["legacy"]
            manifest = json.loads(snapshots[2])["legacy"]
            self.assertEqual(set(code["core.py::work"]), set(CODE_FIELDS))
            self.assertEqual(manifest["documents"]["base"]["core.py::work"]["selection_evidence"],
                             values[0]["core.py::work"]["selection_evidence"])
            # Running a second time with clean files retains all moved metadata.
            loaded = tuple(json.loads(data)["new"] for data in snapshots)
            store_instance(root, "new", loaded)
            self.assertEqual(snapshots, [(root / filename).read_bytes() for filename in OUTPUT_FILES])

    def test_test_export_matches_original_list_schema_and_unicode_loader(self):
        test = {"name": "test_behavior", "file": "tests/test_core.py",
                "code_content": "def test_behavior():\n    assert 'café λ'\n",
                "evidence": ["changed_symbol"], "revision": "base"}
        exported = test_retrieval({"example": [test], "empty": []})
        self.assertEqual(set(exported["example"][0]), set(TEST_FIELDS))
        self.assertEqual(exported["example"][0]["code_content"], test["code_content"])
        self.assertEqual(exported["empty"], [])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "related_tests.json"
            path.write_text(json.dumps(exported, ensure_ascii=False), encoding="utf-8")
            formatter = self.formatters()["get_related_test_list"]
            self.assertIn("café λ", formatter("example", path)[0])
            self.assertEqual(formatter("empty", path), [])

    def test_manifest_cannot_be_exported_as_test_retrieval(self):
        with self.assertRaises(ValueError):
            test_retrieval({"example": {"selected_tests": []}})
        with self.assertRaises(ValueError):
            test_retrieval({"example": [{"name": "test_a", "file": "test_a.py"}]})
        with self.assertRaises(ValueError):
            split_code_documents({"work": {"code_content": "def work(): pass"}})

    def test_empty_code_and_original_null_matches_remain_valid(self):
        self.assertEqual(split_code_documents({}), ({}, {}))
        self.assertEqual(split_code_documents({"missing": None}), ({"missing": None}, {}))

    @staticmethod
    def formatters():
        path = Path(__file__).resolve().parents[1] / "scripts/generator/make_prompt_util.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"get_retrieval_docs", "get_related_test_list"}]
        namespace = {"json": json}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
        return namespace
