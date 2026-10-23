import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts import analyze_brt_generations as analysis


class AnalyzeBrtGenerationsTests(unittest.TestCase):
    def test_missing_second_preserves_first_and_reports_none(self):
        rows = analysis.compare({'a': {'a_n1.txt': {'success': True}},
                                 'b': {'b_n1.txt': '[error] SyntaxError'}}, {})
        self.assertEqual(rows, [dict(instance_id='a', first_gen=True, second_gen=None, final_result=True),
                                dict(instance_id='b', first_gen=False, second_gen=None, final_result=False)])
        self.assertEqual(analysis.statistics(rows)['second_gen'],
                         dict(f2p=0, evaluated=0, none=2, rate=None))

    def test_replacement_can_improve_or_regress(self):
        first = {'a': {'a_n1.txt': {'success': False}}, 'b': {'b_n1.txt': {'success': True}}}
        second = {'a': {'a_n1.txt': {'success': True}}, 'b': {'b_n1.txt': {'success': False}}}
        rows = analysis.compare(first, second)
        self.assertEqual([row['final_result'] for row in rows], [True, False])
        self.assertEqual(analysis.statistics(rows)['gained'], 1)
        self.assertEqual(analysis.statistics(rows)['lost'], 1)

    def test_partial_candidate_replacement_keeps_other_originals(self):
        first = {'a': {'a_n1.txt': {'success': True}, 'a_n2.txt': {'success': False}}}
        second = {'a': {'a_n2.txt': {'success': False}}}
        self.assertEqual(analysis.compare(first, second),
                         [dict(instance_id='a', first_gen=True, second_gen=False, final_result=True)])

    def test_alias_filenames_csv_none_and_evaluated_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'second_generation').mkdir()
            (root / 'execution__result.json').write_text(json.dumps({
                'a': {'a_n1.txt': {'success': False}}, 'b': {'b_n1.txt': {'success': True}}}), encoding='utf-8')
            (root / 'second_generation/execution_result.json').write_text(json.dumps({
                'a': {'a_n1.txt': {'success': True}}}), encoding='utf-8')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                analysis.main([str(root)])
            with (root / 'generation_comparison.csv').open(newline='', encoding='utf-8') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[1]['second_gen'], 'None')
            self.assertEqual(rows[0]['final_result'], 'True')
            self.assertIn('second_gen: F2P 1/1 (100.0%); None=1', output.getvalue())
            self.assertIn('final_result: F2P 2/2 (100.0%)', output.getvalue())

    def test_missing_first_is_an_error_missing_second_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(analysis.load_results(root), ({}, None))
            with self.assertRaises(FileNotFoundError):
                analysis.load_results(root, required=True)


if __name__ == '__main__':
    unittest.main()
