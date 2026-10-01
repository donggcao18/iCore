import ast
import difflib
import unittest
from unittest.mock import patch

from scripts.test_retrieval.extract_oracle import default_paths, extract_instance, parse_patch


def file_diff(path, before, after, *, new_file=False):
    header = f"diff --git a/{path} b/{path}\n"
    if new_file:
        header += "new file mode 100644\n"
    return header + "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="/dev/null" if new_file else f"a/{path}", tofile=f"b/{path}",
    ))


class OracleExtractionTests(unittest.TestCase):
    def test_changed_decorator_new_method_and_new_file(self):
        path = "tests/test_example.py"
        other = "tests/test_added.py"
        before = "class TestExample:\n    def test_existing(self):\n        assert True\n"
        after = (
            "class TestExample:\n"
            "    @pytest.mark.xfail\n"
            "    def test_existing(self):\n"
            "        assert True\n"
            "\n"
            "    def test_new(self):\n"
            "        assert True\n"
        )
        new_file = "def test_other():\n    assert True\n"
        diff = file_diff(path, before, after) + file_diff(other, "", new_file, new_file=True)
        row = {
            "instance_id": "pylint-dev__pylint-example",
            "base_commit": "example",
            "test_patch": diff,
            "FAIL_TO_PASS": '["tests/test_example.py::TestExample::test_new"]',
        }
        sources = ({path: before, other: None}, {path: after, other: new_file})
        with patch("scripts.test_retrieval.extract_oracle.patched_sources", return_value=sources):
            patched, base, manifest = extract_instance(row, None)

        self.assertEqual([item["name"] for item in patched], [
            "TestExample.test_new", "TestExample.test_existing", "test_other",
        ])
        self.assertEqual([item["name"] for item in base], ["TestExample.test_existing"])
        self.assertEqual([item["kind"] for item in manifest], ["new", "modified", "new"])
        self.assertIn("@pytest.mark.xfail", patched[1]["code_content"])
        for item in patched + base:
            ast.parse(item["code_content"])

    def test_label_identifies_unchanged_function_using_changed_fixture(self):
        path = "tests/test_fixture.py"
        before = "PARAMS = [1]\n\ndef test_parameterized():\n    assert PARAMS\n"
        after = "PARAMS = [2]\n\ndef test_parameterized():\n    assert PARAMS\n"
        row = {
            "instance_id": "pylint-dev__pylint-fixture",
            "base_commit": "example",
            "test_patch": file_diff(path, before, after),
            "FAIL_TO_PASS": '["tests/test_fixture.py::test_parameterized[value-2]"]',
        }
        sources = ({path: before}, {path: after})
        with patch("scripts.test_retrieval.extract_oracle.patched_sources", return_value=sources):
            patched, base, manifest = extract_instance(row, None)
        self.assertEqual(patched[0]["name"], "test_parameterized")
        self.assertEqual(base[0]["name"], "test_parameterized")
        self.assertEqual(manifest[0]["evidence"], ["fail_to_pass_label"])

    def test_patch_parser_counts_changed_lines_in_multiple_files(self):
        diff = file_diff("tests/a.py", "x = 1\n", "x = 2\n") + file_diff(
            "tests/b.py", "", "def test_new():\n    pass\n", new_file=True,
        )
        changes = parse_patch(diff)
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0].old_lines, {1})
        self.assertEqual(changes[0].new_lines, {1})
        self.assertTrue(changes[1].new_file)

    def test_dataset_output_directories_are_parallel(self):
        lite_csv, lite_output = default_paths("lite", "pylint-dev/pylint")
        verified_csv, verified_output = default_paths("swt-verified", "pylint-dev/pylint")
        self.assertEqual(lite_csv.name, "test.csv")
        self.assertEqual(verified_csv.name, "test.csv")
        self.assertEqual(lite_output.parts[-3:], ("oracle", "lite", "pylint"))
        self.assertEqual(verified_output.parts[-3:], ("oracle", "swt-bench-verified", "pylint"))


if __name__ == "__main__":
    unittest.main()
