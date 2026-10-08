import ast
import copy
import csv
from contextlib import nullcontext, redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import run_retrieval as pipeline
from scripts.utils.benchmark_data import load_selected_csv
from scripts.generator.make_prompt_util import get_retrieval_docs

TEST_MODEL = 'vendor/model-a'
TEST_REPOS = ('pylint-dev/pylint', 'pytest-dev/pytest')


def write_csv(path, rows):
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_function(path, name, globals_):
    # Exercise the actual function without importing unavailable API/graph SDKs.
    tree = ast.parse(path.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), globals_)
    return globals_[name]


class RetrievalPipelineTests(unittest.TestCase):
    def setUp(self):
        keys = patch.dict(pipeline.API_KEY, {TEST_MODEL: 'test-key', 'other/model-b': 'test-key'})
        urls = patch.dict(pipeline.BASE_URL, {TEST_MODEL: 'https://example.invalid/v1', 'other/model-b': 'https://example.invalid/v1'})
        keys.start()
        urls.start()
        self.addCleanup(keys.stop)
        self.addCleanup(urls.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv = self.root / 'dataset.csv'
        self.rows = [dict(repo=repo, instance_id=f'{repo.replace("/", "__")}-1',
                          base_commit='abc', problem_statement='bug', version='1.0',
                          source_dataset=pipeline.DATASET, test_patch='diff --git a/test.py b/test.py\n')
                     for repo in TEST_REPOS]
        write_csv(self.csv, self.rows)

    def test_selection_ignores_inherited_ids_but_loader_enforces_requested_ids(self):
        ids = self.root / 'ids.txt'
        ids.write_text(self.rows[0]['instance_id'] + '\n', encoding='utf-8')
        with patch.dict(os.environ, {'SWT_IDS_FILE': str(ids)}):
            self.assertEqual(load_selected_csv(self.csv), self.rows[:1])
            self.assertEqual(pipeline.select_rows(self.csv, TEST_REPOS[1]), self.rows[1:])
        ids.write_text('missing-1\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'missing'):
            load_selected_csv(self.csv, ids)

    def test_rejects_wrong_dataset_and_duplicate_rows(self):
        wrong = [dict(self.rows[0], source_dataset='SWE-bench/SWE-bench_Lite')]
        write_csv(self.csv, wrong)
        with self.assertRaisesRegex(ValueError, 'normalized'):
            pipeline.select_rows(self.csv, TEST_REPOS[0])
        write_csv(self.csv, [self.rows[0], self.rows[0]])
        with self.assertRaisesRegex(ValueError, 'unique'):
            pipeline.select_rows(self.csv, TEST_REPOS[0])

    def fake_stage(self, module, *args, env=None):
        self.commands.append((module, args, env))
        def option(name):
            return args[args.index(name) + 1]
        selected = load_selected_csv(option('--dataset_csv'), env['SWT_IDS_FILE'])
        self.assertEqual(len(selected), 1)
        row = selected[0]
        ident = row['instance_id']
        if '--model' in args:
            self.assertIn(option('--model'), (TEST_MODEL, 'other/model-b'))
        if module.endswith('extract_keywords'):
            Path(option('--keywords_path')).write_text(json.dumps({ident: ['function']}))
        elif module.endswith('repo_graph.graph'):
            (Path(option('--graph_path')) / f'{ident}_graph.pkl').write_bytes(b'graph')
        elif module.endswith('code_retrieval.retrieval'):
            Path(option('--save_path')).write_text(json.dumps({ident: {'function': {'code_content': 'def function(): pass'}}}))
        elif module.endswith('get_all_cg_parallel'):
            directory = Path(option('--output_dir')) / ident
            directory.mkdir(parents=True)
            (directory / 'call_trees.db').write_bytes(b'db')
            (directory / 'df.json').write_text('[]')
        elif module.endswith('initial_retrieval') or module.endswith('rerank'):
            flag = '--related_tests_path' if module.endswith('initial_retrieval') else '--output_related_tests_path'
            Path(option(flag)).write_text(json.dumps({ident: []}))
        elif module.endswith('llm_query'):
            directory = Path(option('--out_dir'))
            directory.mkdir(parents=True)
            (directory / f'{ident}_n1.txt').write_text('def test_bug(): assert True')
        elif module.endswith('retrieve_test'):
            current = Path(option('--injection_path'))
            draft = Path(option('--gen_test_dir'))
            self.assertIn(draft.name.removeprefix('iteration_'), current.stem)
            directory = Path(option('--output_dir')) / ident
            directory.mkdir(parents=True)
            (directory / 'semantic.csv').write_text('file_path,test_name\n')

    def test_both_repositories_complete_two_refinements_with_isolated_csvs(self):
        self.commands = []
        output = self.root / 'outputs'
        with patch.object(pipeline, 'ROOT', self.root), \
             patch.object(pipeline, 'preflight') as preflight, \
             patch.object(pipeline, 'run_module', side_effect=self.fake_stage):
            pipeline.main(['--model', TEST_MODEL, '--repo', TEST_REPOS[0], '--repo', TEST_REPOS[1],
                           '--dataset-csv', str(self.csv), '--output-root', str(output), '--iterations', '2'])
        self.assertEqual(len(preflight.call_args.args[0]), 2)
        self.assertEqual(len(self.commands), 22)  # 3 code + 2 initial + 3 per refinement, each repo.
        for repo in TEST_REPOS:
            paths = pipeline.artifact_paths(output, 'swt-verified', TEST_MODEL, repo)
            self.assertTrue((paths.tests / 'related_tests_3.json').exists())
            metadata = json.loads(paths.manifest.read_text())
            self.assertEqual(metadata['model'], TEST_MODEL)
            self.assertIsNone(metadata['provider'])
            self.assertNotIn('api_key', metadata)
        ids_paths = {env['SWT_IDS_FILE'] for _, _, env in self.commands}
        self.assertEqual(len(ids_paths), 2)

    def test_missing_artifact_stops_before_dependent_stage(self):
        with patch.object(pipeline, 'run_module') as run:
            with self.assertRaises(FileNotFoundError):
                pipeline.code_retrieval(pipeline.artifact_paths(self.root, 'swt-verified', TEST_MODEL, TEST_REPOS[0]),
                                        self.rows[:1], self.csv, {}, 1, TEST_MODEL)
        self.assertEqual(run.call_count, 1)

    def test_zero_code_matches_are_valid_and_generate_empty_code_context(self):
        ident = self.rows[0]['instance_id']
        path = self.root / 'retrieval_results.json'
        for matches in ({'min-similarity-lines': None, 'R0801': None}, {}):
            with self.subTest(matches=matches):
                path.write_text(json.dumps({ident: matches}))
                output = io.StringIO()
                with redirect_stdout(output):
                    pipeline.check_json(path, self.rows[:1], 'code')
                self.assertIn('no production-code matches for 1/1', output.getvalue())
                self.assertIn(ident, output.getvalue())
                self.assertEqual(get_retrieval_docs(ident, path), '')
                self.assertEqual(json.loads(path.read_text())[ident], matches)

    def test_partial_code_matches_keep_valid_snippets(self):
        ident = self.rows[0]['instance_id']
        path = self.root / 'retrieval_results.json'
        path.write_text(json.dumps({ident: {'R0801': None, 'work': {
            'code_content': 'def work(): pass', 'obj_name': 'work'}}}))
        output = io.StringIO()
        with redirect_stdout(output):
            pipeline.check_json(path, self.rows[:1], 'code')
        self.assertEqual(output.getvalue(), '')
        self.assertIn('def work(): pass', get_retrieval_docs(ident, path))

    def test_missing_and_malformed_artifacts_still_fail_validation(self):
        ident = self.rows[0]['instance_id']
        path = self.root / 'invalid.json'
        for values in ([], {}, {ident: None}, {ident: []}, {ident: {'key': 123}},
                       {ident: {'key': {}}}, {ident: {'key': {'code_content': ' '}}},
                       {ident: {'key': {'code_content': 123}}}):
            with self.subTest(values=values):
                path.write_text(json.dumps(values))
                with self.assertRaisesRegex(RuntimeError, 'Missing or invalid code'):
                    pipeline.check_json(path, self.rows[:1], 'code')
        path.write_text(json.dumps({ident: None}))
        with self.assertRaisesRegex(RuntimeError, 'Missing or invalid keywords'):
            pipeline.check_json(path, self.rows[:1], 'keywords')

    def test_no_code_hit_instance_reaches_test_refinement_without_being_dropped(self):
        self.commands = []
        ident = self.rows[0]['instance_id']
        misses = {'min-similarity-lines': None, 'R0801': None}
        def stage(module, *args, env=None):
            self.fake_stage(module, *args, env=env)
            if module.endswith('code_retrieval.retrieval'):
                path = Path(args[args.index('--save_path') + 1])
                path.write_text(json.dumps({ident: misses}))
        output = self.root / 'outputs'
        with patch.object(pipeline, 'ROOT', self.root), patch.object(pipeline, 'preflight'), \
             patch.object(pipeline, 'run_module', side_effect=stage), redirect_stdout(io.StringIO()):
            pipeline.main(['--model', TEST_MODEL, '--repo', TEST_REPOS[0],
                           '--dataset-csv', str(self.csv), '--output-root', str(output),
                           '--iterations', '1'])
        paths = pipeline.artifact_paths(output, 'swt-verified', TEST_MODEL, TEST_REPOS[0])
        self.assertEqual(json.loads(paths.code.read_text())[ident], misses)
        self.assertIn(ident, json.loads((paths.tests / 'related_tests_2.json').read_text()))
        self.assertTrue(any(module.endswith('llm_query') for module, _, _ in self.commands))

    def test_preflight_only_does_not_run_any_stage_or_write_outputs(self):
        output = self.root / 'outputs'
        with patch.object(pipeline, 'preflight'), patch.object(pipeline, 'run_module') as run:
            pipeline.main(['--model', TEST_MODEL, '--repo', TEST_REPOS[0], '--repo', TEST_REPOS[1],
                           '--dataset-csv', str(self.csv), '--output-root', str(output), '--preflight-only'])
        run.assert_not_called()
        self.assertFalse(output.exists())

    def test_resume_configuration_mismatch_preserves_old_selection(self):
        output = self.root / 'outputs'
        root = pipeline.artifact_paths(output, 'swt-verified', TEST_MODEL, TEST_REPOS[0]).selection
        root.mkdir(parents=True)
        (root / 'selected.csv').write_text('existing selection')
        (root / 'run_config.json').write_text(json.dumps({'model': 'different-model'}))
        with patch.object(pipeline, 'preflight'), patch.object(pipeline, 'run_module') as run:
            with self.assertRaisesRegex(RuntimeError, 'configuration changed'):
                pipeline.main(['--model', TEST_MODEL, '--dataset-csv', str(self.csv), '--output-root', str(output),
                               '--repo', TEST_REPOS[0]])
        run.assert_not_called()
        self.assertEqual((root / 'selected.csv').read_text(), 'existing selection')

    def test_model_uses_qwen_credentials_and_openrouter_default(self):
        with patch.dict(os.environ, {'QWEN_API_KEY': 'test-key'}, clear=True):
            config = runpy.run_path(str(pipeline.ROOT / 'scripts/config.py'))
        self.assertEqual(config['API_KEY']['deepseek/deepseek-v4-flash-0731'], 'test-key')
        self.assertEqual(config['BASE_URL']['deepseek/deepseek-v4-flash-0731'], 'https://openrouter.ai/api/v1')
        self.assertNotIn('DEEPSEEK_OPENROUTER_MODEL', config)
        self.assertEqual(config['API_KEY']['qwen/qwen3.8-27b:free'], 'test-key')

    def test_streamed_tools_preserve_reasoning_and_fragmented_arguments(self):
        extract = load_function(pipeline.ROOT / 'scripts/test_retrieval/initial_retrieval.py',
                                'extract_function_call', {'copy': copy})
        chunks = [{'choices': [{'delta': {
            'reasoning': 'inspect ', 'reasoning_details': [{'type': 'reasoning.text', 'text': 'inspect ', 'index': 0}],
            'tool_calls': [{'index': 0, 'id': 'call_1', 'type': 'function',
                            'function': {'name': 'list_folder', 'arguments': '{"folder":'}}],
        }}]}, {'choices': [{'delta': {
            'reasoning': 'tests', 'reasoning_details': [{'type': 'reasoning.text', 'text': 'tests', 'index': 0}],
            'tool_calls': [{'index': 0, 'function': {'arguments': '"tests"}'}}],
        }}]}]
        original = copy.deepcopy(chunks)
        calls, message = extract(chunks)
        self.assertEqual(json.loads(calls[0]['function']['arguments']), {'folder': 'tests'})
        self.assertEqual(message['reasoning'], 'inspect tests')
        self.assertEqual(len(message['reasoning_details']), 2)
        self.assertEqual(chunks, original)

    def test_provider_and_request_options_are_explicit_and_model_independent(self):
        class Stream(list):
            def close(self):
                pass
        create = unittest.mock.Mock(return_value=Stream([{'chunk': 1}]))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        client.with_options = lambda **kw: client
        completion = load_function(pipeline.ROOT / 'scripts/utils/llm_api.py', 'create_chat_completion', {
            'os': os,
            'request_watchdog': lambda *args: nullcontext(),
            'APIError': RuntimeError, 'CompletionResponseError': ValueError,
        })
        with patch.dict(os.environ, {'ICORE_LLM_PROVIDER': 'test-provider',
                                    'ICORE_LLM_MAX_TOKENS': '1234', 'ICORE_LLM_TIMEOUT': '180'}, clear=True):
            completion(client, model=TEST_MODEL, stream=True, timeout=60)
        request = create.call_args.kwargs
        self.assertEqual(request['extra_body']['provider']['only'], ['test-provider'])
        self.assertTrue(request['extra_body']['provider']['require_parameters'])
        self.assertFalse(request['extra_body']['provider']['allow_fallbacks'])
        self.assertEqual(request['max_tokens'], 1234)
        self.assertEqual(request['timeout'], 180)
        with patch.dict(os.environ, {}, clear=True):
            completion(client, model=TEST_MODEL, stream=True, timeout=60)
        self.assertNotIn('extra_body', create.call_args.kwargs)
        self.assertEqual(create.call_args.kwargs['timeout'], 60)

    def test_new_repository_and_different_models_use_separate_outputs_and_caches(self):
        repo = 'custom-owner/new-project'
        write_csv(self.csv, [dict(self.rows[0], repo=repo, instance_id='custom-owner__new-project-1')])
        self.commands = []
        output = self.root / 'outputs'
        with patch.object(pipeline, 'ROOT', self.root), patch.object(pipeline, 'preflight'), \
             patch.object(pipeline, 'run_module', side_effect=self.fake_stage):
            for model in (TEST_MODEL, 'other/model-b'):
                pipeline.main(['--model', model, '--repo', repo, '--dataset-csv', str(self.csv),
                               '--output-root', str(output), '--iterations', '1',
                               '--provider', 'test-provider', '--max-tokens', '1234', '--timeout', '180'])
        drafts = [args for module, args, _ in self.commands if module.endswith('llm_query')]
        names = [args[args.index('--exp_name') + 1] for args in drafts]
        self.assertEqual(len(set(names)), 2)
        for model in (TEST_MODEL, 'other/model-b'):
            self.assertTrue((pipeline.artifact_paths(output, 'swt-verified', model, repo).tests / 'related_tests_2.json').exists())
        for _, _, env in self.commands:
            self.assertEqual(env['ICORE_LLM_PROVIDER'], 'test-provider')
            self.assertEqual(env['ICORE_LLM_MAX_TOKENS'], '1234')
            self.assertEqual(env['ICORE_LLM_TIMEOUT'], '180')

    def test_no_implicit_model_or_repo_and_invalid_repo_is_rejected(self):
        for args in ([], ['--model', TEST_MODEL], ['--model', TEST_MODEL, '--repo', '../..']):
            with self.subTest(args=args), patch('sys.stderr'), self.assertRaises(SystemExit) as exc:
                pipeline.main(args)
            self.assertEqual(exc.exception.code, 2)

    def test_cli_does_not_inherit_another_runs_provider_options(self):
        self.commands = []
        with patch.dict(os.environ, {'ICORE_LLM_PROVIDER': 'stale-provider', 'ICORE_LLM_TIMEOUT': '999'}), \
             patch.object(pipeline, 'ROOT', self.root), patch.object(pipeline, 'preflight'), \
             patch.object(pipeline, 'run_module', side_effect=self.fake_stage):
            pipeline.main(['--model', TEST_MODEL, '--repo', TEST_REPOS[0], '--dataset-csv', str(self.csv),
                           '--output-root', str(self.root / 'outputs'), '--stage', 'code'])
        for _, _, env in self.commands:
            self.assertNotIn('ICORE_LLM_PROVIDER', env)
            self.assertNotIn('ICORE_LLM_TIMEOUT', env)

    def test_timeout_can_change_when_resuming_the_same_experiment(self):
        self.commands = []
        output = self.root / 'outputs'
        with patch.object(pipeline, 'ROOT', self.root), patch.object(pipeline, 'preflight'), \
             patch.object(pipeline, 'run_module', side_effect=self.fake_stage):
            for timeout in ('300', '180'):
                pipeline.main(['--model', TEST_MODEL, '--repo', TEST_REPOS[0],
                               '--dataset-csv', str(self.csv), '--output-root', str(output),
                               '--stage', 'code', '--timeout', timeout])
        paths = pipeline.artifact_paths(output, 'swt-verified', TEST_MODEL, TEST_REPOS[0])
        self.assertEqual(json.loads(paths.manifest.read_text())['timeout'], 180)
        self.assertTrue(paths.code.is_file())
        self.assertEqual(self.commands[-1][2]['ICORE_LLM_TIMEOUT'], '180')

    def test_artifact_layout_preserves_original_categories_and_full_repository_identity(self):
        paths = pipeline.artifact_paths(self.root, 'swt-verified', 'deepseek/model:variant', 'owner/project')
        model_root = self.root / 'swt-bench-verified/deepseek%2Fmodel%3Avariant'
        self.assertEqual(paths.model_root, model_root)
        self.assertEqual(paths.code, model_root / 'code/owner/project/retrieval_results.json')
        self.assertEqual(paths.keywords, model_root / 'code/owner/project/keywords.json')
        self.assertEqual(paths.tests, model_root / 'test/owner/project')
        self.assertEqual(paths.graphs, model_root / 'graphs/owner/project')
        self.assertEqual(paths.trees, model_root / 'swe_test_cgs/owner/project')
        self.assertEqual(paths.manifest, model_root / 'selections/owner/project/run_config.json')
        other_owner = pipeline.artifact_paths(self.root, 'swt-verified', 'deepseek/model:variant', 'other/project')
        self.assertNotEqual(paths.tests, other_owner.tests)
        self.assertEqual(pipeline.DEFAULT_OUTPUT, pipeline.ROOT / 'retrieval_results')

    def test_two_benchmarks_and_two_models_are_isolated_end_to_end(self):
        self.commands = []
        output = self.root / 'outputs'
        expected = []
        with patch.object(pipeline, 'ROOT', self.root), patch.object(pipeline, 'preflight'), \
             patch.object(pipeline, 'run_module', side_effect=self.fake_stage):
            for benchmark in ('swt-verified', 'lite'):
                rows = self.rows[:1] if benchmark == 'swt-verified' else [
                    {key: value for key, value in self.rows[0].items() if key != 'source_dataset'}]
                write_csv(self.csv, rows)
                for model in (TEST_MODEL, 'other/model-b'):
                    pipeline.main(['--benchmark', benchmark, '--model', model, '--repo', TEST_REPOS[0],
                                   '--dataset-csv', str(self.csv), '--output-root', str(output), '--iterations', '1'])
                    paths = pipeline.artifact_paths(output, benchmark, model, TEST_REPOS[0])
                    expected.append(paths.model_root)
                    self.assertTrue((paths.tests / 'related_tests_2.json').exists())
                    metadata = json.loads(paths.manifest.read_text())
                    self.assertEqual(metadata['benchmark'], pipeline.BENCHMARKS[benchmark].folder)
                    self.assertEqual(metadata['source_dataset'], pipeline.BENCHMARKS[benchmark].source_dataset)
        self.assertEqual(len(set(expected)), 4)
        drafts = [args for module, args, _ in self.commands if module.endswith('llm_query')]
        names = [args[args.index('--exp_name') + 1] for args in drafts]
        self.assertEqual(len(set(names)), 4)

    def test_default_csv_follows_selected_benchmark_without_exporting_the_other(self):
        for benchmark in ('swt-verified', 'lite'):
            with self.subTest(benchmark=benchmark):
                folder = self.root / 'data' / pipeline.BENCHMARKS[benchmark].folder
                folder.mkdir(parents=True)
                rows = self.rows[:1] if benchmark == 'swt-verified' else [
                    {key: value for key, value in self.rows[0].items() if key != 'source_dataset'}]
                write_csv(folder / 'test.csv', rows)
                with patch.object(pipeline, 'ROOT', self.root), \
                     patch.object(pipeline, 'preflight') as preflight, patch.object(pipeline, 'run_module') as run:
                    pipeline.main(['--benchmark', benchmark, '--model', TEST_MODEL, '--repo', TEST_REPOS[0],
                                   '--preflight-only'])
                run.assert_not_called()
                self.assertEqual(preflight.call_args.args[0], rows)

    def test_benchmark_aliases_and_wrong_csv_are_rejected(self):
        self.assertEqual(pipeline.benchmark_argument('swt-bench-verified'), 'swt-verified')
        self.assertEqual(pipeline.benchmark_argument('SWE-bench/SWE-bench_Lite'), 'lite')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            pipeline.select_rows(self.csv, TEST_REPOS[0], 'lite')


if __name__ == '__main__':
    unittest.main()
