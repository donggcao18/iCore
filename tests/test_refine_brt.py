import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import refine_brt as refine
from scripts.run_retrieval import DATASET


class RefineBrtTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tests = self.root / 'first_tests'
        self.tests.mkdir()
        self.rows = [dict(repo='scikit-learn/scikit-learn', version='0.22',
                          instance_id=f'scikit-learn__scikit-learn-{i}',
                          problem_statement='The expected result is 2.',
                          patch='SECRET_PRODUCTION_PATCH', test_patch='SECRET_TEST_PATCH')
                     for i in range(1, 4)]
        self.results, self.contexts = {}, {}
        for row in self.rows:
            bug_id = row['instance_id']
            name = bug_id + '_n1.txt'
            (self.tests / name).write_text('def test_bug(): assert target() == 1', encoding='utf-8')
            self.results[bug_id] = {name: {
                'buggy': {'compile_error': False, 'runtime_error': False,
                          'autogen_failed': True, 'failed_tests': ['test_bug'],
                          'fib_error_msg': ['AssertionError: expected 1'],
                          'unapproved': 'SECRET_NESTED_FIELD'},
                'fixed': {'fib_error_msg': ['SECRET_FIXED_RESULT']}, 'success': 'SECRET_SUCCESS'}}
            self.contexts[bug_id] = [dict(file='tests/test_api.py', name='test_api',
                                          code_content='def test_api(): pass',
                                          extra='SECRET_ORACLE_METADATA')]
        self.names = [row['instance_id'] + '_n1.txt' for row in self.rows]

    def tasks(self):
        return refine.plan(self.rows, self.results, self.tests, self.contexts)[0]

    def test_only_allowlisted_buggy_info_enters_generation(self):
        tasks = self.tasks()
        serialized = json.dumps(tasks)
        self.assertNotIn('SECRET', serialized)
        changed = copy.deepcopy(self.results)
        for candidates in changed.values():
            for result in candidates.values():
                result['fixed'] = {'compile_error': True, 'fib_error_msg': ['OTHER_SECRET']}
                result['success'] = False
        self.assertEqual(tasks, refine.plan(self.rows, changed, self.tests, self.contexts)[0])

    def test_fixed_and_success_are_never_accessed(self):
        class BuggyOnly(dict):
            def get(self, key, *args):
                if key != 'buggy':
                    raise AssertionError(f'Forbidden access: {key}')
                return super().get(key, *args)
        for candidates in self.results.values():
            for name in candidates:
                candidates[name] = BuggyOnly(candidates[name])
        self.assertEqual(len(self.tasks()), 3)

    def test_runtime_compile_repair_and_assertion_review_routes(self):
        tasks = self.tasks()
        calls = []
        def request(root, name, phase, system, payload, model):
            calls.append((name, phase, payload))
            if phase == 'repair':
                return 'def test_bug(): assert target() == 2'
            return json.dumps({'matches_issue': name == self.names[1], 'reason': 'issue review'})
        for kind in ('runtime_error', 'compile_error'):
            with self.subTest(kind=kind):
                calls.clear()
                tasks[self.names[0]]['buggy'][kind] = True
                with patch.object(refine, 'request', side_effect=request):
                    decisions = refine.generate(tasks, self.root, 'openai/gpt-6-luna')
                self.assertEqual([(name, phase) for name, phase, _ in calls], [
                    (self.names[0], 'repair'), (self.names[1], 'review'),
                    (self.names[2], 'review'), (self.names[2], 'repair')])
                self.assertEqual(decisions[self.names[1]]['action'], 'keep')
                self.assertEqual({p.name for p in (self.root / 'generated_tests').glob('*.txt')},
                                 {self.names[0], self.names[2]})
                tasks[self.names[0]]['buggy'][kind] = False

    def test_no_buggy_failure_regenerates_without_assertion_review(self):
        task = self.tasks()[self.names[0]]
        task['buggy'].update(autogen_failed=False, failed_tests=[], fib_error_msg=[])
        replacement = 'def test_bug(): assert target() == 2'
        with patch.object(refine, 'request', return_value=replacement) as request:
            decisions = refine.generate({self.names[0]: task}, self.root, 'openai/gpt-6-luna')
        request.assert_called_once()
        self.assertEqual(request.call_args.args[2], 'repair')
        self.assertIn('must fail on buggy code', request.call_args.args[3])
        self.assertIn('autogen_failed is false', request.call_args.args[4]['review']['reason'])
        self.assertEqual(decisions[self.names[0]]['action'], 'regenerate')
        self.assertEqual((self.root / 'generated_tests' / self.names[0]).read_text().strip(), replacement)

    def test_missing_buggy_is_skipped_without_using_fixed(self):
        self.results[self.rows[0]['instance_id']][self.names[0]] = {'fixed': {'runtime_error': False}}
        tasks, skipped = refine.plan(self.rows, self.results, self.tests, self.contexts)
        self.assertNotIn(self.names[0], tasks)
        self.assertEqual(skipped, [self.names[0]])

    def test_invalid_review_does_not_regenerate(self):
        with patch.object(refine, 'request', return_value='{"matches_issue": "no", "reason": "bad schema"}'):
            with self.assertRaisesRegex(ValueError, 'Invalid review'):
                refine.generate(self.tasks(), self.root, 'openai/gpt-6-luna')
        self.assertFalse(list((self.root / 'generated_tests').glob('*.txt')))

    def test_evaluation_only_selects_regenerated_candidates(self):
        generated = self.root / 'generated_tests'
        generated.mkdir()
        (generated / self.names[2]).write_text('def test_bug(): assert target() == 2', encoding='utf-8')
        decisions = {name: {'action': 'regenerate' if name == self.names[2] else 'keep'} for name in self.names}
        with patch.object(refine, 'prepare_selection', return_value=(self.root / 'selected.csv', {})) as select, \
                patch.object(refine, 'run_module') as run:
            refine.evaluate(self.rows, decisions, self.root, self.root / 'oracle.json', 'openai/gpt-6-luna')
        self.assertEqual(select.call_args.args[1], [self.rows[2]])
        self.assertEqual(run.call_args.args[0], 'scripts.libro.postprocess_swe')
        self.assertNotIn('--samples', run.call_args.args)
        self.assertEqual(run.call_args.args[-1], self.root / 'execution_results.json')

    def test_cached_replies_resume_without_api_calls(self):
        responses = self.root / 'responses'
        responses.mkdir()
        (responses / 'candidate.review.txt').write_text('{"matches_issue": true, "reason": "OK"}', encoding='utf-8')
        result = refine.request(self.root, 'candidate', 'review', refine.REVIEW,
                                next(iter(self.tasks().values())), 'openai/gpt-6-luna')
        self.assertTrue(json.loads(result)['matches_issue'])
        self.assertNotIn('SECRET', (self.root / 'prompts/candidate.review.json').read_text())

    def test_main_rejects_changed_buggy_but_ignores_changed_fixed(self):
        csv_path, source, oracle = self.root / 'rows.csv', self.root / 'results.json', self.root / 'oracle.json'
        rows = [dict(row, source_dataset=DATASET,
                     test_patch='diff --git a/test.py b/test.py\n') for row in self.rows]
        with csv_path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        oracle.write_text(json.dumps(self.contexts), encoding='utf-8')
        source.write_text(json.dumps(self.results), encoding='utf-8')
        argv = ['--source-results', str(source), '--source-tests', str(self.tests),
                '--dataset-csv', str(csv_path), '--oracle', str(oracle),
                '--output-dir', str(self.root / 'round2'), '--stage', 'generate']
        with patch.object(refine, 'request', return_value='{"matches_issue": true, "reason": "OK"}'):
            refine.main(argv)
            self.results[self.rows[0]['instance_id']][self.names[0]]['fixed'] = 'CHANGED_FIXED'
            source.write_text(json.dumps(self.results), encoding='utf-8')
            refine.main(argv)
            self.results[self.rows[0]['instance_id']][self.names[0]]['buggy']['fib_error_msg'] = ['changed buggy']
            source.write_text(json.dumps(self.results), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'inputs changed'):
                refine.main(argv)


if __name__ == '__main__':
    unittest.main()
