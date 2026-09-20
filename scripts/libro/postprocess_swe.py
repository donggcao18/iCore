from scripts.config import REPO_ROOT_DIR
from scripts.utils.common import *
from collections import defaultdict
from tqdm import tqdm
from datasets import load_dataset
from scripts.utils.git_utils import get_diff, git_reset_hash, git_reset, git_clean, git_clean_all, git_apply

import os
import re
import glob
import subprocess as sp
import argparse
from scripts.utils import swe_util


def inject_prefix_rootdir(proj):
    rpath = swe_util.repo_path(proj)
    return rpath
    

def needed_imports_by_bug_id(proj, gen_test, env_name):
    repo_path = swe_util.repo_path(proj)
    src_dir = swe_util.swe_path_prefix(proj)

    imports, needed_attr = needed_imports(
        repo_path, src_dir, gen_test, env_name)

    return imports, needed_attr


def inject_test_by_bug_id(proj, gen_test, imports, needed_elements, version, bug_id, pos):
    repo_path = inject_prefix_rootdir(proj)
    src_dir = swe_util.swe_path_prefix(proj)
    test_dir = swe_util.swe_test_path_prefix(proj, bug_id)

    return inject_test_to_best(repo_path, src_dir, test_dir, gen_test, imports, needed_elements, version, bug_id, pos=pos)

def remove_ansi_escape_sequences(text):
    ansi_escape = re.compile(r'\x1b\[([0-9]+)(;[0-9]+)*m')
    return ansi_escape.sub('', text)


def run_test(repo_dir_path, test_name, env_name):
    '''Returns failing test number.'''
    proj = repo_dir_path.split('/')[-2]
    version = env_name.split('__')[-1]
    status = 0
    conda_cmd = f'eval "$(conda shell.bash hook)" && conda activate {env_name} && export PYTHONPATH={repo_dir_path}:$PYTHONPATH && ' + swe_util.swe_test_cmd(proj, version, test_name)
    test_process = sp.run(conda_cmd, shell=True, executable='bash',
                          capture_output=True, cwd=repo_dir_path, timeout=60)
    if proj == 'django':
        captured_stdout = test_process.stderr.decode()
        captured_stderr = ''
    else:
        captured_stdout = test_process.stdout.decode()
        captured_stderr = test_process.stderr.decode()
        if len(captured_stderr) > 0 and ('Traceback' in captured_stderr or 'error' in captured_stderr.lower()):
            status = -1
    captured_stdout = remove_ansi_escape_sequences(captured_stdout)
    if len(captured_stdout) == 0:  # runtime error
        return -1, [], [captured_stderr] 
    else:
        stdout_lines = captured_stdout.split('\n')
        if proj in {'astropy', 'matplotlib', 'seaborn', 'flask', 'xarray', 'pylint', 'pytest', 'scikit-learn', 'sphinx', 'requests'}:
            # pytest style
            match = re.findall(r'(\d+) failed', captured_stdout)
            error_match = re.findall(r'(\d+) error in', captured_stdout)
            if match:
                failed_test_num = sum(int(num) for num in match)
            else:
                failed_test_num = 0
            if error_match:
                failed_test_num += sum(int(num) for num in error_match)
                status = -1
                
            failed_tests = re.findall(r'FAILED(.*::.*)', captured_stdout)
            error_tests = re.findall(r'ERROR (.*::.*)', captured_stdout)
            # reported failing test number and actual number of collected failing tests should match
            # assert len(failed_tests) == failed_test_num
            failed_tests += error_tests
            if failed_test_num > 0 and len(failed_tests) != failed_test_num:
                if f'__ {test_name.split("::")[-1]} __' in captured_stdout:
                    failed_tests.append(test_name)
                    
                if len(failed_tests) != failed_test_num:
                    print(f'Failed test number mismatch: {failed_test_num} vs {len(failed_tests)}')

            fail_idx = -1
            info_idx = -1
            if failed_test_num > 0 or status == -1:
                for (idx, line) in enumerate(stdout_lines):
                    if '= FAILURES =' in line or '= ERRORS =' in line:
                        fail_idx = idx
                    if '= short test summary info =' in line or '= warnings summary =' in line:
                        info_idx = idx
                        break
            stdout_lines = stdout_lines[fail_idx:info_idx]
            if len(stdout_lines) == 0 and status != 0 and captured_stderr:
                stdout_lines = captured_stderr.split('\n')
        elif proj == 'django':
            match = re.search(r'(failures|errors)=(\d+)', captured_stdout)
            
            if match:
                if 'errors' in match.group(1):
                    status = -1
                failed_test_num = int(match.group(2))
            else:
                if not ('Ran' in captured_stdout and 'OK' in captured_stdout):
                    status = -2
                failed_test_num = 0
            failed_tests = re.findall(r'(?:FAIL|ERROR): (.*)', captured_stdout)
            # assert len(failed_tests) == failed_test_num
            
            start_idx = -1
            end_idx = -1
            if failed_test_num > 0:
                for (idx, line) in enumerate(stdout_lines):
                    if 'FAIL:' in line or 'ERROR:' in line:
                        start_idx = idx
                    if 'Ran' in line and 'test' in line:
                        end_idx = idx
            elif status == -2:
                for (idx, line) in enumerate(stdout_lines):
                    if 'Traceback' in line:
                        start_idx = idx
                        break
            stdout_lines = stdout_lines[start_idx:end_idx]
                
        elif proj == 'sympy':
            match = re.search(r'=+ tests finished.*(\d+) (failed|exceptions).*=+', captured_stdout)
            if match:
                failed_test_num = int(match.group(1))
                if match.group(2) == 'exceptions':
                    status = -1
            else:
                failed_test_num = 0
            pattern = r'(?:_+)\n(?:_*)\s([^\s]+)'
            matches = re.findall(pattern, captured_stdout, re.MULTILINE)
            if matches:
                failed_tests = [m for m in matches if 'test' in m]
            else:
                failed_tests = []
            # assert len(failed_tests) == failed_test_num
            
            
            start_idx = -1
            end_idx = -1
            if failed_test_num > 0:
                for (idx, line) in enumerate(stdout_lines):
                    if 'FAIL' in line:
                        start_idx = idx
                    if 'tests finished' in line:
                        end_idx = idx
            stdout_lines = stdout_lines[start_idx:end_idx]
        return status, failed_tests, stdout_lines

def inject_test(proj, test_code, env_name, bug_id, pos):
    imports, needed_elements = needed_imports_by_bug_id(proj, test_code, env_name)
    version = env_name.split('__')[-1]
    return inject_test_by_bug_id(proj, test_code, imports, needed_elements, version, bug_id, pos=pos)

def individual_run(proj, bug_id, example_test, injection, env_name):
    """Run test

    Returns:
        dict: {
            'compile_error'(boolean)
            'runtime_error'(boolean)
            'failed_tests'(list)
            'autogen_failed'(boolean)
            'fib_error_msg'(str)
        }
    """
    # test class generation & addition
    test_names = inject_test(proj, example_test, env_name, bug_id, pos=injection)
    test_name = test_names[0]
    # actual running experiment
    fib_error_msg = None
    
    status, failed_tests, fib_error_msg = run_test(repo_path(proj), test_name, env_name)
    
    
    return {
        'compile_error': status == -2,
        'runtime_error': status == -1,
        'failed_tests': failed_tests,
        'autogen_failed': len(failed_tests) > 0,
        'fib_error_msg': fib_error_msg,
        # 'compile_msg': compile_msg if status == -2 else None
    }

def setup_environment(proj, repo_dir_path, env_name):
    # git_reset_hash(repo_dir_path, env_setup_hash)
    # git_clean(repo_dir_path)
    version = env_name.split('__')[-1]
    setup_cmd = swe_util.swe_setup_cmd(proj, version)
    if setup_cmd is None:
        return
    
    cmd = f'eval "$(conda shell.bash hook)" && conda activate {env_name} && {setup_cmd}'

    process = sp.run(cmd, shell=True, executable='bash', capture_output=True, cwd=repo_dir_path)
    if process.returncode != 0 and 'Successfully installed' not in process.stdout.decode():
        raise ValueError(f'Error setting up environment for {proj}. \n{process.stdout.decode()}\n{cmd}')

def twover_run_experiment(bug_report, example_tests, injection):
    """
    returns results in order of example_tests.
    """
    proj = bug_report["repo"]
    bug_id = bug_report["instance_id"]
    
    env_name = swe_util.get_env_name(bug_report)
    print(f'{bug_id} (injection={injection} root_path={REPO_ROOT_DIR} env_name={env_name})')

    # init
    repo_path = inject_prefix_rootdir(proj)
    
    # Roll back to specified commit
    git_reset_hash(repo_path, bug_report["base_commit"])
    git_clean_all(repo_path)
    
    try:
        setup_environment(proj, repo_path, env_name)
    except Exception as e:
        print(f'[error] {repr(e)}')
        raise e
        # return [f'[error] {repr(e)}' for _ in example_tests]

    # Running experiment for buggy version
    buggy_results = []
    fixed_results = []
    final_results = []
    for example_test in tqdm(example_tests):
        try:
            buggy_info = individual_run(proj, bug_id, example_test, injection, env_name)
        except Exception as e:
            buggy_info = f'[error] {repr(e)}'

        buggy_results.append(buggy_info)
        
        # Roll back to the previous version
        git_reset(repo_path)
        git_clean(repo_path)
    
        # apply patch
        git_apply(repo_path, bug_report["patch"])
        
        try:
            fixed_info = individual_run(proj, bug_id, example_test, injection, env_name)
        except Exception as e:
            fixed_info = f'[error] {repr(e)}'

        fixed_results.append(fixed_info)
        
        # Roll back to the previous version
        git_reset(repo_path)
        git_clean(repo_path)
    
        if isinstance(buggy_info, str):
            final_results.append(buggy_info)
            continue
        fails_in_buggy_version = buggy_info['autogen_failed'] or buggy_info['runtime_error'] or buggy_info['compile_error']
        
        if isinstance(fixed_info, dict):
            fails_in_fixed_version = fixed_info['autogen_failed'] or fixed_info['compile_error'] or fixed_info['runtime_error']
        else:
            fails_in_fixed_version = True
        
        success = (fails_in_buggy_version and not fails_in_fixed_version)

        final_results.append({
            'buggy': buggy_info,
            'fixed': fixed_info,
            'success': success,
        })
    return final_results

def get_injection_results(bug_report, example_test, injection='libro'):
    proj = bug_report["repo"]
    bug_id = bug_report["instance_id"]
    
    env_name = swe_util.get_env_name(bug_report)
    print(f'{bug_id} (injection={injection} root_path={REPO_ROOT_DIR} env_name={env_name})')

    # init
    repo_path = inject_prefix_rootdir(proj)
    
    # Roll back to specified commit
    git_reset_hash(repo_path, bug_report["base_commit"])
    git_clean_all(repo_path)
    
    setup_environment(proj, repo_path, env_name)
    
    try:
        test_names = inject_test(proj, example_test, env_name, bug_id, pos=injection)
        test_name = test_names[0]
        test_patch = get_diff(repo_path)
    except Exception as e:
        print(f'[error] {repr(e)}')
        test_name = None
        test_patch = f'[error] {repr(e)}'
        
    return test_patch
        
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--gen_test_dir', default='')
    parser.add_argument('--all', default=True, action='store_true')
    parser.add_argument('--model', default='gpt')
    parser.add_argument('--exp_name', default='final')
    parser.add_argument('--injection_path', default='libro')
    parser.add_argument('--result_file', default=None)
    parser.add_argument('--swt', default=False, action='store_true')
    parser.add_argument('--tdd', default=False, action='store_true')
    parser.add_argument('--from_id', default=None)
    args = parser.parse_args()

    exp_name = args.exp_name
    model = args.model
    if not args.gen_test_dir:
        gen_test_dir = os.path.expanduser(f'~/code/libro/data/{exp_name}/gen_tests_{model}/')
    else:
        gen_test_dir = args.gen_test_dir
    bug2tests = defaultdict(list)
        
    with open('swt.txt', 'r') as f:
        swt = f.read().strip().split('\n')
    with open('tdd.txt', 'r') as f:
        tdd = f.read().strip().split('\n')
    if args.tdd:
        swe_bench = load_dataset("princeton-nlp/SWE-bench_Verified")["test"]
        swe_bench = [bug for bug in swe_bench if bug["instance_id"] in tdd]
    elif args.swt:
        swe_bench = load_dataset("SWE-bench/SWE-bench_Lite")["test"]
        swe_bench = [bug for bug in swe_bench if bug["instance_id"] in swt]
    else:
        swe_bench = load_dataset("SWE-bench/SWE-bench_Lite")["test"]
    if not args.result_file:
        result_file = f'./results/{exp_name}/{model}.json'
    else:
        result_file = args.result_file
    if not os.path.exists(result_file):
        os.makedirs(os.path.dirname(result_file), exist_ok=True)
        with open(result_file, 'w') as f:
            json.dump({}, f, indent=4)
    with open(result_file, 'r') as f:
        exec_results = json.load(f)
    with open('tmp_data/final_gpt_@1_acc.txt', 'r') as f:
        skip = f.read().strip().split('\n')
    flag = False
    injection = args.injection_path
    for bug_report in swe_bench:
        project = bug_report["repo"]
        bug_id = bug_report["instance_id"]

        if args.from_id and not flag:
            if bug_id == args.from_id:
                flag = True
            else:
                continue
            
        # if bug_id in [
        #     "pylint-dev__pylint-7114",
        #     "pylint-dev__pylint-7228",
        #     "pylint-dev__pylint-7993",
        # ]:
        #     continue
        
        if bug_id in exec_results and len(exec_results[bug_id]) >= 5:
            continue
        # if bug_id in skip:
        #     continue

        res_for_bug = {}

        example_tests = []
        tests = glob.glob(os.path.join(gen_test_dir, f'{bug_id}_*.txt'))
        for test_file in tests:
            with open(test_file) as f:
                test_content = f.read().strip()
            if test_content.startswith('```'):
                test_content = test_content.removeprefix('```')
            if test_content.endswith('```'):
                test_content = test_content.removesuffix('```')
            if test_content.startswith('python'):
                test_content = test_content.removeprefix('python')
            
            example_tests.append(test_content)
        results = twover_run_experiment(bug_report, example_tests, injection=injection)

        for test_path, res, test_content in zip(tests, results, example_tests):
            res_for_bug[os.path.basename(test_path)] = res
        exec_results[bug_id] = res_for_bug
        
        with open(result_file, 'w') as f:
            json.dump(exec_results, f, indent=4)
