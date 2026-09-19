import json
import ast
import os

from scripts.utils.swe_util import repo_path

def get_retrieval_docs(instance_id, retrieval_result_path, all_content = False):
    with open(retrieval_result_path) as f:
        retrieval_results = json.load(f)
    retrieval_results = retrieval_results[instance_id]
    relevant_docs = []
    for keyword, doc in retrieval_results.items():
        if doc == None:
            continue
        obj_name = doc['obj_name']
        node_type = doc['node_type']
        path = doc['path']
        code_start_line = doc['code_start_line']
        code_end_line = doc['code_end_line']
        code_content = doc['code_content']
        parent_node = doc['parent']
        if all_content:
            with open(path) as f:
                code_content = f.read()
                code_content = code_content.split('\n')[code_start_line-1:code_end_line]
                code_content = '\n'.join(code_content)
        if node_type == 'class_function':
            obj_name = f'{parent_node}.{obj_name}'
        content = f'- {node_type}: {path} {obj_name}\n ```\n{code_content}\n```\n'
        if content not in relevant_docs:
            relevant_docs.append(content)
    return '\n'.join(relevant_docs)

def get_function_content(proj, file, function_name):
    if '.' in function_name:
        target_class_name, target_function_name = function_name.split('.')
    elif '::' in function_name:
        target_class_name, target_function_name = function_name.split('::')
    else:
        target_class_name = None
        target_function_name = function_name
    repo_dir = repo_path(proj)
    file_path = os.path.join(repo_dir, file)
    if not os.path.exists(file_path):
        print(f'File {file} not found in {proj}')
        return ""
    with open(file_path, 'r') as f:
        content = f.read()
    content_line = content.split('\n')
    tree = ast.parse(content)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_function_name:
            decorator_list = node.decorator_list
            decorator = ''
            for dec in decorator_list:
                decorator += '\n'.join(content_line[dec.lineno - 1:dec.end_lineno]) + '\n'
            return decorator + '\n'.join(content_line[node.lineno - 1:node.end_lineno])
        if isinstance(node, ast.ClassDef):
            class_decorator_list = node.decorator_list
            class_decorator = ''
            for dec in class_decorator_list:
                class_decorator += '\n'.join(content_line[dec.lineno - 1:dec.end_lineno]) + '\n'
            
            class_name = node.name
            class_line = content_line[node.lineno - 1]
            if target_class_name and class_name != target_class_name:
                continue
            for n in node.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == target_function_name:
                    decorator_list = n.decorator_list
                    decorator = ''
                    for dec in decorator_list:
                        decorator += '\n'.join(content_line[dec.lineno - 1:dec.end_lineno]) + '\n'
                    joined_lines = '\n'.join(content_line[n.lineno - 1:n.end_lineno])
                    test_content = f"{class_decorator}{class_line}\n{decorator}{joined_lines}"
                    return test_content
    print(f'Function {function_name} not found in {file}')
    return ""

def get_function_content_with_lineno(proj, file, function_name):
    if '.' in function_name:
        target_class_name, target_function_name = function_name.split('.')
    else:
        target_class_name = None
        target_function_name = function_name
    repo_dir = repo_path(proj)
    file_path = os.path.join(repo_dir, file)
    with open(file_path, 'r') as f:
        content = f.read()
    content_line = content.split('\n')
    tree = ast.parse(content)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_function_name:
            decorator_list = node.decorator_list
            decorator = ''
            for dec in decorator_list:
                decorator += '\n'.join(content_line[dec.lineno - 1:dec.end_lineno]) + '\n'
            return decorator + '\n'.join(content_line[node.lineno - 1:node.end_lineno]), node.lineno, node.end_lineno
        if isinstance(node, ast.ClassDef):
            class_decorator_list = node.decorator_list
            class_decorator = ''
            for dec in class_decorator_list:
                class_decorator += '\n'.join(content_line[dec.lineno - 1:dec.end_lineno]) + '\n'
            
            class_name = node.name
            class_line = content_line[node.lineno - 1]
            if target_class_name and class_name != target_class_name:
                continue
            for n in node.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == target_function_name:
                    decorator_list = n.decorator_list
                    decorator = ''
                    for dec in decorator_list:
                        decorator += '\n'.join(content_line[dec.lineno - 1:dec.end_lineno]) + '\n'
                    joined_lines = '\n'.join(content_line[n.lineno - 1:n.end_lineno])
                    return f"{class_decorator}{class_line}\n{decorator}{joined_lines}", n.lineno, n.end_lineno
    print(f'Function {function_name} not found in {file}')
    return "", -1, -1
    
def get_related_test(instance_id, path, size=-1):
    related_tests = get_related_test_list(instance_id, path, size=size)
    return '\n'.join(related_tests)

def get_related_test_list(instance_id, path, size=-1):
    with open(path, 'r') as f:
        results = json.load(f)
    test_files = results.get(instance_id, [])
    related_tests = []
    for i, test in enumerate(test_files):
        file = test["file"]
        test_name = test["name"]
        function_content = test["code_content"]
        related_tests.append(f'- {file} {test_name}\n ```\n{function_content}\n```\n')
        if size != -1 and i + 1 >= size:
            break
    return related_tests

def get_aegis_context(instance_id):
    with open('aegis_results/aegis_context.json', 'r') as f:
        results = json.load(f)
    return results[instance_id]

def get_assertflip_context(instance_id):
    with open('data/assertflip/assertflip_context.json', 'r') as f:
        results = json.load(f)
    return results[instance_id]

def get_bm25_context(bug_id):
    with open(f'data/swt-text/{bug_id}.txt', 'r') as f:
        text = f.read()
    if '<code>' not in text:
        return text
    ctx = text.split('<code>')[-1].split('</code>')[0]
    return ctx

def get_otter_focal_funcs(bug_id, context_path):
    with open(context_path, 'r') as f:
        results = json.load(f)
    focal_funcs = results.get(bug_id, [])
    if not focal_funcs:
        return ""
    relevant_docs = []
    for func in focal_funcs:
        file = func['file']
        name = func['name']
        content = func['code_content']
        add_content = f'- {file} {name}\n ```\n{content}\n```\n'
        if add_content not in relevant_docs:
            relevant_docs.append(add_content)
    return '\n'.join(relevant_docs)