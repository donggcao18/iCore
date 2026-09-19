import argparse
import copy
import re
from openai import OpenAI
from scripts.config import API_KEY, BASE_URL
from scripts.test_retrieval.function_calls import FunctionCalls, get_tools
from scripts.test_retrieval.utils import get_related_test
from scripts.utils.git_utils import *
import json
import os
from datasets import load_dataset


system_prompt = """\
You are an assistant for a Python project, specifically helping to write test cases based on a bug report.

When the user provides a bug report, your tasks are as follows:
1. Find and recommend some appropriate test functions based on the bug report.
2. Rank the selected test functions in order of relevance, with the most relevant one first.
3. Output the name of test function and its file path. The result should contain at most five test cases.

### Available Tools:
- `list_root()`: Lists all files and directories inside the root test folder of the project. You may call this function first.
- `list_folder(path)`: Lists files and directories at the given path.  
- `list_classes_and_functions(file_path)`: Lists all classes and functions in a given file.  
- `read_function(file_path, function_name)`: Reads the source code of a specific function.  

You may need to call list_folder multiple times, including on subdirectories, to explore the full directory structure and locate the appropriate test file. \
To explore subdirectories, you need to use the relative path (e.g., tests/folder1/folder2). 
If you find that the initial path you accessed is not suitable, you can access the directory, files, and functions multiple times to find the most appropriate ones.
The maximum number of steps is 10.

### Required Output Format:
Your output must follow this structure:
```python
[
    ["path/to/test_file_x.py", "test_function_m"],
    ...
    ["path/to/test_file_y.py', "test_function_n"]
]
```
This list should contain **at most five** entries, ranked from most to least relevant.
"""

def extract_function_call(chunks):
    tool_call_map = {}  # index -> tool_call dict
    role = "assistant"
    content_parts = []
    
    for chunk in chunks:
        choices = chunk.get("choices", [])
        for choice in choices:
            delta = choice.get("delta", {})
            if 'content' in delta and delta['content'] is not None:
                content_parts.append(delta['content'])
            tool_calls = delta.get("tool_calls", [])
            if not tool_calls:
                continue
            for tool_call in tool_calls:
                index = tool_call.get("index")
                if index is None:
                    continue
                if index not in tool_call_map:
                    tool_call_map[index] = {
                        "id": tool_call.get("id"),
                        "type": tool_call.get("type"),
                        "function": {
                            "name": "",
                            "arguments": ""
                        }
                    }
                func = tool_call.get("function", {})
                if "name" in func and func["name"] is not None:
                    tool_call_map[index]["function"]["name"] = func["name"]
                if "arguments" in func and func["arguments"] is not None:
                    tool_call_map[index]["function"]["arguments"] += func["arguments"]

    # Construct response_message
    response_message = {
        "role": role,
        "content": ''.join(content_parts) if content_parts else None,
    }
    tool_calls = list(tool_call_map.values())
    if tool_calls:
        response_message["tool_calls"] = copy.deepcopy(tool_calls)

    return tool_calls, response_message

def chat_with_llm(instance, model_name, messages_path, restart=False):
    bug_id = instance['instance_id']
    bug_report = instance['problem_statement']
    
    func_calls = FunctionCalls(instance)
    
    api_key = API_KEY[model_name]
    base_url = BASE_URL[model_name]
    client = OpenAI(api_key=api_key, base_url=base_url)
    if os.path.exists(messages_path) and not restart:
        with open(messages_path, 'r') as f:
            messages = json.load(f)
        if messages[-1]['role'] == 'assistant':
            return
        first_time = False
    else:
        messages = [
            {
                "role": "system",
                "content": system_prompt 
            },
            {
                "role": "user",
                "content": f"# Bug Report\n{bug_report}"
            }
        ]
        first_time = True
    error_retries = 0
    while True:
        if first_time:
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=get_tools(),
                stream=True,
                timeout=60,
                temperature=0.0,
                tool_choice={"type": "function", "function": {"name": "list_root", "arguments": {}}}
            )
            first_time = False
        else:
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
        
        # Concatenate all messages
        tool_calls, response_message = extract_function_call(chunks)
        messages.append(response_message)
        if not tool_calls:
            with open(messages_path, 'w') as f:
                json.dump(messages, f, indent=4)
            break
        # if len(tool_calls) > 5:
        #     raise Exception('Too many tool calls!')
        
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
        if not os.path.exists(messages_path):
            os.makedirs(os.path.dirname(messages_path), exist_ok=True)
        with open(messages_path, 'w') as f:
            json.dump(messages, f, indent=4)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--related_tests_path", default="./retrieval_results/test/related_tests_1.json")
    parser.add_argument("--message_path", default="./retrieval_results/test/function_call/messages/")
    parser.add_argument("--swt", action="store_true", help="Whether to use the SWT-bench dataset.")
    parser.add_argument("--tdd", action="store_true", help="Whether to use the TDD-bench dataset.")
    parser.add_argument("--model", type=str, default="qwen-32b", help="LLM model to use.")

    args = parser.parse_args()

    use_swt = args.swt
    use_tdd = args.tdd
    if use_swt:
        ds = load_dataset("SWE-bench/SWE-bench_Lite")["test"]
        with open("swt.txt", "r") as f:
            swt = f.read().strip().split("\n")
        ds = [bug_report for bug_report in ds if bug_report["instance_id"] in swt]
    elif use_tdd:
        ds = load_dataset("princeton-nlp/SWE-bench_Verified")["test"]
        with open("tdd.txt", "r") as f:
            tdd = f.read().strip().split("\n")
        ds = [bug_report for bug_report in ds if bug_report["instance_id"] in tdd]
    
    flag = False

    related_tests = {}
    if os.path.exists(args.related_tests_path):
        with open(args.related_tests_path, 'r') as f:
            related_tests = json.load(f)
    else:
        os.makedirs(os.path.dirname(args.related_tests_path), exist_ok=True)
    
    for bug_report in ds:
        bug_id = bug_report["instance_id"]
        proj = bug_report['repo']
        if bug_id in related_tests:
            continue

        print(f'Generating {bug_id}')

        initialize(bug_report)
        
        if os.path.exists(args.message_path) == False:
            os.makedirs(args.message_path)
        
        messages_path = os.path.join(args.message_path, f"{bug_id}.json")
        chat_with_llm(bug_report, model_name=args.model, messages_path=messages_path, restart=args.restart)

        if os.path.exists(messages_path):
            with open(messages_path, 'r') as f:
                messages = json.load(f)
        else:
            raise Exception('File not found')
        
        assert messages[-1]['role'] == 'assistant', bug_id
        content = messages[-1]['content']
        if not '```' in content:
            pass
        else:
            content = content.split('```python')[-1]
            content = content.split('```')[0]
        
        # Remove comment parts
        s_clean = re.sub(r"#.*", "", content)  # Remove # and the content following it
        if s_clean.endswith(',\n]\n'):
            s_clean = s_clean.replace(',\n]\n', ']\n')
        tests = json.loads(s_clean)
        # results[bug_id] = tests
        related_tests[bug_id] = get_related_test(proj, tests)
    with open(args.related_tests_path, 'w') as f:
        json.dump(related_tests, f, indent=4)
    