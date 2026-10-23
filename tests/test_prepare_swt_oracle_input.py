import csv
import json
from pathlib import Path
import tempfile
import unittest

from scripts import prepare_swt_oracle_input as prepare


class PrepareSwtOracleInputTests(unittest.TestCase):
    def setUp(self):
        self.full = 'diff --git a/code.py b/code.py\n--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n-buggy\n+fixed\n'
        self.reference = dict(repo='scikit-learn/scikit-learn', instance_id='sklearn-1',
                              base_commit='abc', version='0.20', environment_setup_commit='def',
                              patch=self.full, test_patch='UPSTREAM TEST PATCH')
        self.row = dict(self.reference, source_dataset=prepare.DATASET,
                        patch='\n'.join(self.full.splitlines()[1:-1]),
                        test_patch='diff --git a/tests/test_one.py b/tests/test_one.py\n',
                        problem_statement='original issue')

    def test_restores_complete_fix_and_preserves_swt_tests_and_selection(self):
        rows, records = prepare.restore_rows([dict(self.row, repo='other/repo'), self.row],
                                             [self.reference], self.row['repo'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['patch'], self.full)
        self.assertEqual(rows[0]['test_patch'], self.row['test_patch'])
        self.assertEqual(rows[0]['problem_statement'], 'original issue')
        self.assertEqual(records[0]['repair'], 'restored_first_and_last_upstream_lines')
        self.assertNotEqual(self.row['patch'], self.full)

    def test_accepts_already_complete_fix(self):
        rows, records = prepare.restore_rows([dict(self.row, patch=self.full)],
                                             [self.reference], self.row['repo'])
        self.assertEqual(records[0]['repair'], 'already_complete')
        self.assertEqual(rows[0]['patch'], self.full)

    def test_rejects_wrong_reference_metadata_or_unrelated_patch(self):
        for field in ('repo', 'base_commit', 'version', 'environment_setup_commit'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                prepare.restore_rows([self.row], [dict(self.reference, **{field: 'different'})], self.row['repo'])
        with self.assertRaisesRegex(ValueError, 'exact truncated interior'):
            prepare.restore_rows([dict(self.row, patch=self.row['patch'].replace('buggy', 'other'))],
                                 [self.reference], self.row['repo'])

    def test_rejects_missing_duplicate_or_unnormalized_instances(self):
        with self.assertRaisesRegex(ValueError, 'missing from'):
            prepare.restore_rows([self.row], [], self.row['repo'])
        with self.assertRaisesRegex(ValueError, 'unique'):
            prepare.restore_rows([self.row, self.row], [self.reference], self.row['repo'])
        with self.assertRaisesRegex(ValueError, 'Duplicate reference'):
            prepare.restore_rows([self.row], [self.reference, self.reference], self.row['repo'])
        with self.assertRaisesRegex(ValueError, 'normalized'):
            prepare.restore_rows([dict(self.row, source_dataset='wrong')], [self.reference], self.row['repo'])

    def test_cli_writes_repo_csv_and_manifest_from_saved_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output, reference = root / 'swt.csv', root / 'prepared.csv', root / 'reference.json'
            with source.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(self.row))
                writer.writeheader()
                writer.writerow(self.row)
            reference.write_text(json.dumps([self.reference]), encoding='utf-8')
            prepare.main(['--repo', self.row['repo'], '--input', str(source),
                          '--output', str(output), '--reference-json', str(reference)])
            with output.open(newline='', encoding='utf-8') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]['patch'], self.full)
            manifest = json.loads(output.with_suffix('.manifest.json').read_text())
            self.assertEqual(manifest['instances'], 1)
            self.assertTrue(manifest['swt_test_patches_preserved'])
            self.assertEqual(len(manifest['prepared_csv_sha256']), 64)


if __name__ == '__main__':
    unittest.main()
