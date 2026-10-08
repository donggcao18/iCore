import ast
import csv
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import run_retrieval as retrieval
from scripts.generator.make_prompt_util import get_related_test


ROOT = Path(__file__).resolve().parents[1]
MODEL = 'vendor/model-a'
EXCLUDED = 'pylint-dev__pylint-7277'


class OracleBrtLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv = self.root / 'input.csv'
        self.oracle = self.root / 'oracle.json'
        self.output = self.root / 'output'
        self.rows = [dict(repo='pylint-dev/pylint', instance_id=bug_id,
                          base_commit='abc', version='2.14', problem_statement='issue text',
                          source_dataset=retrieval.DATASET,
                          test_patch='diff --git a/tests/test_one.py b/tests/test_one.py\n',
                          patch='--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n-buggy\n+fixed\n')
                     for bug_id in ('pylint-dev__pylint-6386', 'pylint-dev__pylint-6903', EXCLUDED)]
        with self.csv.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        self.contexts = {row['instance_id']: [dict(file='tests/test_one.py', name='test_one',
                                                  code_content='def test_one(): assert True')]
                         for row in self.rows}
        self.oracle.write_text(json.dumps(self.contexts))
        templates = self.root / 'data/prompt_templates'
        templates.mkdir(parents=True)
        for name in ('prompt_with_test.json', 'prompt_with_test.txt'):
            (templates / name).write_text((ROOT / 'data/prompt_templates' / name).read_text())
        script = (ROOT / 'scripts/launchers/run_brt_oracle_test_swt_verified.sh').read_text()
        self.code = compile(script.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0], '<oracle launcher>', 'exec')
        self.commands = []

    def run_module(self, module, *args, env):
        self.commands.append((module, args, env))
        csv_path = args[args.index('--dataset_csv') + 1]
        with Path(csv_path).open(newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        samples = int(args[args.index('--query_time' if module.endswith('llm_query') else '--samples') + 1])
        if module.endswith('llm_query'):
            generated = Path(args[args.index('--out_dir') + 1])
            generated.mkdir(parents=True, exist_ok=True)
            for row in rows:
                for sample in range(1, samples + 1):
                    (generated / f'{row["instance_id"]}_n{sample}.txt').write_text('def test_bug(): assert False')
        else:
            results = {row['instance_id']: {
                f'{row["instance_id"]}_n{sample}.txt': {'success': True}
                for sample in range(1, samples + 1)} for row in rows}
            Path(args[args.index('--result_file') + 1]).write_text(json.dumps(results))

    def run_launcher(self):
        argv = ['oracle', MODEL, 'pylint-dev/pylint', str(self.oracle), str(self.csv),
                '2', str(self.output), EXCLUDED]
        with patch.object(sys, 'argv', argv), patch.object(retrieval, 'ROOT', self.root), \
             patch.object(retrieval, 'preflight'), patch.object(retrieval, 'run_module', side_effect=self.run_module):
            exec(self.code, {})

    def test_generates_and_evaluates_oracle_context_without_code_artifacts(self):
        self.run_launcher()
        self.assertEqual(len(self.commands), 2)
        generation, evaluation = self.commands
        self.assertTrue(generation[0].endswith('llm_query'))
        self.assertNotIn('--context_code_path', generation[1])
        self.assertEqual(generation[1][generation[1].index('--context_test_path') + 1], self.oracle)
        self.assertTrue(str(generation[1][generation[1].index('--template_file') + 1]).endswith('prompt_with_test.json'))
        self.assertEqual(evaluation[1][evaluation[1].index('--injection_path') + 1], self.oracle)
        ids = Path(generation[2]['SWT_IDS_FILE']).read_text().split()
        self.assertEqual(ids, [row['instance_id'] for row in self.rows[:2]])
        root = self.output / 'swt-bench-verified/vendor%2Fmodel-a/brt/pylint-dev/pylint/oracle_test_augmented_s2'
        summary = json.loads((root / 'summary.json').read_text())
        self.assertEqual(summary['instances'], 2)
        self.assertEqual(summary['candidates'], 4)
        self.assertEqual(summary['excluded_instance_ids'], [EXCLUDED])
        self.assertEqual(json.loads((root / 'run_config.json').read_text())['context_mode'], 'oracle_test_only')

    def test_missing_oracle_record_stops_before_generation(self):
        del self.contexts[self.rows[0]['instance_id']]
        self.oracle.write_text(json.dumps(self.contexts))
        with self.assertRaisesRegex(SystemExit, 'Missing or invalid oracle test context'):
            self.run_launcher()
        self.assertEqual(self.commands, [])

    def test_changed_oracle_rejects_cached_prompt_reuse(self):
        self.run_launcher()
        self.commands.clear()
        self.contexts[self.rows[0]['instance_id']][0]['code_content'] = 'def test_one(): assert False'
        self.oracle.write_text(json.dumps(self.contexts))
        with self.assertRaisesRegex(SystemExit, 'inputs/settings changed'):
            self.run_launcher()
        self.assertEqual(self.commands, [])

    def test_real_test_only_prompt_contains_oracle_tests_without_production_fix(self):
        path = ROOT / 'scripts/generator/llm_query.py'
        tree = ast.parse(path.read_text())
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == 'make_messages_from_dataset')
        no_code = Mock(side_effect=AssertionError('Oracle experiment must not read code retrieval'))
        namespace = dict(ROOT_DIR=str(self.root), Path=Path, os=os, re=re, json=json,
                         get_retrieval_docs=no_code, get_related_test=get_related_test)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        row = dict(self.rows[0], patch='SECRET PRODUCTION FIX', test_patch='SECRET REFERENCE DIFF')
        messages = namespace['make_messages_from_dataset'](
            'oracle_test_augmented', row, None, str(self.oracle),
            str(self.root / 'data/prompt_templates/prompt_with_test.json'),
            prompt_dir=self.root / 'prompts')
        content = messages[-1]['content']
        self.assertIn('issue text', content)
        self.assertIn('def test_one(): assert True', content)
        self.assertNotIn('<code>', content)
        self.assertNotIn('SECRET', json.dumps(messages))
        no_code.assert_not_called()


if __name__ == '__main__':
    unittest.main()
