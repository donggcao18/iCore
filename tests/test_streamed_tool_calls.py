import ast
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scripts.test_retrieval.tool_call_utils import InvalidToolCall, load_tool_history, validate_tool_calls


ROOT = Path(__file__).resolve().parents[1]


class StreamedToolTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / 'scripts/test_retrieval/initial_retrieval.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        self.create = Mock()
        namespace = dict(copy=copy, json=json, create_chat_completion=self.create,
                         InvalidToolCall=InvalidToolCall, validate_tool_calls=validate_tool_calls)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in ('extract_function_call', 'request_tool_message')]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
        self.extract = namespace['extract_function_call']
        self.request = namespace['request_tool_message']

    def delta(self, call):
        return {'choices': [{'delta': {'tool_calls': [call]}}]}

    def test_empty_continuation_does_not_erase_function_name(self):
        chunks = [self.delta({'index': 0, 'id': 'call_1', 'type': 'function',
                              'function': {'name': 'list_root', 'arguments': '{'}}),
                  self.delta({'index': 0, 'function': {'name': '', 'arguments': '}'}})]
        original = copy.deepcopy(chunks)
        calls, message = self.extract(chunks)
        self.assertEqual(calls[0]['function'], {'name': 'list_root', 'arguments': '{}'})
        validate_tool_calls(calls)
        self.assertEqual(message['tool_calls'], calls)
        self.assertEqual(chunks, original)

    def test_interleaved_calls_fragments_late_headers_and_repeated_name(self):
        chunks = [self.delta({'index': 1, 'function': {'name': 'list_', 'arguments': '{'}}),
                  self.delta({'index': 0, 'id': 'call_0', 'type': 'function',
                              'function': {'name': 'list_root', 'arguments': '{}'}}),
                  self.delta({'index': 1, 'id': 'call_1', 'type': 'function',
                              'function': {'name': 'folder', 'arguments': '"folder":"tests"}'}}),
                  self.delta({'index': 1, 'id': None, 'type': None,
                              'function': {'name': 'list_folder', 'arguments': ''}})]
        calls, _ = self.extract(chunks)
        self.assertEqual([c['id'] for c in calls], ['call_0', 'call_1'])
        self.assertEqual(calls[1]['function']['name'], 'list_folder')
        validate_tool_calls(calls)

    def test_genuinely_missing_name_is_bounded_and_never_modifies_history(self):
        malformed = self.delta({'index': 0, 'id': 'call_1', 'type': 'function',
                                'function': {'name': '', 'arguments': '{}'}})
        data = json.dumps(malformed)
        self.create.side_effect = [[SimpleNamespace(model_dump_json=lambda: data)] for _ in range(3)]
        history = [{'role': 'user', 'content': 'bug'}]
        with self.assertRaisesRegex(InvalidToolCall, 'after 3 attempts'):
            self.request(Mock(), messages=history, stream=True)
        self.assertEqual(self.create.call_count, 3)
        self.assertEqual(history, [{'role': 'user', 'content': 'bug'}])

    def test_plain_reasoning_fragments_join_without_losing_block_boundaries(self):
        fragments = [('We', 0), (' are', 0), (' testing.', 0), ('Next block.', 1)]
        chunks = [{'choices': [{'delta': {
            'reasoning': text,
            'reasoning_details': [{'type': 'reasoning.text', 'text': text,
                                   'format': 'unknown', 'index': index}],
        }}]} for text, index in fragments]
        original = copy.deepcopy(chunks)
        _, message = self.extract(chunks)
        self.assertEqual(message['reasoning'], 'We are testing.Next block.')
        self.assertEqual(message['reasoning_details'], [
            {'type': 'reasoning.text', 'text': 'We are testing.', 'format': 'unknown', 'index': 0},
            {'type': 'reasoning.text', 'text': 'Next block.', 'format': 'unknown', 'index': 1},
        ])
        self.assertEqual(chunks, original)

    def test_special_reasoning_payloads_and_signed_sequences_are_preserved(self):
        plain = {'type': 'reasoning.text', 'text': 'We', 'format': 'unknown', 'index': 0}
        continuation = dict(plain, text=' are')
        special = [dict(plain, text='', signature='opaque-signature'),
                   {'type': 'reasoning.encrypted', 'data': 'opaque-payload', 'index': 1},
                   dict(plain, format='anthropic-claude-v1'),
                   dict(plain, id='reasoning-id'), dict(plain, provider_metadata='opaque')]
        for payload in special:
            with self.subTest(payload=payload):
                details = [plain, continuation, payload]
                chunks = [{'choices': [{'delta': {'reasoning_details': [detail]}}]}
                          for detail in details]
                _, message = self.extract(chunks)
                self.assertEqual(message['reasoning_details'], details)

    def test_malformed_ids_and_arguments_are_rejected(self):
        good = {'id': 'call_1', 'type': 'function',
                'function': {'name': 'list_root', 'arguments': '{}'}}
        for bad in (dict(good, id=''), dict(good, type=None),
                    dict(good, function={'name': ' ', 'arguments': '{}'}),
                    dict(good, function={'name': 'list_root', 'arguments': '{'}),
                    dict(good, function={'name': 'list_root', 'arguments': '[]'})):
            with self.subTest(call=bad), self.assertRaises(InvalidToolCall):
                validate_tool_calls([bad])
        with self.assertRaises(InvalidToolCall):
            validate_tool_calls([good, good])

    def test_recovery_preserves_prior_backups_and_leaves_valid_history_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'instance.json'
            valid = [{'role': 'user', 'content': 'bug'}]
            bad = valid + [{'role': 'assistant', 'tool_calls': [{'id': 'bad', 'type': 'function',
                            'function': {'name': '', 'arguments': '{}'}}]}]
            backup = Path(directory) / 'instance.invalid_tool_calls.json'
            backup.write_text('earlier evidence')
            original = json.dumps(bad).encode()
            path.write_bytes(original)
            with self.assertLogs(level='WARNING'):
                self.assertEqual(load_tool_history(path), valid)
            self.assertEqual(backup.read_text(), 'earlier evidence')
            self.assertEqual(backup.with_name('instance.invalid_tool_calls.1.json').read_bytes(), original)
            unchanged = path.read_bytes()
            self.assertEqual(load_tool_history(path), valid)
            self.assertEqual(path.read_bytes(), unchanged)


if __name__ == '__main__':
    unittest.main()
