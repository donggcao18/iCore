import unittest

from scripts.test_retrieval.response_parser import parse_test_selection


class TestSelectionParser(unittest.TestCase):
    def test_supported_formats(self):
        expected = [['tests/test_one.py', 'TestCase.test_one']]
        for text in (
            '[["tests/test_one.py", "TestCase.test_one"]]',
            'Here are the tests:\n[["tests/test_one.py", "TestCase.test_one"]]\nExplanation follows.',
            '```json\n[["tests/test_one.py", "TestCase.test_one"]]\n```\nExtra text',
            "```python\n[\n ['tests/test_one.py', 'TestCase.test_one'], # comment with ]\n]\n```",
            '<think>Consider []</think>[["tests/test_one.py", "TestCase.test_one"]]',
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_test_selection(text), expected)

    def test_brackets_and_hash_inside_strings(self):
        self.assertEqual(
            parse_test_selection('[["tests/test_one.py", "test_one[x#y]"]]\nDone.'),
            [['tests/test_one.py', 'test_one[x#y]']],
        )

    def test_empty_selection(self):
        self.assertEqual(parse_test_selection('[]'), [])

    def test_invalid_or_ambiguous_output(self):
        for text in (None, '', 'No tests', '[1, 2]', '[["only-one"]]',
                     '[["a", null]]', '[["a", "b"]',
                     '[["a", "b"]]\n[["c", "d"]]',
                     '[["a", __import__("os").getcwd()]]'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_test_selection(text)


if __name__ == '__main__':
    unittest.main()
