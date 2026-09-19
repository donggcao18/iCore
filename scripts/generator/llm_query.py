# coding: utf-8
from pathlib import Path
import os
import re
import json
from scripts.config import ROOT_DIR
import argparse
from tqdm import tqdm
from datasets import load_dataset
from scripts.generator.make_prompt_util import get_aegis_context, get_assertflip_context, get_bm25_context, get_otter_focal_funcs, get_retrieval_docs, get_related_test
from scripts.utils.llm_api import query_llm

def get_relevant_docs(instance_id, docs_path):
    with open(docs_path) as f:
        docs = json.load(f)
        
    docs = docs[instance_id]
    relevant_docs = []
    i = 0
    for doc in docs:
        content = doc['results']
        if content:
            content = content[0]['contents']
            relevant_docs.append(content)
        i += 1
        if i == 5:
            break
    
    return '\n'.join(relevant_docs)

def make_messages_from_dataset(exp_name, bug_report, context_code_path, context_test_path, template_file):
    prompt_save_path = f'{ROOT_DIR}/data/{exp_name}/prompts/{bug_report["instance_id"]}.json'
    if os.path.exists(prompt_save_path):
        with open(prompt_save_path, 'r') as f:
            return json.load(f)

    bug_report_content = bug_report['problem_statement']
    with open(template_file) as f:
        messages = json.load(f)
    
    system_content = messages[0]['content']
    match = re.search(r'%\{(.*?)\}', system_content)
    if match:
        # group(1) gets the content of the first capture group, which is the filename
        filename = match.group(1)
        file_path = Path(template_file).parent / filename
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
            
            # Replace original content
            messages[0]['content'] = file_content

        except FileNotFoundError:
            print(f"Error: File '{file_path}' not found. Content remains unchanged.")
        except Exception as e:
            print(f"Unknown error occurred while reading file: {e}. Content remains unchanged.")

    current_query = messages[-1]['content']
    current_query = current_query.replace('{{bug_report_content}}', bug_report_content)
    bug_id = bug_report['instance_id']

    if template_file.endswith('prompt_with_context.json'):
        if exp_name == 'aegis':
            relevant_docs = get_aegis_context(bug_id)
        elif exp_name == 'bm25':
            relevant_docs = get_bm25_context(bug_id)
        elif exp_name == 'assertflip':
            relevant_docs = get_assertflip_context(bug_id)
        current_query = current_query.replace('{{relevant_docs}}', relevant_docs)
    else:
        if exp_name == 'otter':
            relevant_docs = get_otter_focal_funcs(bug_id, context_code_path)
            current_query = current_query.replace('{{relevant_docs}}', relevant_docs)
        elif context_code_path:
            relevant_docs = get_retrieval_docs(bug_id, context_code_path)
            current_query = current_query.replace('{{relevant_docs}}', relevant_docs)
        if context_test_path:
            related_tests = get_related_test(bug_id, context_test_path)
            current_query = current_query.replace('{{related_tests}}', related_tests)
    messages[-1]['content'] = current_query

    return messages

def query_llm_for_gentest(
    # proj, bug_id, 
    exp_name, model, bug_report, context_code_path, context_test_path, template_file, save_prompt=False, prompt_save_path=None, save_message=False, temperature=0.7):
    
    # chat_mode = model_is_chat(model)
    
    prompt = make_messages_from_dataset(exp_name, bug_report, context_code_path, context_test_path, template_file)

    if save_prompt:
        ext = 'json'
        if prompt_save_path is None:
            prompt_save_path = f'{ROOT_DIR}/data/{exp_name}/prompts/{bug_report["instance_id"]}.json'
        if not os.path.exists(prompt_save_path):
            Path(prompt_save_path).parent.mkdir(parents=True, exist_ok=True)
            with open(prompt_save_path, 'w') as f:
                if ext == 'json':
                    json.dump(prompt, f, indent=2)
                else:
                    f.write(prompt)

    query_result = query_llm(prompt, model, temperature)
    
    # save chat messages
    if save_message:
        message = prompt
        message.append({"role": "assistant", "content": query_result})
        with open(f'{ROOT_DIR}/data/{exp_name}/chat_messages/{bug_report["instance_id"]}.json', 'w') as f:
            json.dump(message, f, indent=2)

    if query_result:
        gen_test = query_result
    else:
        return None
    if '<think>' in gen_test:
        gen_test = gen_test.split('</think>')[-1]
        
    tag_pattern = r'<result>.*?</result>'
    match = re.search(tag_pattern, gen_test, re.DOTALL)
    if match:
        gen_test = match.group(0)
        gen_test = gen_test.replace('<result>', '').replace('</result>', '').strip()
    
    code_pattern = r'```(?:python)?(.*?)```'
    match = re.search(code_pattern, gen_test, re.DOTALL)
    if match:
        gen_test = match.group(1).strip()
        
    return gen_test

def query_times(args, bug_report):
    times = args.query_time
    model = args.model
    for i in tqdm(range(0, times)):
        if args.out_dir is None:
            out = f'{ROOT_DIR}/data/{args.exp_name}/gen_tests_{model}/{bug_report["instance_id"]}_n{i+1}.txt'
        else:
            out = f'{args.out_dir}/{bug_report["instance_id"]}_n{i+1}.txt'
        if not args.retry and os.path.exists(out):
            continue
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(out), exist_ok=True)
        gen_test = query_llm_for_gentest(
            exp_name=args.exp_name,
            bug_report=bug_report,
            model = args.model,
            context_code_path=args.context_code_path,
            context_test_path=args.context_test_path,
            template_file=args.template_file, 
            save_prompt=args.save_prompt, 
            save_message=args.save_message,
            temperature=args.temperature
        )
        if gen_test is None:
            # retry
            continue
            
        with open(out, 'w', encoding='utf8') as f:
            f.write(gen_test)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--exp_name', default='final')
    parser.add_argument('--query_time', type=int, default=10)
    parser.add_argument('--context_code_path', default=None)
    parser.add_argument('--context_test_path', default=None)
    parser.add_argument('--out_dir', default=None)
    parser.add_argument('--save_prompt', action='store_true')
    parser.add_argument('--template_file', default='./data/prompt_templates/prompt_with_code_and_tests.json')
    parser.add_argument('--model', default='deepseek-chat')
    parser.add_argument('--save_message', action='store_true')
    parser.add_argument('--temperature', type=float, default=0.7)
    parser.add_argument('--from_id', default='')
    parser.add_argument('--to_id', default='')
    parser.add_argument('--swt', default=False, action='store_true')
    parser.add_argument('--tdd', default=False, action='store_true')
    parser.add_argument('--retry', default=False, action='store_true')
    args = parser.parse_args()
    with open('swt.txt', 'r') as f:
        swt = f.read().strip().split('\n')
    with open('tdd.txt', 'r') as f:
        tdd = f.read().strip().split('\n')
    with open('swt.txt', 'r') as f:
        skip = f.read().strip().split('\n')
    if args.swt:
        ds = load_dataset("SWE-bench/SWE-bench_Lite")["test"]
        ds = [bug_report for bug_report in ds if bug_report["instance_id"] in swt]
    elif args.tdd:
        ds = load_dataset("princeton-nlp/SWE-bench_Verified")["test"]
        ds = [bug_report for bug_report in ds if bug_report["instance_id"] in tdd]
    else:
        raise NotImplementedError

    if args.from_id == '':
        flag = True
    else:
        flag = False
    for bug_report in ds:
        bug_id = bug_report["instance_id"]
        if bug_id == args.from_id:
            flag = True
        if not flag:
            continue
        if args.to_id != '' and bug_id == args.to_id:
            break
        # if bug_id in skip:
        #     continue
        print("generating", bug_id)

        query_times(args, bug_report)