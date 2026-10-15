import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from scripts import run_retrieval as retrieval
from tests import test_oracle_brt_launcher as base_tests


class PatchedOracleBrtLauncherTests(unittest.TestCase):
    def setUp(self):
        base_tests.OracleBrtLauncherTests.setUp(self)
        self.injection = self.root / 'base_injection.json'
        self.injection.write_text(json.dumps(self.contexts), encoding='utf-8')
        self.contexts = {row['instance_id']: [dict(
            file='tests/test_one.py', name='test_new_regression',
            code_content='def test_new_regression(): assert "PATCHED SCENARIO"')]
            for row in self.rows}
        self.oracle.write_text(json.dumps(self.contexts), encoding='utf-8')
        script = (base_tests.ROOT / 'scripts/launchers/run_brt_oracle_test_patched_swt_verified.sh').read_text()
        self.code = compile(script.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0], '<patched oracle launcher>', 'exec')
        self.preflight = unittest.mock.Mock()

    run_module = base_tests.OracleBrtLauncherTests.run_module

    def run_launcher(self, stage='all', injection=None, samples='2'):
        argv = ['oracle', base_tests.MODEL, 'pylint-dev/pylint', str(self.oracle), str(self.csv),
                samples, str(self.output), base_tests.EXCLUDED, stage,
                str(self.injection) if injection is None else str(injection)]
        with patch.object(sys, 'argv', argv), patch.object(retrieval, 'ROOT', self.root), \
             patch.object(retrieval, 'preflight', self.preflight), \
             patch.object(retrieval, 'run_module', side_effect=self.run_module):
            exec(self.code, {})

    def result_root(self):
        return self.output / 'swt-bench-verified/vendor%2Fmodel-a/brt/pylint-dev/pylint/oracle_test_patched_s2'

    def test_generate_then_evaluate_uses_patched_prompt_and_separate_base_injection(self):
        self.run_launcher()
        generation, evaluation = self.commands
        self.assertTrue(generation[0].endswith('llm_query'))
        self.assertTrue(evaluation[0].endswith('postprocess_swe'))
        self.assertNotIn('--context_code_path', generation[1])
        prompt_context = Path(generation[1][generation[1].index('--context_test_path') + 1])
        injection = Path(evaluation[1][evaluation[1].index('--injection_path') + 1])
        self.assertEqual(prompt_context.read_bytes(), self.oracle.read_bytes())
        self.assertEqual(injection.read_bytes(), self.injection.read_bytes())
        self.assertNotEqual(prompt_context, injection)
        self.assertEqual(json.loads(injection.read_text())[self.rows[0]['instance_id']][0]['name'], 'test_one')
        self.assertTrue(str(generation[1][generation[1].index('--template_file') + 1]).endswith('prompt_with_test.json'))
        root = self.result_root()
        summary = json.loads((root / 'summary.json').read_text())
        self.assertEqual(summary['instances'], 2)
        self.assertEqual(summary['candidates'], 4)
        self.assertEqual(summary['excluded_instance_ids'], [base_tests.EXCLUDED])
        manifest = json.loads((root / 'run_config.json').read_text())
        self.assertEqual(manifest['oracle_revision'], 'patched')
        self.assertEqual(manifest['evaluation_injection'], 'base_oracle')
        self.assertFalse((root.parent / 'oracle_test_augmented_s2').exists())

    def test_changed_prompt_or_injection_rejects_cache_reuse(self):
        self.run_launcher()
        self.commands.clear()
        for path in (self.oracle, self.injection):
            with self.subTest(path=path):
                original = path.read_bytes()
                contexts = json.loads(original)
                contexts[self.rows[0]['instance_id']][0]['code_content'] += '\n# changed'
                path.write_text(json.dumps(contexts), encoding='utf-8')
                try:
                    with self.assertRaisesRegex(SystemExit, 'inputs/settings changed'):
                        self.run_launcher()
                    self.assertEqual(self.commands, [])
                finally:
                    path.write_bytes(original)

    def test_generate_only_then_evaluate_only(self):
        self.run_launcher(stage='generate')
        self.assertEqual(len(self.commands), 1)
        self.assertFalse((self.result_root() / 'execution_results.json').exists())
        self.commands.clear()
        self.run_launcher(stage='evaluate')
        self.assertEqual(len(self.commands), 1)
        self.assertTrue(self.commands[0][0].endswith('postprocess_swe'))
        self.assertTrue((self.result_root() / 'summary.json').exists())

    def test_evaluate_requires_saved_configuration(self):
        with self.assertRaisesRegex(SystemExit, 'No saved patched-oracle configuration'):
            self.run_launcher(stage='evaluate')
        self.assertEqual(self.commands, [])
        self.preflight.assert_not_called()

    def test_libro_injection_does_not_require_base_context(self):
        self.injection.unlink()
        self.run_launcher(injection='libro')
        evaluation = self.commands[1][1]
        self.assertEqual(evaluation[evaluation.index('--injection_path') + 1], 'libro')
        manifest = json.loads((self.result_root() / 'run_config.json').read_text())
        self.assertEqual(manifest['evaluation_injection'], 'libro')
        self.assertIsNone(manifest['injection_sha256'])

    def test_new_reference_names_cannot_be_used_as_injection_context(self):
        with self.assertRaisesRegex(SystemExit, 'patched tests are generation context only'):
            self.run_launcher(injection=self.oracle)
        self.assertEqual(self.commands, [])

    def test_invalid_sample_count_stops_before_generation(self):
        for value in ('0', '-1', 'not-an-integer'):
            with self.subTest(value=value), self.assertRaisesRegex(SystemExit, 'positive integer'):
                self.run_launcher(samples=value)
        self.assertEqual(self.commands, [])

    def test_missing_context_stops_before_generation(self):
        for path in (self.oracle, self.injection):
            with self.subTest(path=path):
                original = path.read_bytes()
                contexts = json.loads(original)
                del contexts[self.rows[0]['instance_id']]
                path.write_text(json.dumps(contexts), encoding='utf-8')
                try:
                    with self.assertRaisesRegex(SystemExit, 'Missing or invalid'):
                        self.run_launcher()
                finally:
                    path.write_bytes(original)
        self.assertEqual(self.commands, [])


if __name__ == '__main__':
    unittest.main()
