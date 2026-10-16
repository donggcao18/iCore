"""Behavioral tests for context, faithful insertion, repair, and frozen execution."""

import csv
import difflib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.brt_harness.context import Host, TestContext
from scripts.brt_harness.execution import PytestRunner, classify, evaluate_frozen, git, snapshot
from scripts.brt_harness.materialize import materialize, parse_candidate
from scripts.brt_harness.workflow import generate_instance
from scripts.run_brt_harness import main


def candidate(code='def test_brt_value():\n    assert VALUE == 1\n', **kwargs):
    return dict(trigger='a comma in the input', expected_behavior='retain the comma',
                distinguishing_assertion='the returned value includes the comma',
                additional_imports='', helpers='', test_code=code, **kwargs)


def reference(file, name):
    return dict(file=file, name=name, code_content='retrieved snippet is refreshed from base')


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.sources = {
            'core.py': 'def api(value): return value\n',
            'tests/conftest.py': 'import pytest\n@pytest.fixture\ndef sample(): return "a,b"\n',
            'tests/test_core.py': (
                'from core import api\nLIMIT = 1\n'
                'class TestCore:\n'
                '    def setup_method(self): self.limit = LIMIT\n'
                '    def helper(self, text): return api(text)\n'
                '    def test_existing(self, sample): assert self.helper(sample)\n'),
            'support.py': 'brt_input = "a,b"\n',
        }
        self.context = TestContext(self.sources, [reference('tests/test_core.py', 'TestCore.test_existing')])

    def test_context_includes_fixture_setup_constants_and_helpers(self):
        host = self.context.hosts('comma')[0]
        self.assertEqual(host.class_name, 'TestCore')
        package = self.context.package(host)
        ids = {record['id'] for record in package['records']}
        self.assertIn('tests/conftest.py::sample', ids)
        self.assertTrue(any('setup_method' in sid for sid in ids))
        self.assertTrue(any('helper' in sid for sid in ids))
        self.assertIn('tests/test_core.py::LIMIT', ids)
        self.assertLessEqual(sum(len(r['code']) for r in package['records']), 24000)

    def test_missing_reference_falls_back_to_existing_base_tests(self):
        context = TestContext(self.sources, [reference('tests/test_new.py', 'test_future')])
        host = context.hosts('api')[0]
        self.assertIn(host.file, self.sources)
        self.assertTrue(context.package(host)['retrieval_fallback'])
        self.assertIn('tests/test_new.py::test_future', context.rejected)

    def test_targeted_lookup_adds_exact_missing_definition(self):
        host = self.context.hosts('comma')[0]
        extra = self.context.feedback_symbols(host, {'diagnostic': "NameError: name 'brt_input' is not defined"})
        self.assertEqual(extra, ['support.py::brt_input'])
        self.assertIn('support.py::brt_input', {r['id'] for r in self.context.package(host, extra)['records']})


class MaterializeTests(unittest.TestCase):
    def test_module_preserves_helpers_constants_and_decorators(self):
        source = '"""host"""\nfrom __future__ import annotations\nimport math\nVALUE = 1\ndef test_old(): pass\n'
        host = Host('test_core.py::test_old', 'test_core.py', 'test_old', None)
        value = candidate()
        value['additional_imports'] = 'import math\nfrom functools import wraps\n'
        value['helpers'] = 'brt_constant = math.pi\ndef brt_helper(): return brt_constant\n'
        value['test_code'] = '@wraps(test_old)\ndef test_brt_value():\n    assert brt_helper() > 0\n'
        result = materialize(source, host, value)
        self.assertEqual(result['source'].count('import math'), 1)
        self.assertLess(result['source'].index('import math'), result['source'].index('brt_constant ='))
        self.assertIn('@wraps(test_old)', result['source'])
        self.assertIn('VALUE = 1\ndef test_old(): pass', result['source'])
        namespace = {}
        exec(result['source'], namespace)
        namespace['test_brt_value']()

    def test_class_keeps_setup_bases_and_other_methods(self):
        source = ('import unittest\nVALUE = 1\nclass TestCore(unittest.TestCase):\n'
                  '    def setUp(self): self.value = VALUE\n'
                  '    def test_old(self): self.assertEqual(self.value, 1)\n'
                  '\ndef unrelated(): return 2\n')
        host = Host('test_core.py::TestCore.test_old', 'test_core.py', 'TestCore.test_old', 'TestCore')
        value = candidate('def test_brt_value(self):\n    self.assertEqual(self.value, brt_helper())\n')
        value['helpers'] = 'def brt_helper(): return VALUE\n'
        result = materialize(source, host, value)
        namespace = {}
        exec(result['source'], namespace)
        case = namespace['TestCore']('test_brt_value')
        outcome = unittest.TestResult()
        case.run(outcome)
        self.assertTrue(outcome.wasSuccessful(), outcome.errors)
        self.assertEqual(result['selector'], 'test_core.py::TestCore::test_brt_value')
        self.assertEqual(namespace['unrelated'](), 2)

    def test_rejects_shadowing_extra_tests_and_class_wrappers(self):
        host = Host('test.py::test_old', 'test.py', 'test_old', None)
        source = 'VALUE = 1\ndef test_old(): pass\n'
        changes = [dict(helpers='VALUE = 2'), dict(helpers='def test_other(): pass'),
                   dict(test_code='class TestNew:\n    def test_brt_x(self): pass'),
                   dict(additional_imports='from pkg import *')]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                value = candidate()
                value.update(change)
                materialize(source, host, value)

    def test_json_contract_and_syntax_validation(self):
        self.assertEqual(parse_candidate('```json\n' + json.dumps(candidate()) + '\n```')['trigger'], 'a comma in the input')
        with self.assertRaises(ValueError):
            parse_candidate('{}')
        with self.assertRaises(SyntaxError):
            materialize('def test_old(): pass\n', Host('test.py::test_old', 'test.py', 'test_old', None),
                        candidate('def test_brt_bad():\n    finally:\n        pass\n'))


class ReportTests(unittest.TestCase):
    def test_all_parameter_cases_must_run_and_skips_are_invalid(self):
        selector = 'test.py::test_brt_value'
        collected = [selector + '[a]', selector + '[b]']
        events = [dict(nodeid=node, phase='call', outcome='passed') for node in collected]
        report = dict(collected=collected, events=events)
        self.assertEqual(classify(report, 0, selector)['status'], 'passed')
        report['events'] = events[:1]
        self.assertEqual(classify(report, 0, selector)['status'], 'runner_error')
        report['events'] = events + [dict(nodeid=collected[0], phase='setup', outcome='skipped')]
        self.assertEqual(classify(report, 0, selector)['status'], 'skipped')
        self.assertEqual(classify(dict(collected=['wrong'], events=[]), 0, selector)['status'], 'collection_error')


class RepositoryFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        self.sources = {
            '.gitattributes': 'tests/test_core.py export-ignore\n',
            'core.py': "def api(value):\n    return value.replace(',', '')\n",
            'support.py': "brt_input = 'a,b'\n",
            'tests/conftest.py': "import pytest\n@pytest.fixture\ndef sample(): return 'a,b'\n",
            'tests/test_core.py': "from core import api\ndef test_existing(sample):\n    assert api('ab') == 'ab'\n",
        }
        for name, content in self.sources.items():
            file = self.repo / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(content, encoding='utf-8')
        git(self.repo, 'init')
        git(self.repo, 'add', '.')
        git(self.repo, '-c', 'user.name=Harness Test', '-c', 'user.email=test@example.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '-m', 'fixture')
        commit = git(self.repo, 'rev-parse', 'HEAD').decode().strip()
        production_patch = ''.join(difflib.unified_diff(self.sources['core.py'].splitlines(True),
                    ['def api(value):\n', '    return value\n'], fromfile='a/core.py', tofile='b/core.py'))
        self.row = dict(instance_id='example__core-1', repo='example/core', base_commit=commit,
                        problem_statement='A comma is incorrectly removed from the output.',
                        patch=production_patch, test_patch='SECRET_TEST_PATCH')
        self.refs = [reference('tests/test_core.py', 'test_existing')]

    def test_prepare_requires_no_model_or_benchmark_environment(self):
        data, refs = self.root / 'rows.csv', self.root / 'refs.json'
        with data.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.row))
            writer.writeheader()
            writer.writerow(self.row)
        refs.write_text(json.dumps({self.row['instance_id']: self.refs}))
        output = self.root / 'output'
        argv = ['--dataset-csv', str(data), '--repo', 'example/core', '--repo-dir', str(self.repo),
                '--tests', str(refs), '--output-dir', str(output), '--stage', 'prepare']
        main(argv)
        folder = output / self.row['instance_id']
        package = json.loads((folder / 'prepared.json').read_text())
        self.assertTrue(package['hosts'])
        self.assertNotIn('SECRET_TEST_PATCH', (folder / 'run_config.json').read_text())
        with self.assertRaises(RuntimeError):
            main(argv + ['--attempts', '7'])

    def test_snapshot_preserves_files_ignored_by_git_archive(self):
        with snapshot(self.repo, self.row['base_commit']) as work:
            self.assertEqual((work / 'tests/test_core.py').read_text(), self.sources['tests/test_core.py'])

    def test_budget_preserves_expected_behavior_and_deduplicates_execution(self):
        value = candidate('def test_brt_missing():\n    assert missing_name\n')
        changed = dict(value, expected_behavior='accept buggy output')
        answers = iter(map(json.dumps, [value, changed, value]))
        runner = Mock()
        runner.run.side_effect = [dict(status='passed', diagnostic=''),
                                  dict(status='failed', diagnostic="NameError: name 'missing_name' is not defined")]
        public = {key: self.row[key] for key in ('instance_id', 'repo', 'base_commit', 'problem_statement')}
        result = generate_instance(public, self.repo, TestContext(self.sources, self.refs), None,
                                   runner, lambda *args: next(answers), self.root / 'budget', attempts=3)
        self.assertEqual(result['attempts_used'], 3)
        self.assertEqual(result['history'][1]['status'], 'invalid_candidate')
        self.assertTrue(result['history'][2]['execution_reused'])
        self.assertEqual(runner.run.call_count, 2)  # calibration + one unique candidate
        self.assertEqual(result['selected']['candidate']['expected_behavior'], value['expected_behavior'])

    def test_unstable_final_outcomes_are_not_success(self):
        host = TestContext(self.sources, self.refs).hosts('comma')[0]
        built = materialize(self.sources[host.file], host,
                            candidate('def test_brt_value(sample):\n    assert api(sample) == "a,b"\n'))
        runner = Mock()
        selector = built['selector']
        runner.run.side_effect = [dict(status=status, collected=[selector])
                                  for status in ('failed', 'passed', 'passed', 'passed')]
        result = evaluate_frozen(self.repo, self.row, built, runner, self.root / 'flaky')
        self.assertFalse(result['success'])
        self.assertEqual(result['buggy']['status'], 'flaky')

    @unittest.skipUnless(importlib.util.find_spec('pytest'), 'pytest required for subprocess integration')
    def test_cli_selected_metric_includes_unselected_instances_and_evaluation_resumes(self):
        data, refs = self.root / 'rows.csv', self.root / 'refs.json'
        other = dict(self.row, instance_id='example__core-2', problem_statement='Ordinary input still works.')
        with data.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.row))
            writer.writeheader()
            writer.writerows([self.row, other])
        refs.write_text(json.dumps({row['instance_id']: self.refs for row in (self.row, other)}))
        answers = iter([json.dumps(candidate('def test_brt_comma(sample):\n    assert api(sample) == "a,b"\n')),
                        json.dumps(candidate('def test_brt_normal(sample):\n    assert api("ab") == "ab"\n'))])
        def complete(*args):
            self.assertEqual(os.environ['ICORE_LLM_TIMEOUT'], '750')
            return next(answers)

        fake_api = SimpleNamespace(query_llm=complete)
        output = self.root / 'cli'
        argv = ['--dataset-csv', str(data), '--repo', 'example/core', '--repo-dir', str(self.repo),
                '--tests', str(refs), '--output-dir', str(output), '--attempts', '1',
                '--python', sys.executable, '--repetitions', '1', '--llm-timeout', '750']
        with patch.dict(sys.modules, {'scripts.utils.llm_api': fake_api}):
            main(argv)
        summary = json.loads((output / 'summary.json').read_text())
        self.assertEqual(summary['instances'], 2)
        self.assertEqual(summary['selected_instances'], 1)
        self.assertEqual(summary['instance_reproduction_rate'], 0.5)
        manifest = json.loads((output / self.row['instance_id'] / 'run_config.json').read_text())
        self.assertEqual(manifest['settings']['request_timeout'], '750')
        with patch('scripts.run_brt_harness.evaluate_frozen', side_effect=AssertionError('must reuse evaluation')):
            main(argv + ['--stage', 'evaluate'])
        # A changed reference fix cannot reuse an old final result.
        self.row['patch'] += '\n'
        with data.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.row))
            writer.writeheader()
            writer.writerows([self.row, other])
        with self.assertRaises(RuntimeError):
            main(argv + ['--stage', 'evaluate'])

    @unittest.skipUnless(importlib.util.find_spec('pytest'), 'pytest required for subprocess integration')
    def test_real_retrieval_repair_and_frozen_patch_fail_to_pass(self):
        context = TestContext(self.sources, self.refs)
        first = candidate('def test_brt_comma(sample):\n    assert api(brt_input) == "a,b"\n')
        second = candidate('def test_brt_comma(sample):\n    assert api(brt_helper(sample)) == "a,b"\n')
        second['helpers'] = 'def brt_helper(value): return value\n'
        second['additional_imports'] = 'from support import brt_input\n'
        answers = [json.dumps(first), json.dumps(second)]
        prompts = []

        def complete(messages, temperature):
            prompts.append(messages)
            return answers[len(prompts) - 1]

        public = {key: self.row[key] for key in ('instance_id', 'repo', 'base_commit', 'problem_statement')}
        runner = PytestRunner(sys.executable, timeout=20)
        runner.preflight()
        generated = generate_instance(public, self.repo, context, None, runner, complete,
                                      self.root / 'generated', attempts=4)
        self.assertEqual(generated['attempts_used'], 2)
        self.assertIn('support.py::brt_input', generated['support_symbols_added'])
        self.assertIn('support.py::brt_input', prompts[1][1]['content'])
        self.assertNotIn('SECRET_TEST_PATCH', json.dumps(prompts))
        self.assertIsNotNone(generated['selected'])
        evaluated = evaluate_frozen(self.repo, self.row, generated['selected'], runner, self.root / 'evaluation')
        self.assertTrue(evaluated['success'], evaluated)
        self.assertEqual(evaluated['buggy']['status'], 'failed')
        self.assertEqual(evaluated['fixed']['status'], 'passed')
        self.assertEqual(len(evaluated['fixed']['runs']), 2)
        # Source checkout and its tracked/untracked state are never modified.
        self.assertEqual(git(self.repo, 'status', '--porcelain'), b'')
        self.assertEqual((self.repo / 'core.py').read_text(), self.sources['core.py'])
        # Resume reuses all saved responses and executions without another call.
        resumed = generate_instance(public, self.repo, context, None, runner,
                                    lambda *args: self.fail('resume called the model'),
                                    self.root / 'generated', attempts=4)
        self.assertEqual(resumed['selected'], generated['selected'])

    @unittest.skipUnless(importlib.util.find_spec('pytest'), 'pytest required for subprocess integration')
    def test_real_collection_errors_skips_and_parameterization(self):
        with snapshot(self.repo, self.row['base_commit']) as work:
            file = work / 'tests/test_probe.py'
            file.write_text('import pytest\n@pytest.mark.parametrize("x", [1, 2])\ndef test_brt_params(x): assert x\n')
            runner = PytestRunner(sys.executable, timeout=20)
            result = runner.run(work, 'tests/test_probe.py::test_brt_params', self.root / 'params')
            self.assertEqual(result['status'], 'passed', result)
            self.assertEqual(len(result['collected']), 2)
            file.write_text('import pytest\n@pytest.mark.skip\ndef test_brt_skip(): pass\n')
            result = runner.run(work, 'tests/test_probe.py::test_brt_skip', self.root / 'skip')
            self.assertEqual(result['status'], 'skipped', result)
            file.write_text('def test_brt_missing(unknown_fixture): pass\n')
            result = runner.run(work, 'tests/test_probe.py::test_brt_missing', self.root / 'missing')
            self.assertEqual(result['status'], 'setup_error', result)
            self.assertIn("fixture 'unknown_fixture' not found", result['diagnostic'])


if __name__ == '__main__':
    unittest.main()
