import argparse
import ast
from pathlib import Path
import re
from openai import OpenAI
from scripts.config import API_KEY, BASE_URL
from scripts.test_retrieval.function_calls import FunctionCalls, get_tools
from scripts.test_retrieval.initial_retrieval import extract_function_call
from scripts.test_retrieval.utils import get_related_test
from scripts.utils.git_utils import *
import json
import os
from datasets import load_dataset
import pandas as pd

from scripts.generator.make_prompt_util import get_function_content, get_related_test_list


system_prompt = """\
You are an assistant for a Python project, specifically helping to select relevant test cases as references for writing new test cases based on a bug report.

When the user provides a list of test files and functions, your task is as follows:  
1. Select up to **{topk}** of the most relevant test functions from the provided list. The selection should prioritize tests that directly match the functionality, module, or issue described in the bug report, as these will be most useful for writing a new test case.
2. Rank the selected test functions in order of relevance, with the most relevant one first.  
3. If none of the provided test functions seem sufficiently relevant, you may use the available tools (`list_root()`, `list_folder`, `list_classes_and_functions`, `read_function`) to explore the test directory and find better matches.  

### Available Tools:  
- `list_root()`: Lists all files and directories inside the root test folder of the project.
- `list_folder(path)`: Lists files and directories at the given path.  
- `list_classes_and_functions(file_path)`: Lists all classes and functions in a given file.  
- `read_function(file_path, function_name)`: Reads the source code of a specific function.  

### Required Output Format:  
Your output must follow this structure and be enclosed within triple backticks (```):  
```python
[
    ["path/to/test_file1.py", "test_function1"],
    ["path/to/test_file2.py", "test_function2"],
    ...
]
```
This list should contain at most **{topk}** entries, ranked from most to least relevant.
"""

def check_not_in(ret, file_path, test_name):
    for test in ret:
        test_file_path = test.split('::')[0]
        test_test_name = test.split('::')[1]
        if test_file_path == file_path and \
            test_test_name.split('.')[-1] == test_name.split('.')[-1]:
            return False
    return True

def load_tests_from_df(proj, df, topk=5):
    tests = []
    for i, row in df.iterrows():
        # class name might be nan
        file_path, class_name, test_name = row['file_path'], row['class_name'], row['test_name']
        test_name = f"{class_name}.{test_name}" if not pd.isna(class_name) else test_name
        test_content = get_function_content(proj, file_path, test_name)
        tests.append(f'- {file_path} {test_name}\n ```\n{test_content}\n```\n')
    return tests

def list_candidates(proj, bug_id, test_similarity_dir, last_related_tests_path, wo_functioncall, topk):
    call_similarity_csv_path = os.path.join(test_similarity_dir, f"{bug_id}/call.csv")
    score_similarity_csv_path = os.path.join(test_similarity_dir, f"{bug_id}/score.csv")
    semantic_similarity_csv_path = os.path.join(test_similarity_dir, f"{bug_id}/semantic.csv")
    if not os.path.exists(semantic_similarity_csv_path) and not wo_functioncall:
        raise Exception(f'File not found: {call_similarity_csv_path}')
    if not os.path.exists(score_similarity_csv_path) or not os.path.exists(call_similarity_csv_path):
        wo_functioncall = True
    if not wo_functioncall:
        
        call_df = pd.read_csv(call_similarity_csv_path)
        if call_df.shape[0] > topk:
            call_df = call_df[:topk]
        semantic_df = pd.read_csv(semantic_similarity_csv_path)[:topk]
        if semantic_df.shape[0] > topk:
            semantic_df = semantic_df[:topk]
        score_df = pd.read_csv(score_similarity_csv_path)[:topk]
        if score_df.shape[0] > topk:
            score_df = score_df[:topk]

        call_tests = load_tests_from_df(proj, call_df)
        semantic_tests = load_tests_from_df(proj, semantic_df)
        score_tests = load_tests_from_df(proj, score_df)

        last_related_tests = get_related_test_list(bug_id, last_related_tests_path)

        candidates = set(call_tests + semantic_tests + score_tests + last_related_tests)
    else:
        semantic_similarity_csv_path = os.path.join(test_similarity_dir, f"{bug_id}/semantic.csv")
        semantic_df = pd.read_csv(semantic_similarity_csv_path)[:topk*2]
        semantic_tests = load_tests_from_df(proj, semantic_df)

        last_related_tests = get_related_test_list(bug_id, last_related_tests_path)
        candidates = set(semantic_tests + last_related_tests)
    return candidates

def chat_with_llm(instance, model_name, messages_path, test_similarity_dir, last_related_tests_path, wo_functioncall, topk, restart=False):
    bug_id = instance['instance_id']
    bug_report = instance['problem_statement']
    proj = instance['repo']
    
    if os.path.exists(messages_path) and not restart:
        with open(messages_path, 'r') as f:
            messages = json.load(f)
        if messages[-1]['role'] == 'assistant':
            return
    else:
        candidates = list_candidates(proj, bug_id, test_similarity_dir, last_related_tests_path, wo_functioncall, topk)
        candidate_tests = "\n".join(candidates)
        messages = [
            {
                "role": "system",
                "content": system_prompt.format(topk=topk)
            },
            {
                "role": "user",
                "content": f"# Bug Report\n{bug_report}\n# Candidate tests\n{candidate_tests}"
            }
        ]
    func_calls = FunctionCalls(instance)
    
    api_key = API_KEY[model_name]
    base_url = BASE_URL[model_name]
    client = OpenAI(api_key=api_key, base_url=base_url)

    
    while True:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=get_tools(),
            stream=True,
            timeout=60,
            temperature=0.0
        )
        chunks = []
        for chunk in response:
            message_json = chunk.model_dump_json()
            chunks.append(json.loads(message_json))
        
        # concatenate all messages
        tool_calls, response_message = extract_function_call(chunks)
        messages.append(response_message)
        if not tool_calls:
            with open(messages_path, 'w') as f:
                json.dump(messages, f, indent=4)
            break
        # if len(tool_calls) > 5:
        #     raise Exception('Too many tool calls!')
        error_retries = 0
        for tool_call in tool_calls:
            try:
                name = tool_call['function']['name']
                args = json.loads(tool_call['function']['arguments'])

                result = func_calls.call_function(name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call['id'],
                    "content": result
                })
            except Exception as e:
                error_retries += 1
                if error_retries >= 10:
                    raise e
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call['id'],
                    "content": f"{e.__class__.__name__}: {str(e)}"
                })
        with open(messages_path, 'w') as f:
            json.dump(messages, f, indent=4)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output_related_tests_path", default="./retrieval_results/test/related_tests_2.json")
    parser.add_argument("--message_dir", default="./retrieval_results/test/messages/messages_2/")
    parser.add_argument("--test_similarity_dir", default="./retrieval_results/test/test_similarity/1/")
    parser.add_argument("--last_related_tests_path", default="./retrieval_results/test/related_tests_1.json")
    parser.add_argument("--wo_call", action='store_true', default=False)
    parser.add_argument("--topk", type=int, default=5)
    parser.add_argument("--swt", action='store_true', default=False)
    parser.add_argument("--tdd", action='store_true', default=False)
    parser.add_argument("--model", type=str, default="gpt-4o-2024-08-06")

    args = parser.parse_args()
    message_dir = args.message_dir
    test_similarity_dir = args.test_similarity_dir
    last_related_tests_path = args.last_related_tests_path
    Path(message_dir).mkdir(parents=True, exist_ok=True)
    assert os.path.exists(test_similarity_dir)
    assert os.path.exists(last_related_tests_path)

    with open('swt.txt', 'r') as f:
        swt = f.read().strip().split('\n')
    with open('tdd.txt', 'r') as f:
        tdd = f.read().strip().split('\n')
    if args.tdd:
        swe_bench = load_dataset("princeton-nlp/SWE-bench_Verified")["test"]
        swe_bench = [bug_report for bug_report in swe_bench if bug_report["instance_id"] in tdd]
    else:
        swe_bench = load_dataset("SWE-bench/SWE-bench_Lite")["test"]
        swe_bench = [bug_report for bug_report in swe_bench if bug_report["instance_id"] in swt]
    related_tests = {}
    if os.path.exists(args.output_related_tests_path):
        with open(args.output_related_tests_path, 'r') as f:
            related_tests = json.load(f)
    flag = False
    for bug_report in swe_bench:
        bug_id = bug_report["instance_id"]
        proj = bug_report['repo']
        
        if bug_id in related_tests:
            continue

        print(f'Generating {bug_id}')
        initialize(bug_report)
        
        message_path = os.path.join(message_dir, f"{bug_id}.json")
        chat_with_llm(bug_report, args.model, message_path, test_similarity_dir, last_related_tests_path, args.wo_call, args.topk, restart=False)

        if os.path.exists(message_path):
            with open(message_path, 'r') as f:
                messages = json.load(f)
        else:
            raise Exception('File not found')
        
        assert messages[-1]['role'] == 'assistant', bug_id
        content = messages[-1]['content']
        
        pattern = r"```python(.*?)```"
        matches = re.findall(pattern, content, re.DOTALL)
        if matches:
            content = matches[-1].strip()
        else:
            pattern2 = r"```(.*?)```"
            matches2 = re.findall(pattern2, content, re.DOTALL)
            if matches2:
                content = matches2[-1].strip()
        
        # Remove comment parts
        s_clean = re.sub(r"#.*", "", content)  # Remove # and the content following it
        # if s_clean.endswith(',\n]\n'):
        #     s_clean = s_clean.replace(',\n]\n', ']\n')
        try:
            tests = ast.literal_eval(s_clean)
        
            # results[bug_id] = tests
            related_tests[bug_id] = get_related_test(proj, tests)
        except Exception as e:
            print(f'{message_path}')
            raise e
    with open(args.output_related_tests_path, 'w') as f:
        json.dump(related_tests, f, indent=4)