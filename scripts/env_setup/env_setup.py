"""Prepare benchmark Conda environments from a dataset or a local CSV."""

import argparse
import csv
from pathlib import Path

from scripts.config import REPO_ROOT_DIR
import subprocess
import os
from scripts.utils.swe_util import get_conda_python, get_env_name

GITHUB_TOKEN = ""


def clone_repo(repo, root_dir, token):
    """
    Clones a GitHub repository to a specified directory.

    Args:
        repo (str): The GitHub repository to clone.
        root_dir (str): The root directory to clone the repository to.
        token (str): The GitHub personal access token to use for authentication.

    Returns:
        Path: The path to the cloned repository directory.
    """
    repo_dir = Path(root_dir, f"{repo.split('/')[-1]}/")
    print(repo_dir)
    if not repo_dir.exists():
        from git import Repo
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        repo_url = f"https://{token}@github.com/{repo}.git" if token else f"https://github.com/{repo}.git"
        print(f"Cloning {repo} to {repo_dir}")
        Repo.clone_from(repo_url, repo_dir)
    return repo_dir

ROOT = Path(__file__).resolve().parents[2]


def selected_instances(dataset_csv, repos):
    if dataset_csv is not None:
        csv.field_size_limit(10_000_000)
        with Path(dataset_csv).open(encoding='utf-8-sig', newline='') as handle:
            dataset = list(csv.DictReader(handle))
    else:
        from datasets import load_dataset
        dataset = load_dataset('princeton-nlp/SWE-bench_Verified')['test']
    rows = [row for row in dataset if row['repo'] in repos]
    missing = set(repos) - {row['repo'] for row in rows}
    if missing:
        raise ValueError('No dataset instances for: ' + ', '.join(sorted(missing)))
    return rows


def setup_environments(rows, log_dir):
    # Environments are shared by repository/version, not by model or instance.
    unique = {}
    for row in rows:
        unique.setdefault(get_env_name(row), row)
    for env_name, row in unique.items():
        directory = Path(log_dir).resolve() / env_name
        script_path = directory / 'setup.sh'
        complete = directory / 'complete.txt'
        get_conda_python.cache_clear()
        try:
            python = get_conda_python(env_name)
        except FileNotFoundError:
            python = None
        if python is not None:
            if script_path.exists() and not complete.exists():
                raise RuntimeError(f'Previous setup did not complete for {env_name}. '
                                   f'Check {directory / "setup.log"}; remove the incomplete '
                                   f'environment with conda env remove -n {env_name} before retrying.')
            print(f'Using existing environment: {env_name} ({python})', flush=True)
            clone_repo(row['repo'], os.path.expanduser(REPO_ROOT_DIR), GITHUB_TOKEN)
            continue
        # Environment scripts read requirements at the setup commit from GitHub.
        # They do not need to reset or clean the user's repository checkout.
        from scripts.env_setup.exec_spec import make_exec_spec
        spec = make_exec_spec(row)
        clone_repo(row['repo'], os.path.expanduser(REPO_ROOT_DIR), GITHUB_TOKEN)
        directory.mkdir(parents=True, exist_ok=True)
        complete.unlink(missing_ok=True)
        script_path.write_text(spec.env_script, encoding='utf-8')
        log = directory / 'setup.log'
        print(f'Setting up {env_name}; log: {log}', flush=True)
        with log.open('w', encoding='utf-8') as handle:
            result = subprocess.run(['bash', str(script_path)], cwd=directory,
                                    stdout=handle, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'Setup failed for {env_name}. Check {log}. '
                               'If a partial environment was created, remove it with '
                               f'conda env remove -n {env_name} before retrying.')
        get_conda_python.cache_clear()
        python = get_conda_python(env_name)
        complete.write_text(python + '\n', encoding='utf-8')
    print(f'Prepared {len(unique)} benchmark environments.', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-csv', type=Path, help='Use the same local CSV as retrieval.')
    parser.add_argument('--repo', action='append', help='Repository owner/name; repeat for several repositories.')
    parser.add_argument('--log-dir', type=Path, default=ROOT / 'retrieval_results/env_setup')
    args = parser.parse_args(argv)
    # Retain the original Flask/Verified selection for existing no-argument users.
    rows = selected_instances(args.dataset_csv, args.repo or ['pallets/flask'])
    setup_environments(rows, args.log_dir)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from exc
