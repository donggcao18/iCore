"""Isolated snapshots and structured execution; never reset the source clone."""

from contextlib import contextmanager
import json
import io
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import tempfile


def git(repo, *args, input=None):
    result = subprocess.run(['git', '-c', f'safe.directory={Path(repo).resolve().as_posix()}',
                             '-C', str(repo), *args], input=input,
                            capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace').strip())
    return result.stdout


@contextmanager
def snapshot(repo, commit, parent=None):
    """Export an immutable commit without a worktree or destructive Git calls."""
    with tempfile.TemporaryDirectory(prefix='icore-brt-', dir=parent) as folder:
        root = Path(folder)
        work = root / 'repo'
        work.mkdir()
        entries = []
        for record in git(repo, 'ls-tree', '-r', '-z', commit).split(b'\0'):
            if not record:
                continue
            metadata, name = record.split(b'\t', 1)
            mode, kind, oid = metadata.split()
            path = name.decode('utf-8')
            if (PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts
                    or '\\' in path or ':' in path):
                raise ValueError(f'Unsupported Git path: {path!r}')
            if kind != b'blob':
                raise ValueError(f'Submodules require a separate snapshot adapter: {path}')
            entries.append((mode, oid, path))
        # Read exact blobs: git archive can omit tests via export-ignore or
        # rewrite export-subst files. Neither is acceptable for reproduction.
        blobs = io.BytesIO(git(repo, 'cat-file', '--batch',
                               input=b''.join(oid + b'\n' for _, oid, _ in entries)))
        for mode, oid, path in entries:
            header = blobs.readline().split()
            if len(header) != 3 or header[0] != oid or header[1] != b'blob':
                raise RuntimeError(f'Cannot export Git blob: {path}')
            data = blobs.read(int(header[2]))
            if blobs.read(1) != b'\n':
                raise RuntimeError(f'Truncated Git blob: {path}')
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == b'120000':
                link = data.decode('utf-8')
                if not (target.parent / link).resolve().is_relative_to(work.resolve()):
                    raise ValueError(f'Symlink leaves the snapshot: {path}')
                target.symlink_to(link)
            else:
                target.write_bytes(data)
                if mode == b'100755' and os.name != 'nt':
                    target.chmod(target.stat().st_mode | 0o111)
        yield work


def apply_patch(work, patch):
    git(work, 'apply', '--recount', '--whitespace=nowarn', '-', input=patch.encode('utf-8'))


def classify(report, returncode, selector):
    collected = report.get('collected', [])
    events = report.get('events', [])
    diagnostics = '\n'.join(event.get('diagnostic', '') for event in events)
    exact = lambda node: node == selector or node.startswith(selector + '[')
    if (not collected or any(not exact(node) for node in collected)
            or any(event['phase'] == 'collect' and event['outcome'] == 'failed' for event in events)):
        status = 'collection_error'
    elif returncode not in (0, 1):
        status = 'runner_error'
    elif any(event['outcome'] == 'skipped' or event.get('xfail') for event in events):
        status = 'skipped'
    elif any(event['phase'] in ('setup', 'teardown') and event['outcome'] != 'passed' for event in events):
        status = 'setup_error'
    else:
        calls = [event for event in events if event['phase'] == 'call']
        if (len(calls) != len(collected) or {e['nodeid'] for e in calls} != set(collected)
                or len(collected) != len(set(collected))):
            status = 'runner_error'
        elif any(event['outcome'] == 'failed' for event in calls):
            status = 'failed' if returncode == 1 else 'runner_error'
        else:
            status = 'passed' if returncode == 0 else 'runner_error'
    return dict(status=status, collected=collected, events=events, returncode=returncode,
                diagnostic=diagnostics)


class PytestRunner:
    def __init__(self, python, *, timeout=60, pytest_args=(), install_project=False):
        self.python = str(Path(python).resolve())
        self.timeout = timeout
        self.pytest_args = list(pytest_args)
        self.install_project = install_project

    def preflight(self):
        if not Path(self.python).is_file():
            raise ValueError(f'Missing benchmark interpreter: {self.python}')
        result = subprocess.run([self.python, '-c', 'import pytest; print(pytest.__version__)'],
                                capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Benchmark interpreter cannot import pytest: ' +
                               result.stderr.decode('utf-8', errors='replace'))

    def prepare(self, work, artifacts):
        if not self.install_project:
            return
        # Opt-in rebuild in each exported version; dependencies must be prepared
        # already. This may update the selected environment's editable install.
        result = subprocess.run([self.python, '-m', 'pip', 'install', '--no-deps',
                                 '--no-build-isolation', '-e', '.'], cwd=work,
                                capture_output=True, timeout=max(300, self.timeout))
        Path(artifacts).mkdir(parents=True, exist_ok=True)
        (Path(artifacts) / 'install.stdout.txt').write_bytes(result.stdout)
        (Path(artifacts) / 'install.stderr.txt').write_bytes(result.stderr)
        if result.returncode:
            raise RuntimeError(f'Project installation failed; see {artifacts}')

    def run(self, work, selector, artifacts):
        artifacts = Path(artifacts).resolve()
        artifacts.mkdir(parents=True, exist_ok=True)
        self.prepare(work, artifacts)
        report_file = artifacts / 'report.json'
        report_file.unlink(missing_ok=True)
        plugin_dir = Path(__file__).parent.resolve()
        env = os.environ.copy()
        env['ICORE_BRT_REPORT'] = str(report_file)
        # Imported project code must come from this snapshot, including src/lib layouts.
        env['PYTHONPATH'] = os.pathsep.join(map(str, [work / 'src', work / 'lib', work, plugin_dir]))
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        # Override inherited selection flags; explicit plugins from the project remain available.
        env.pop('PYTEST_ADDOPTS', None)
        command = [self.python, '-m', 'pytest', '-p', 'icore_brt_probe', '-o', 'addopts=',
                   '--tb=short', '-v', *self.pytest_args, selector]
        options = dict(cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options['start_new_session'] = True
        process = subprocess.Popen(command, **options)
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            stdout, stderr = process.communicate()
        (artifacts / 'stdout.txt').write_bytes(stdout)
        (artifacts / 'stderr.txt').write_bytes(stderr)
        try:
            report = json.loads(report_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            report = {}
        result = classify(report, process.returncode, selector)
        if not report:
            result['status'] = 'runner_error'
        if timed_out:
            result['status'] = 'timeout'
        if not result['diagnostic']:
            result['diagnostic'] = (stdout + b'\n' + stderr).decode('utf-8', errors='replace')
        result['artifacts'] = str(artifacts)
        return result


def evaluate_frozen(repo, row, candidate, runner, artifacts, *, repetitions=2):
    """No generation/retrieval is callable here. Run the exact saved test diff twice."""
    results = {}
    for version in ('buggy', 'fixed'):
        runs = []
        for repeat in range(1, repetitions + 1):
            try:
                with snapshot(repo, row['base_commit']) as work:
                    if version == 'fixed':
                        apply_patch(work, row['patch'])
                    apply_patch(work, candidate['patch'])
                    runs.append(runner.run(work, candidate['selector'], Path(artifacts) / version / str(repeat)))
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                runs.append(dict(status='infrastructure_error', diagnostic=str(exc)))
        results[version] = dict(runs[0])
        results[version]['runs'] = runs
        if any((run['status'], run.get('collected')) != (runs[0]['status'], runs[0].get('collected'))
               for run in runs[1:]):
            results[version]['status'] = 'flaky'
    results['success'] = (results['buggy']['status'] == 'failed'
                          and results['fixed']['status'] == 'passed'
                          and results['buggy'].get('collected') == results['fixed'].get('collected'))
    return results
