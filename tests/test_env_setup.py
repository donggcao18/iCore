import csv
import os
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.env_setup import env_setup as setup
from scripts.utils.swe_util import get_env_name


class EnvironmentSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = [
            dict(repo='pylint-dev/pylint', version='3.0', instance_id='pylint-1'),
            dict(repo='pylint-dev/pylint', version='3.0', instance_id='pylint-2'),
            dict(repo='pytest-dev/pytest', version='4.5', instance_id='pytest-1'),
        ]

    def test_local_csv_selects_repositories_without_inheriting_retrieval_ids(self):
        csv_path = self.root / 'test.csv'
        with csv_path.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        with patch.dict(os.environ, {'SWT_IDS_FILE': str(self.root / 'missing.txt')}):
            self.assertEqual(setup.selected_instances(csv_path, ['pylint-dev/pylint']), self.rows[:2])
        with self.assertRaisesRegex(ValueError, 'owner/missing'):
            setup.selected_instances(csv_path, ['owner/missing'])

    def test_builds_each_required_version_once_and_verifies_python(self):
        make_spec = Mock(side_effect=lambda row: SimpleNamespace(env_script=f'echo {row["instance_id"]}\n'))
        with patch.dict(sys.modules, {'scripts.env_setup.exec_spec': SimpleNamespace(make_exec_spec=make_spec)}), \
             patch.object(setup, 'clone_repo') as clone, \
             patch.object(setup, 'get_conda_python', side_effect=[
                 FileNotFoundError(), '/conda/pylint/bin/python',
                 FileNotFoundError(), '/conda/pytest/bin/python']) as python, \
             patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            setup.setup_environments(self.rows, self.root)
        self.assertEqual(make_spec.call_count, 2)
        self.assertEqual(clone.call_count, 2)
        self.assertEqual(run.call_count, 2)
        self.assertEqual([call.args[0] for call in python.call_args_list], [
            'setup_pylint-dev_pylint__3.0', 'setup_pylint-dev_pylint__3.0',
            'setup_pytest-dev_pytest__4.5', 'setup_pytest-dev_pytest__4.5'])
        for row in (self.rows[0], self.rows[2]):
            directory = self.root / get_env_name(row)
            self.assertTrue((directory / 'complete.txt').is_file())
            self.assertTrue((directory / 'setup.log').is_file())
        # Setup never resets or cleans an existing source checkout.
        self.assertTrue(all(call.args[0][0] == 'bash' for call in run.call_args_list))

    def test_existing_environments_are_reused(self):
        with patch.object(setup, 'get_conda_python', return_value='/conda/env/bin/python'), \
             patch.object(setup, 'clone_repo'), patch.object(setup.subprocess, 'run') as run:
            setup.setup_environments(self.rows, self.root)
        run.assert_not_called()

    def test_failed_installation_stops_and_is_not_reused_as_complete(self):
        env_name = get_env_name(self.rows[0])
        make_spec = Mock(return_value=SimpleNamespace(env_script='exit 1\n'))
        with patch.dict(sys.modules, {'scripts.env_setup.exec_spec': SimpleNamespace(make_exec_spec=make_spec)}), \
             patch.object(setup, 'clone_repo'), \
             patch.object(setup, 'get_conda_python', side_effect=FileNotFoundError()), \
             patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaisesRegex(RuntimeError, 'Setup failed'):
                setup.setup_environments(self.rows, self.root)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.root / env_name / 'complete.txt').exists())
        with patch.object(setup, 'get_conda_python', return_value='/conda/partial/bin/python'), \
             patch.object(setup, 'clone_repo') as clone, patch.object(setup.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Previous setup did not complete'):
                setup.setup_environments(self.rows, self.root)
        clone.assert_not_called()
        run.assert_not_called()

    def test_conda_unavailable_stops_before_cloning(self):
        with patch.object(setup, 'get_conda_python', side_effect=RuntimeError('Cannot locate Conda')), \
             patch.object(setup, 'clone_repo') as clone:
            with self.assertRaisesRegex(RuntimeError, 'Cannot locate Conda'):
                setup.setup_environments(self.rows, self.root)
        clone.assert_not_called()

    def test_generated_scripts_use_current_conda_and_benchmark_dependencies(self):
        utils = SimpleNamespace(get_requirements_by_commit=lambda *args: 'pytest\n',
                                get_environment_yml_by_commit=Mock(), extract_changed_files=lambda patch: [])
        with patch.dict(sys.modules, {'scripts.env_setup.utils': utils}):
            namespace = runpy.run_path(str(setup.ROOT / 'scripts/env_setup/exec_spec.py'))
            for row in (self.rows[0], self.rows[2]):
                spec = namespace['make_exec_spec'](dict(row, base_commit='abc', test_patch=''))
                script = spec.env_script
                self.assertIn('etc/profile.d/conda.sh', script)
                self.assertIn('CONDA_EXE', script)
                self.assertIn(f'conda create -n {get_env_name(row)} python=3.9', script)
                self.assertNotIn('/research/', script)
                self.assertNotIn('$HOME/requirements.txt', script)
                if row['repo'] == 'pylint-dev/pylint':
                    self.assertIn('astroid==3.0.0a6', script)
                else:
                    self.assertIn('python -m pip install', script)


if __name__ == '__main__':
    unittest.main()
