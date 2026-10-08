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
from scripts.generator.make_prompt_util import get_retrieval_docs


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = 'pylint-dev__pylint-7277'


class OracleCodeBrtLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv = self.root / 'input.csv'
        self.oracle = self.root / 'base.json'
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
        self.contexts = {row['instance_id']: {'code.py::Checker': dict(
            obj_name='Checker', node_type='class', path='code.py', code_start_line=1,
            code_end_line=3, parent=None,
            code_content='class Checker:\n    def check(self):\n        return "BUGGY BASE CODE"')}
            for row in self.rows}
        self.write_contexts()
        templates = self.root / 'data/prompt_templates'
        templates.mkdir(parents=True)
        for name in ('prompt_with_code.json', 'prompt_with_code.txt'):
            (templates / name).write_text((ROOT / 'data/prompt_templates' / name).read_text(encoding='utf-8'),
                                         encoding='utf-8')
        script = (ROOT / 'scripts/launchers/run_brt_oracle_code_swt_verified.sh').read_text()
        self.code = compile(script.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0], '<code oracle launcher>', 'exec')
        self.commands = []

    def write_contexts(self):
        self.oracle.write_text(json.dumps(self.contexts), encoding='utf-8')

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

    def run_launcher(self, stage='all'):
        argv = ['oracle', 'vendor/model-a', 'pylint-dev/pylint', str(self.oracle), str(self.csv),
                '2', str(self.output), EXCLUDED, stage]
        with patch.object(sys, 'argv', argv), patch.object(retrieval, 'ROOT', self.root), \
             patch.object(retrieval, 'preflight'), patch.object(retrieval, 'run_module', side_effect=self.run_module):
            exec(self.code, {})

    @property
    def experiment(self):
        return self.output / 'swt-bench-verified/vendor%2Fmodel-a/brt/pylint-dev/pylint/oracle_code_base_s2'

    def test_code_only_generation_and_evaluation_use_separate_outputs(self):
        self.run_launcher()
        generation, evaluation = self.commands
        self.assertTrue(generation[0].endswith('llm_query'))
        self.assertNotIn('--context_test_path', generation[1])
        self.assertEqual(generation[1][generation[1].index('--context_code_path') + 1], self.oracle)
        self.assertEqual(generation[1][generation[1].index('--template_file') + 1].name, 'prompt_with_code.json')
        self.assertEqual(evaluation[1][evaluation[1].index('--injection_path') + 1], 'libro')
        self.assertNotIn(self.oracle, evaluation[1])
        ids = Path(generation[2]['SWT_IDS_FILE']).read_text().split()
        self.assertEqual(ids, [row['instance_id'] for row in self.rows[:2]])
        summary = json.loads((self.experiment / 'summary.json').read_text())
        self.assertEqual(summary['instances'], 2)
        self.assertEqual(summary['candidates'], 4)
        self.assertEqual(summary['excluded_instance_ids'], [EXCLUDED])
        self.assertEqual(summary['no_code_instance_ids'], [])
        self.assertEqual(json.loads((self.experiment / 'run_config.json').read_text())['context_mode'],
                         'oracle_code_only')

    def test_missing_empty_or_malformed_code_stops_before_generation(self):
        bug_id = self.rows[0]['instance_id']
        for value in (None, {}, {'object': None}, {'object': {'code_content': 'def bug(): pass'}}):
            with self.subTest(value=value):
                self.contexts[bug_id] = value
                self.write_contexts()
                with self.assertRaisesRegex(SystemExit, 'Missing or invalid oracle code context'):
                    self.run_launcher()
        self.assertEqual(self.commands, [])

    def test_changed_code_rejects_cached_prompt_reuse(self):
        self.run_launcher()
        self.commands.clear()
        self.contexts[self.rows[0]['instance_id']]['code.py::Checker']['code_content'] = 'class Changed: pass'
        self.write_contexts()
        with self.assertRaisesRegex(SystemExit, 'inputs/settings changed'):
            self.run_launcher()
        self.assertEqual(self.commands, [])

    def test_generate_then_evaluate_reuses_saved_candidates(self):
        self.run_launcher(stage='generate')
        self.assertEqual(len(self.commands), 1)
        self.assertFalse((self.experiment / 'execution_results.json').exists())
        self.commands.clear()
        self.run_launcher(stage='evaluate')
        self.assertEqual(len(self.commands), 1)
        self.assertTrue(self.commands[0][0].endswith('postprocess_swe'))

    def test_evaluate_without_generation_stops(self):
        with self.assertRaisesRegex(SystemExit, 'run generation first'):
            self.run_launcher(stage='evaluate')
        self.assertEqual(self.commands, [])

    def test_real_code_only_prompt_keeps_class_and_omits_tests_and_fixes(self):
        path = ROOT / 'scripts/generator/llm_query.py'
        tree = ast.parse(path.read_text())
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == 'make_messages_from_dataset')
        no_tests = Mock(side_effect=AssertionError('Code-only experiment must not read test retrieval'))
        namespace = dict(ROOT_DIR=str(self.root), Path=Path, os=os, re=re, json=json,
                         get_retrieval_docs=get_retrieval_docs, get_related_test=no_tests)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        row = dict(self.rows[0], patch='SECRET PRODUCTION FIX', test_patch='SECRET REFERENCE DIFF')
        messages = namespace['make_messages_from_dataset'](
            'oracle_code_base', row, str(self.oracle), None,
            str(self.root / 'data/prompt_templates/prompt_with_code.json'),
            prompt_dir=self.root / 'prompts')
        content = messages[-1]['content']
        self.assertIn('issue text', content)
        self.assertIn('class Checker:', content)
        self.assertIn('BUGGY BASE CODE', content)
        self.assertIn('<code>', content)
        self.assertNotIn('<test>', content)
        self.assertNotIn('SECRET', json.dumps(messages))
        no_tests.assert_not_called()


if __name__ == '__main__':
    unittest.main()
