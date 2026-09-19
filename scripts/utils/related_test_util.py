import ast
import glob
import os

from tqdm import tqdm

from scripts.test_retrieval.utils import Test
from scripts.utils.swe_util import repo_path, swe_test_path_prefix

def list_all_tests_in_file(file_path) -> dict[str, str]:
    """
    List all test functions in a given file.
    Args:
        file: The absolute path to the file.
        Return: {
            test_name: test_content
        }
    """
    results = {}
    try:
        with open(file_path, 'r') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading file {file_path}: {e}")
        return {}
    content_line = content.split('\n')
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        print(f"Syntax error in file {file_path}: {e}")
        return {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test'):
            function_name = node.name
            decorator_list = node.decorator_list
            decorator = ''
            for dec in decorator_list:
                decorator += '\n'.join(content_line[dec.lineno - 1:dec.end_lineno]) + '\n'
            content = decorator + '\n'.join(content_line[node.lineno - 1:node.end_lineno])
            results[function_name] = content
        if isinstance(node, ast.ClassDef) and ('Test' in node.name or (node.bases and any('Test' in base.id for base in node.bases if isinstance(base, ast.Name)))):
            class_name = node.name
            class_decorator_list = node.decorator_list
            class_decorator = ''
            for dec in class_decorator_list:
                class_decorator += '\n'.join(content_line[dec.lineno - 1:dec.end_lineno]) + '\n'
            
            class_line = content_line[node.lineno - 1]
            for n in node.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and 'test' in n.name:
                    test_name = n.name
                    decorator_list = n.decorator_list
                    decorator = ''
                    for dec in decorator_list:
                        decorator += '\n'.join(content_line[dec.lineno - 1:dec.end_lineno]) + '\n'
                    joined_lines = '\n'.join(content_line[n.lineno - 1:n.end_lineno])
                    test_content = f"{class_decorator}{class_line}\n{decorator}{joined_lines}"
                    results[f"{class_name}.{test_name}"] = test_content
                    
    return results


def list_all_tests(bug_id, proj):
    # List all test objects
    proj_path = repo_path(proj)
    test_dir = swe_test_path_prefix(proj, bug_id) + '**/*.py'
    test_dir = os.path.join(proj_path, test_dir)
    test_files = glob.glob(test_dir, recursive=True)
    test_files = [test_file for test_file in test_files if test_file.split('/')[-1].startswith('test') or test_file.split('/')[-1].startswith('unittest')]
    all_test_objs:list[Test] = []
    # print(f"Found {len(test_files)} test files in {test_dir}.")

    for test_file in tqdm(test_files):
        rel_path = test_file.replace(proj_path, '')
        tests = list_all_tests_in_file(test_file)
        
        for test, test_content in tests.items():
            class_name = ''
            test_name = test
            if '.' in test:
                class_name = test.split('.')[0]
                test_name = test.split('.')[1]
            test_obj = Test(proj, rel_path, class_name, test_name, test_content)

            all_test_objs.append(test_obj)

    return all_test_objs


def list_all_functions_in_file(file_path):
    if not os.path.exists(file_path):
        print(f"Error: File not found at '{file_path}'")
        return []
    functions = []
    try:
        with open(file_path, "r", encoding="utf-8") as source_file:
            # Read file content and parse into abstract syntax tree
            tree = ast.parse(source_file.read(), filename=file_path)
        
        # Traverse the top-level nodes of the syntax tree
        for node in ast.walk(tree):
            # Check if the node is a function definition or an async function definition
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)

    except FileNotFoundError:
        print(f"Error: File not found at '{file_path}'")
    except SyntaxError as e:
        print(f"Error: Could not parse file '{file_path}' due to a syntax error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred while processing '{file_path}': {e}")
        
    return functions

def get_function_content(file_path, function_name):
    if not os.path.exists(file_path):
        print(f"Error: File not found at '{file_path}'")
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as source_file:
            # Read file content and parse into abstract syntax tree
            tree = ast.parse(source_file.read(), filename=file_path)
        
        # Traverse the top-level nodes of the syntax tree
        for node in ast.walk(tree):
            # Check if the node is a function definition or an async function definition
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == function_name:
                content_line = open(file_path, 'r').read().split('\n')
                decorator_list = node.decorator_list
                decorator = ''
                for dec in decorator_list:
                    decorator += '\n'.join(content_line[dec.lineno - 1:dec.end_lineno]) + '\n'
                content = decorator + '\n'.join(content_line[node.lineno - 1:node.end_lineno])
                return content
    except FileNotFoundError:
        print(f"Error: File not found at '{file_path}'")
    except SyntaxError as e:
        print(f"Error: Could not parse file '{file_path}' due to a syntax error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred while processing '{file_path}': {e}")

    print(f"Function {function_name} not found in {file_path}")
    return ""
        