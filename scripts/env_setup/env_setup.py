from pathlib import Path

from git import Repo
from scripts.config import REPO_ROOT_DIR
from scripts.env_setup.exec_spec import make_exec_spec
from datasets import load_dataset
import subprocess
import os
from tqdm import tqdm

from scripts.utils.git_utils import initialize_commit
from scripts.utils.swe_util import repo_path

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
        repo_url = f"https://{token}@github.com/{repo}.git"
        print(f"Cloning {repo} to {repo_dir}")
        Repo.clone_from(repo_url, repo_dir)
    return repo_dir

PREFIX = ''

if __name__ == "__main__":
    with open('env.txt', 'r') as f:
        env_list = [line.strip() for line in f.readlines()]
    with open("tdd.txt", "r") as f:
        tdd = set(line.strip() for line in f)
    flag = False
    try:
        dataset = load_dataset("princeton-nlp/SWE-bench_Verified")["test"]
        test_specs = list(map(make_exec_spec, dataset))
        for spec in test_specs:
            if spec.instance_id not in tdd:
                continue
            # if spec.instance_id != "pylint-dev__pylint-7114":
            #     flag = True
            # if not flag:
                # continue
            env_name = PREFIX + spec.env_name
            if not os.path.exists(repo_path(spec.repo)):
                print(f"clone {repo_path(spec.repo)}")
                clone_repo(spec.repo, os.path.expanduser(REPO_ROOT_DIR), GITHUB_TOKEN)
            
            if env_name in env_list:
                continue
            print(f"Setting up environment: {env_name}")
            initialize_commit(spec.repo, spec.base_commit)
            with open("env_setup.sh", "w") as f:
                script = spec.env_script.replace(spec.env_name, env_name)
                f.write(script)
            result = subprocess.run(["bash", "env_setup.sh"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            if result.returncode != 0:
                print(f"Error setting up environment {env_name}: {result.stderr.decode()}")

            env_list.append(env_name)
    except Exception as e:
        print(e)
        print(env_list)
