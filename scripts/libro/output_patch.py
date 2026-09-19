import ast
from collections import defaultdict
import json
import os
import argparse
import pandas

from scripts.libro.postprocess_swe import get_injection_results
from datasets import load_dataset

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--gen_test_dir', default='')
    parser.add_argument('--model', default='deepseek-chat')
    parser.add_argument('--exp_name', default='final')
    parser.add_argument('--swt', default=False, action='store_true')
    parser.add_argument('--tdd', default=False, action='store_true')
    parser.add_argument('--injection_path', default='libro')
    parser.add_argument('--result_file', default=None)
    parser.add_argument('--from_id', default=None)
    args = parser.parse_args()

    exp_name = args.exp_name
    model = args.model
    injection = args.injection_path
    result_file = args.result_file

    if not args.gen_test_dir:
        gen_test_dir = os.path.expanduser(f'~/code/libro/data/{exp_name}/gen_tests_{model}/')
    else:
        gen_test_dir = args.gen_test_dir
    
    if not args.result_file:
        result_file = f'./results/{exp_name}/{model}_patch.jsonl'
    else:
        result_file = args.result_file

    done_ids = []
    if os.path.exists(result_file):
        with open(result_file) as res_f:
            for line in res_f.readlines():
                line_data = json.loads(line)
                done_ids.append(line_data["instance_id"])
                
    bug2tests = defaultdict(list)
    with open('swt.txt', 'r') as f:
        swt = f.read().strip().split('\n')
    with open('tdd.txt', 'r') as f:
        tdd = f.read().strip().split('\n')
    if args.tdd:
        swe_bench = load_dataset("SWE-bench/SWE-bench_Verified")["test"]
        swe_bench = [bug_report for bug_report in swe_bench if bug_report["instance_id"] in tdd]
    elif args.swt:
        swe_bench = load_dataset("SWE-bench/SWE-bench_Lite")["test"]
        swe_bench = [bug_report for bug_report in swe_bench if bug_report["instance_id"] in swt]
    df = pandas.read_csv(f'results/{exp_name}/ranking_{exp_name}_{model}.csv')
    
    flag = False
    for bug_report in swe_bench:
        project = bug_report["repo"]
        bug_id = bug_report["instance_id"]
        if args.from_id and not flag:
            if bug_id == args.from_id:
                flag = True
            else:
                continue

        sorted_tests = df[df['bug_id'] == bug_id]['sorted_tests'].tolist()
        if len(sorted_tests) == 0:
            print(f'No generated tests for {bug_id}')
            continue
        sorted_tests = sorted_tests[0]

        test_file = ast.literal_eval(sorted_tests)[0]
        test_path = os.path.expanduser(os.path.join(gen_test_dir, f'{test_file}'))
        with open(test_path) as f:
            test_content = f.read().strip()
        if test_content.startswith('```'):
            test_content = test_content.removeprefix('```')
        if test_content.endswith('```'):
            test_content = test_content.removesuffix('```')
        if test_content.startswith('python'):
            test_content = test_content.removeprefix('python')
        
        example_test = test_content

        result = get_injection_results(
            bug_report, example_test, injection=injection)
        if result is None:
            print(f"Error in {bug_id}")
        
        data = {
            "instance_id": bug_id,
            "model_name_or_path": f'{exp_name}_{model}',
            "model_patch": result
        }
        with open(result_file, 'a') as f:
            json.dump(data, f, ensure_ascii=False)
            f.write('\n')
