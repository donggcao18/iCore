import subprocess as sp
from os import path
from scripts.utils.swe_util import repo_path

def git_reset_hash(repo_dir_path, commit_hash):
    process = sp.run(['git', 'reset', '--hard', commit_hash],
           cwd=repo_dir_path, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    assert process.returncode == 0, f"git reset failed with return code {process.returncode} {repo_dir_path}"

def git_reset(repo_dir_path):
    process = sp.run(['git', 'reset', '--hard', 'HEAD'],
           cwd=repo_dir_path, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    assert process.returncode == 0, f"git reset failed with return code {process.returncode}"

def git_clean(repo_dir_path):
    process = sp.run(['git', 'clean', '-df'],
           cwd=repo_dir_path, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    assert process.returncode == 0, f"git clean failed with return code {process.returncode}"
    
def git_clean_all(repo_dir_path):
    git_clean(repo_dir_path)
    process = sp.run(['git', 'clean', '-dfX'],
           cwd=repo_dir_path, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    assert process.returncode == 0, f"git clean failed with return code {process.returncode}"

def git_apply(repo_dir_path, patch_content):
    # Write patch to file
    with open(path.join(repo_dir_path, 'swe.patch'), 'w') as f:
        f.write(patch_content)
    # apply patch
    process = sp.run(['git', 'apply', 'swe.patch'],
              cwd=repo_dir_path, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    assert process.returncode == 0, f"git apply failed with return code {process.returncode}"
    
def get_diff(repo_dir_path):
    process = sp.run(['git', 'diff', '--no-color'],
           cwd=repo_dir_path, stdout=sp.PIPE, stderr=sp.DEVNULL)
    assert process.returncode == 0, f"git diff failed with return code {process.returncode}"
    result = process.stdout.decode('utf-8')

    # Split diff by file
    file_diffs = {}
    current_file = None
    current_diff = []
    for line in result.splitlines():
        line = line.rstrip()  # Clean up trailing spaces
        
        # Detect the start of a new file
        if line.startswith('diff --git'):
            if current_file is not None:
                file_diffs[current_file] = current_diff
            current_file = line.split()[-1][2:]  # Extract file name
            current_diff = [line]
        elif current_file is not None:
            current_diff.append(line)
    
    # Add the last file
    if current_file is not None and current_diff:
        file_diffs[current_file] = current_diff
    
    # Filter files containing "test"
    filtered_diffs = {}
    for file_path, diff_lines in file_diffs.items():
        if 'test' in file_path.lower():
            filtered_diffs[file_path] = diff_lines
        
    combined_diff = []
    for file_path, diff_lines in filtered_diffs.items():
        combined_diff.extend(diff_lines)
        combined_diff.append('')  # Add empty line to separate different files
    
    return '\n'.join(combined_diff)

def initialize(bug_report):
    repo_dir = repo_path(bug_report["repo"])
    base_commit = bug_report["base_commit"]
    git_reset_hash(repo_dir, base_commit)
    git_clean(repo_dir)
    git_clean_all(repo_dir)

def initialize_commit(repo, commit):
    repo_dir = repo_path(repo)
    base_commit = commit
    git_reset_hash(repo_dir, base_commit)
    git_clean(repo_dir)
    git_clean_all(repo_dir)