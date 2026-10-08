import ast
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scripts.test_retrieval.response_parser import ensure_test_selection


ROOT = Path(__file__).resolve().parents[1]
SELECTION = '[["tests/test_one.py", "TestOne.test_one"]]'


def load_functions(path, names, namespace):
    # Exercise production code without the server-only OpenAI/graph dependencies.
    tree = ast.parse(path.read_text(encoding='utf-8'))
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)


def chunks(content):
    data = json.dumps({'choices': [{'delta': {'content': content}}]})
    return [SimpleNamespace(model_dump_json=lambda: data)]


class SelectionRecoveryTests(unittest.TestCase):
    def test_valid_reply_makes_no_request(self):
        history = [{'role': 'assistant', 'content': SELECTION}]
        request, save = Mock(), Mock()
        self.assertEqual(ensure_test_selection(history, request, save),
                         [['tests/test_one.py', 'TestOne.test_one']])
        request.assert_not_called()

    def test_repair_preserves_invalid_reply_and_tool_evidence(self):
        history = [{'role': 'tool', 'tool_call_id': 'call_1', 'content': 'test_one'},
                   {'role': 'assistant', 'content': 'Try TestOne.test_one in tests/test_one.py.'}]
        original = copy.deepcopy(history)
        snapshots = []
        request = Mock(return_value={'role': 'assistant', 'content': SELECTION})
        with self.assertLogs(level='WARNING'):
            selected = ensure_test_selection(
                history, request, lambda items: snapshots.append(copy.deepcopy(items)), topk=3)
        self.assertEqual(selected, [['tests/test_one.py', 'TestOne.test_one']])
        self.assertEqual(history[:2], original)
        self.assertIn('at most 3 pairs', history[-2]['content'])
        self.assertEqual(snapshots[0], original)
        self.assertEqual(snapshots[-1], history)
        self.assertEqual(request.call_count, 1)

    def test_exhausted_repairs_raise_and_checkpoint_all_replies(self):
        history = [{'role': 'assistant', 'content': None}]
        snapshots = []
        request = Mock(return_value={'role': 'assistant', 'content': 'Still no list.'})
        with self.assertLogs(level='WARNING'), self.assertRaisesRegex(ValueError, 'after 2 formatting retries'):
            ensure_test_selection(history, request,
                                  lambda items: snapshots.append(copy.deepcopy(items)))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(history), 5)
        self.assertIsNone(history[0]['content'])
        self.assertEqual(snapshots[-1], history)

    def test_timeout_keeps_original_checkpoint_and_propagates(self):
        history = [{'role': 'assistant', 'content': 'Malformed.'}]
        snapshots = []
        request = Mock(side_effect=TimeoutError('deadline'))
        with self.assertLogs(level='WARNING'), self.assertRaises(TimeoutError):
            ensure_test_selection(history, request,
                                  lambda items: snapshots.append(copy.deepcopy(items)))
        self.assertEqual(snapshots, [[{'role': 'assistant', 'content': 'Malformed.'}]])
        self.assertEqual(request.call_count, 1)

    def test_only_explicit_model_empty_selection_is_accepted(self):
        history = [{'role': 'assistant', 'content': 'No tests'}]
        request = Mock(return_value={'role': 'assistant', 'content': '[]'})
        with self.assertLogs(level='WARNING'):
            self.assertEqual(ensure_test_selection(history, request, Mock()), [])
        self.assertEqual(request.call_count, 1)


class RetrievalRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'instance.json'
        self.instance = {'instance_id': 'repo-123', 'problem_statement': 'bug', 'repo': 'owner/repo'}

    def load_chat(self, stage, replies):
        client = Mock()
        create = Mock(side_effect=[chunks(reply) for reply in replies])
        namespace = {
            'copy': copy, 'json': json, 'os': os,
            'ensure_test_selection': ensure_test_selection,
            'create_chat_completion': create, 'OpenAI': Mock(return_value=client),
            'API_KEY': {'vendor/model': 'test-key'}, 'BASE_URL': {'vendor/model': 'https://example.invalid'},
            'FunctionCalls': Mock(), 'get_tools': Mock(return_value=[{'type': 'function'}]),
            'list_candidates': Mock(return_value=[]), 'system_prompt': 'Select tests.',
        }
        initial = ROOT / 'scripts/test_retrieval/initial_retrieval.py'
        load_functions(initial, ['extract_function_call', 'finalize_test_selection'], namespace)
        load_functions(ROOT / f'scripts/test_retrieval/{stage}.py', ['chat_with_llm'], namespace)

        def run():
            args = [self.instance, 'vendor/model', str(self.path)]
            if stage == 'rerank':
                args.extend(['similarity', 'previous.json', False, 3])
            namespace['chat_with_llm'](*args)
        return run, namespace, client, create

    def test_cached_invalid_reply_is_repaired_in_both_stages(self):
        for stage in ('initial_retrieval', 'rerank'):
            with self.subTest(stage=stage):
                self.path.write_text(json.dumps([{'role': 'assistant', 'content': 'Malformed.'}]))
                run, namespace, client, create = self.load_chat(stage, [SELECTION])
                with self.assertLogs(level='WARNING'):
                    run()
                history = json.loads(self.path.read_text())
                self.assertEqual(history[0]['content'], 'Malformed.')
                self.assertEqual(history[-1]['content'], SELECTION)
                self.assertEqual(create.call_count, 1)
                self.assertNotIn('tools', create.call_args.kwargs)
                self.assertEqual(create.call_args.kwargs['model'], 'vendor/model')
                namespace['get_tools'].assert_not_called()
                namespace['list_candidates'].assert_not_called()
                client.close.assert_called_once()

    def test_cached_valid_reply_is_reused_in_both_stages(self):
        for stage in ('initial_retrieval', 'rerank'):
            with self.subTest(stage=stage):
                self.path.write_text(json.dumps([{'role': 'assistant', 'content': SELECTION}]))
                run, _, client, create = self.load_chat(stage, [])
                run()
                create.assert_not_called()
                client.close.assert_called_once()

    def test_new_invalid_reply_is_repaired_in_both_stages(self):
        for stage in ('initial_retrieval', 'rerank'):
            with self.subTest(stage=stage):
                if self.path.exists():
                    self.path.unlink()
                run, _, client, create = self.load_chat(stage, ['Malformed.', SELECTION])
                with self.assertLogs(level='WARNING'):
                    run()
                history = json.loads(self.path.read_text())
                self.assertEqual(history[2]['content'], 'Malformed.')
                self.assertEqual(history[-1]['content'], SELECTION)
                self.assertEqual(create.call_count, 2)
                self.assertIn('tools', create.call_args_list[0].kwargs)
                self.assertNotIn('tools', create.call_args_list[1].kwargs)
                client.close.assert_called_once()

    def test_failure_reports_instance_and_retains_history_in_both_stages(self):
        for stage in ('initial_retrieval', 'rerank'):
            with self.subTest(stage=stage):
                self.path.write_text(json.dumps([{'role': 'assistant', 'content': 'Malformed.'}]))
                run, _, client, create = self.load_chat(stage, ['Still malformed.'] * 2)
                with self.assertLogs(level='WARNING'), self.assertRaises(ValueError) as error:
                    run()
                self.assertIn('repo-123', str(error.exception))
                self.assertIn(str(self.path), str(error.exception))
                self.assertEqual(create.call_count, 2)
                self.assertEqual(len(json.loads(self.path.read_text())), 5)
                client.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
