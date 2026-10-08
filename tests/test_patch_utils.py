from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.utils.git_utils import git_apply
from scripts.utils.patch_utils import prepare_reference_patch


class ReferencePatchTests(unittest.TestCase):
    def test_git_and_unified_headers_are_accepted(self):
        body = '--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n-buggy\n+fixed\n'
        for patch in (body, 'diff --git a/code.py b/code.py\n' + body):
            with self.subTest(patch=patch):
                self.assertEqual(prepare_reference_patch(patch, 'bug-1'), patch)

    def test_recount_and_final_newline_apply_only_the_provided_fix(self):
        patch = '--- a/code.py\n+++ b/code.py\n@@ -1,4 +1,4 @@\n-buggy\n+fixed'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            target = root / 'code.py'
            target.write_text('buggy\n', encoding='utf-8')
            untouched = root / 'other.py'
            untouched.write_text('unchanged\n', encoding='utf-8')
            git_apply(str(root), patch)
            self.assertEqual(target.read_text(), 'fixed\n')
            self.assertEqual(untouched.read_text(), 'unchanged\n')
            self.assertTrue((root / 'swe.patch').read_bytes().endswith(b'\n'))

    def test_missing_or_malformed_patch_reports_instance(self):
        for patch in (None, '', '   ', 'not a patch', 'diff --git a/code.py b/code.py\n',
                      '--- a/code.py\n+++ b/code.py\n@@ nonsense @@\n-invalid\n+fixed\n'):
            with self.subTest(patch=patch):
                with self.assertRaisesRegex(ValueError, 'bug-123:'):
                    prepare_reference_patch(patch, 'bug-123')

    def test_parseable_fix_that_does_not_apply_still_fails(self):
        patch = '--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n-buggy\n+fixed\n'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            target = root / 'code.py'
            target.write_text('different\n', encoding='utf-8')
            with self.assertRaises(AssertionError):
                git_apply(str(root), patch)
            self.assertEqual(target.read_text(), 'different\n')


if __name__ == '__main__':
    unittest.main()
