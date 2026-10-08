import argparse
import ast
from collections import defaultdict
import csv
import glob
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import run_brt as brt
from scripts.utils.benchmark_data import load_selected_csv


ROOT = Path(__file__).resolve().parents[1]
MODEL = 'vendor/model-a'
REPOS = ('pylint-dev/pylint', 'pytest-dev/pytest')


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)


class BrtWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        workspace = patch('scripts.run_retrieval.ROOT', self.root)
        workspace.start()
        self.addCleanup(workspace.stop)
        self.output = self.root / 'retrieval'
        self.csv = self.root / 'dataset.csv'
        self.rows = [dict(repo=repo, instance_id=repo.replace('/', '__') + '-1',
                          problem_statement='bug', base_commit='abc', version='1.0',
                          source_dataset=brt.BENCHMARKS['swt-verified'].source_dataset,
                          patch='diff --git a/code.py b/code.py\n',
                          test_patch='diff --git a/test.py b/test.py\n') for repo in REPOS]
        write_csv(self.csv, self.rows)
        self.args = ['--model', MODEL, '--dataset-csv', str(self.csv), '--output-root', str(self.output)]
        for repo in REPOS:
            self.args += ['--repo', repo]
        for row in self.rows:
            paths = brt.artifact_paths(self.output, 'swt-verified', MODEL, row['repo'])
            paths.code.parent.mkdir(parents=True)
            paths.code.write_text(json.dumps({row['instance_id']: {'symbol': {'code_content': 'def target(): pass'}}}))
            paths.tests.mkdir(parents=True)
            (paths.tests / 'related_tests_4.json').write_text(json.dumps({row['instance_id']: []}))
        self.commands = []

    def run_module(self, module, *args, env):
        self.commands.append((module, args, env.copy()))
        csv_path = Path(args[args.index('--dataset_csv') + 1])
        rows = load_selected_csv(csv_path, env['SWT_IDS_FILE'])
        samples = int(args[args.index('--query_time' if module.endswith('llm_query') else '--samples') + 1])
        if module.endswith('llm_query'):
            output = Path(args[args.index('--out_dir') + 1])
            output.mkdir(parents=True, exist_ok=True)
            for row in rows:
                for sample in range(1, samples + 1):
                    (output / f'{row["instance_id"]}_n{sample}.txt').write_text('def test_bug(): assert False')
        else:
            options = dict(zip(args[::2], args[1::2]))
            results = {row['instance_id']: {
                f'{row["instance_id"]}_n{sample}.txt': {'success': sample == 1}
                for sample in range(1, samples + 1)} for row in rows}
            Path(options['--result_file']).write_text(json.dumps(results))

    def test_two_repositories_use_matching_context_csv_and_isolated_outputs(self):
        with patch.object(brt, 'preflight'), patch.object(brt, 'run_module', side_effect=self.run_module):
            brt.main(self.args + ['--samples', '2', '--provider', 'example/provider', '--test-timeout', '120'])
        self.assertEqual(len(self.commands), 4)
        for index, row in enumerate(self.rows):
            generation, evaluation = self.commands[2 * index:2 * index + 2]
            self.assertTrue(generation[0].endswith('llm_query'))
            self.assertTrue(evaluation[0].endswith('postprocess_swe'))
            self.assertEqual(generation[2]['SWT_IDS_FILE'], evaluation[2]['SWT_IDS_FILE'])
            self.assertEqual(Path(generation[2]['SWT_IDS_FILE']).read_text().split(), [row['instance_id']])
            self.assertEqual(evaluation[2]['ICORE_TEST_TIMEOUT'], '120')
            self.assertEqual(generation[2]['ICORE_LLM_PROVIDER'], 'example/provider')
            root = brt.artifact_paths(self.output, 'swt-verified', MODEL, row['repo']).category('brt') / 'i3_s2'
            summary = json.loads((root / 'summary.json').read_text())
            self.assertEqual(summary['candidates'], 2)
            self.assertEqual(summary['fail_to_pass_candidates'], 1)
            self.assertEqual(summary['reproduced_instances'], 1)

    def test_incomplete_context_in_second_repo_prevents_any_generation(self):
        paths = brt.artifact_paths(self.output, 'swt-verified', MODEL, REPOS[1])
        (paths.tests / 'related_tests_4.json').write_text('{}')
        with patch.object(brt, 'preflight'), patch.object(brt, 'run_module') as run:
            with self.assertRaisesRegex(RuntimeError, 'Missing or invalid tests'):
                brt.main(self.args)
        run.assert_not_called()

    def test_changed_generation_settings_reject_cached_outputs(self):
        with patch.object(brt, 'preflight'), patch.object(brt, 'run_module', side_effect=self.run_module):
            brt.main(self.args)
        with patch.object(brt, 'preflight'), patch.object(brt, 'run_module') as run:
            with self.assertRaisesRegex(RuntimeError, 'configuration/context changed'):
                brt.main(self.args + ['--temperature', '0.2'])
        run.assert_not_called()

    def test_evaluate_stage_uses_existing_candidates_without_generation(self):
        with patch.object(brt, 'preflight'), patch.object(brt, 'run_module', side_effect=self.run_module):
            brt.main(self.args + ['--stage', 'generate'])
            self.commands.clear()
            brt.main(self.args + ['--stage', 'evaluate'])
        self.assertEqual(len(self.commands), 2)
        self.assertTrue(all(module.endswith('postprocess_swe') for module, _, _ in self.commands))

    def test_preflight_does_not_generate_or_write_experiment(self):
        with patch.object(brt, 'preflight') as preflight, patch.object(brt, 'run_module') as run:
            brt.main(self.args + ['--preflight-only'])
        preflight.assert_called_once_with(self.rows, MODEL)
        run.assert_not_called()
        self.assertFalse(any(self.output.rglob('run_config.json')))

    def add_no_code_instances(self):
        paths = brt.artifact_paths(self.output, 'swt-verified', MODEL, REPOS[0])
        code = json.loads(paths.code.read_text())
        skipped = []
        for index, value in enumerate((None, {}, {'symbol': None})):
            row = dict(self.rows[0], instance_id=f'pylint-no-code-{index}')
            self.rows.append(row)
            skipped.append(row['instance_id'])
            code[row['instance_id']] = value
        # A single null keyword must not exclude an instance with another hit.
        code[self.rows[0]['instance_id']]['missing_symbol'] = None
        paths.code.write_text(json.dumps(code))
        write_csv(self.csv, self.rows)
        return paths, skipped

    def test_no_code_instances_are_excluded_from_generation_evaluation_and_rate(self):
        paths, skipped = self.add_no_code_instances()
        # The skipped IDs deliberately have no test context.
        with patch.object(brt, 'preflight') as preflight, patch.object(brt, 'run_module', side_effect=self.run_module):
            brt.main(self.args)
        preflight.assert_called_once_with(self.rows[:2], MODEL)
        root = paths.category('brt') / 'i3_s1'
        self.assertEqual((root / 'selections/selected_ids.txt').read_text().split(), [self.rows[0]['instance_id']])
        summary = json.loads((root / 'summary.json').read_text())
        self.assertEqual(summary['instances'], 1)
        self.assertEqual(summary['total_selected_instances'], 4)
        self.assertEqual(summary['skipped_no_code_instance_ids'], skipped)
        self.assertEqual(summary['instance_reproduction_rate'], 1)
        self.assertFalse(any((root / 'generated_tests' / f'{bug_id}_n1.txt').exists() for bug_id in skipped))

    def test_resume_old_manifest_preserves_candidates_and_excludes_old_no_code_results(self):
        paths, skipped = self.add_no_code_instances()
        with patch.object(brt, 'preflight'), patch.object(brt, 'run_module', side_effect=self.run_module):
            brt.main(self.args)
        root = paths.category('brt') / 'i3_s1'
        manifest_path = root / 'run_config.json'
        manifest = json.loads(manifest_path.read_text())
        del manifest['skipped_no_code_instance_ids']
        manifest_path.write_text(json.dumps(manifest))
        result_path = root / 'execution_results.json'
        results = json.loads(result_path.read_text())
        results[skipped[0]] = {f'{skipped[0]}_n1.txt': {'success': True}}
        result_path.write_text(json.dumps(results))
        candidate = root / 'generated_tests' / f'{self.rows[0]["instance_id"]}_n1.txt'
        original = candidate.read_text()
        with patch.object(brt, 'preflight'), patch.object(brt, 'run_module'):
            brt.main(self.args + ['--stage', 'evaluate'])
        self.assertEqual(candidate.read_text(), original)
        self.assertIn(skipped[0], json.loads(result_path.read_text()))
        self.assertEqual(json.loads((root / 'summary.json').read_text())['fail_to_pass_candidates'], 1)

    def test_all_null_repository_is_skipped_and_next_repository_runs(self):
        paths = brt.artifact_paths(self.output, 'swt-verified', MODEL, REPOS[0])
        paths.code.write_text(json.dumps({self.rows[0]['instance_id']: {'symbol': None}}))
        (paths.tests / 'related_tests_4.json').unlink()
        with patch.object(brt, 'preflight') as preflight, patch.object(brt, 'run_module', side_effect=self.run_module):
            brt.main(self.args)
        preflight.assert_called_once_with([self.rows[1]], MODEL)
        self.assertEqual(len(self.commands), 2)
        summary = json.loads((paths.category('brt') / 'i3_s1/summary.json').read_text())
        self.assertEqual(summary['instances'], 0)
        self.assertIsNone(summary['instance_reproduction_rate'])

    def test_missing_or_malformed_code_is_not_silently_excluded(self):
        paths = brt.artifact_paths(self.output, 'swt-verified', MODEL, REPOS[0])
        for value in ({}, {self.rows[0]['instance_id']: {'symbol': {'code_content': ''}}},
                      {self.rows[0]['instance_id']: ['invalid node']}):
            with self.subTest(value=value):
                paths.code.write_text(json.dumps(value))
                with patch.object(brt, 'run_module') as run, self.assertRaises(RuntimeError):
                    brt.main(self.args)
                run.assert_not_called()


class EvaluationTests(unittest.TestCase):
    def test_pytest_exit_status_and_real_execution_are_required(self):
        namespace = {'re': re, 'os': os, 'remove_ansi_escape_sequences': lambda value: value,
                     'swe_util': SimpleNamespace(swe_test_cmd=lambda *args: 'pytest test.py'),
                     'sp': SimpleNamespace(run=Mock())}
        load_functions(ROOT / 'scripts/libro/postprocess_swe.py', ['run_test'], namespace)
        cases = [
            (0, '1 passed in 0.1s', 0),
            (1, 'FAILED tests/test_bug.py::test_bug\n1 failed in 0.1s', 0),
            (0, '1 skipped in 0.1s', -1),
            (5, 'no tests ran in 0.1s', -1),
            (2, 'ERROR tests/test_bug.py\n1 error in 0.1s', -1),
            (3, 'INTERNALERROR broken\n1 passed in 0.1s', -1),
            (1, '1 error, 1 passed in 0.1s', -1),
        ]
        for project in ('pylint', 'pytest'):
            for code, stdout, expected in cases:
                with self.subTest(project=project, code=code, stdout=stdout):
                    namespace['sp'].run.return_value = subprocess.CompletedProcess(
                        [], code, stdout=stdout.encode(), stderr=b'')
                    with patch.dict(os.environ, {'ICORE_TEST_TIMEOUT': '120'}):
                        status, _, _ = namespace['run_test'](f'/repos/{project}/', 'tests/test_bug.py::test_bug', 'env__1')
                    self.assertEqual(status, expected)
                    self.assertEqual(namespace['sp'].run.call_args.kwargs['timeout'], 120)

    def test_only_test_failure_to_pass_is_success(self):
        passed = dict(autogen_failed=False, runtime_error=False, compile_error=False)
        failed = dict(passed, autogen_failed=True)
        error = dict(failed, runtime_error=True)
        for buggy, fixed, expected in ((failed, passed, True), (error, passed, False),
                                       (passed, passed, False), (failed, error, False)):
            with self.subTest(buggy=buggy, fixed=fixed):
                individual = Mock(side_effect=[buggy, fixed])
                namespace = {'swe_util': SimpleNamespace(get_env_name=lambda row: 'env__1'),
                             'REPO_ROOT_DIR': '/repos', 'inject_prefix_rootdir': lambda repo: '/repos/pylint/',
                             'individual_run': individual, 'tqdm': lambda values: values}
                for name in ('git_reset_hash', 'git_clean_all', 'setup_environment', 'git_reset', 'git_clean', 'git_apply'):
                    namespace[name] = Mock()
                load_functions(ROOT / 'scripts/libro/postprocess_swe.py', ['twover_run_experiment'], namespace)
                row = dict(repo='pylint-dev/pylint', instance_id='bug-1', base_commit='abc', patch='fix')
                result = namespace['twover_run_experiment'](row, ['test code'], injection='references.json')
                self.assertEqual(result[0]['success'], expected)
                namespace['git_apply'].assert_called_once_with('/repos/pylint/', 'fix')
                self.assertEqual(individual.call_count, 2)

    def test_csv_evaluation_resumes_by_sample_without_loading_lite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            row = dict(repo='pylint-dev/pylint', instance_id='bug-1', base_commit='abc', problem_statement='bug', patch='fix')
            csv_path = root / 'selected.csv'
            write_csv(csv_path, [row])
            generated = root / 'generated'
            generated.mkdir()
            for sample in (1, 2):
                (generated / f'bug-1_n{sample}.txt').write_text(f'def test_{sample}(): assert True')
            result_path = root / 'results.json'
            result_path.write_text(json.dumps({'bug-1': {'bug-1_n1.txt': {'success': False}}}))
            evaluate = Mock(return_value=[{'success': True}])
            load_dataset = Mock(side_effect=AssertionError('Must not load another benchmark'))
            namespace = dict(__name__='__main__', argparse=argparse, Path=Path, os=os, json=json,
                             glob=glob, defaultdict=defaultdict, load_selected_csv=load_selected_csv,
                             load_dataset=load_dataset, twover_run_experiment=evaluate)
            path = ROOT / 'scripts/libro/postprocess_swe.py'
            tree = ast.parse(path.read_text(encoding='utf-8'))
            main = next(node for node in tree.body if isinstance(node, ast.If))
            code = compile(ast.Module(body=[main], type_ignores=[]), str(path), 'exec')
            argv = ['evaluate', '--dataset_csv', str(csv_path), '--gen_test_dir', str(generated),
                    '--result_file', str(result_path), '--samples', '2']
            with patch.object(sys, 'argv', argv), patch.dict(os.environ, {}, clear=True):
                exec(code, namespace)
                exec(code, namespace)
            evaluate.assert_called_once_with(row, ['def test_2(): assert True'], injection='libro')
            load_dataset.assert_not_called()
            results = json.loads(result_path.read_text())['bug-1']
            self.assertFalse(results['bug-1_n1.txt']['success'])
            self.assertTrue(results['bug-1_n2.txt']['success'])

    def test_explicit_prompt_directory_prevents_legacy_cache_reuse_and_patch_leakage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = root / 'data/brt/prompts/bug-1.json'
            legacy.parent.mkdir(parents=True)
            legacy.write_text('[{"role":"user","content":"stale prompt"}]')
            namespace = {'ROOT_DIR': str(root), 'Path': Path, 'os': os, 're': re, 'json': json,
                         'get_retrieval_docs': Mock(return_value='buggy source'),
                         'get_related_test': Mock(return_value='reference test')}
            load_functions(ROOT / 'scripts/generator/llm_query.py', ['make_messages_from_dataset'], namespace)
            row = dict(instance_id='bug-1', problem_statement='issue text', patch='SECRET FIX', test_patch='SECRET TEST')
            messages = namespace['make_messages_from_dataset'](
                'brt', row, 'code.json', 'tests.json',
                str(ROOT / 'data/prompt_templates/prompt_with_code_and_tests.json'),
                prompt_dir=root / 'new/prompts')
            text = json.dumps(messages)
            for expected in ('issue text', 'buggy source', 'reference test'):
                self.assertIn(expected, text)
            for forbidden in ('stale prompt', 'SECRET FIX', 'SECRET TEST'):
                self.assertNotIn(forbidden, text)


if __name__ == '__main__':
    unittest.main()
