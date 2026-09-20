
import os
import codecs
import subprocess as sp
import json
import tokenize
from io import StringIO
import ast
import glob
import astor

from os import path
from collections import Counter, defaultdict

from scripts.utils.swe_util import instance_id_to_proj, repo_path

SP_OUTPUT_SUPPRESS = True

class import_name:
    def __init__(self, name, asname = None):
        self.name = name
        self.asname = asname
        
    def __str__(self):
        if self.asname is not None:
            return f'{self.name} as {self.asname}'
        else:
            return f'{self.name}'
        
    def __eq__(self, other):
        return self.asname == other.asname and self.name == other.name
        
    def out_name(self):
        if self.asname is not None:
            return self.asname
        else:
            return self.name

class import_node:
    module : str
    imp_name : import_name
    
    def __init__(self, name, module = None):
        self.imp_name = name
        self.module = module
        
    def __str__(self):
        if self.module is None:
            return f'import {self.imp_name}'
        else:
            return f'from {self.module} import {self.imp_name}'
        
    def __eq__(self, other):
        return self.module == other.module and self.imp_name == other.imp_name

def get_attribute_prefix(node):
    """
    Recursively parse ast.Attribute nodes to get the full invocation prefix.
    """
    if isinstance(node, ast.Attribute):
        prefix = get_attribute_prefix(node.value)
        if prefix is None:
            return None
        return prefix + '.' + node.attr
    elif isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Subscript):
        return get_attribute_prefix(node.value)
    return None

def get_call_info(node):
    """
    Identify ast.Call nodes and retrieve the invocation prefix and function name separately.
    """
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            prefix = get_attribute_prefix(node.func.value)
            func_name = node.func.attr
            return prefix, func_name
        elif isinstance(node.func, ast.Name):
            return None, node.func.id
    return None, None

"""
For injecting LLM generated test
"""
def inject_test_to_best(repo_path, src_dir, test_dir, gen_test, needed_imports, needed_elements, version, bug_id, pos):
    proj = instance_id_to_proj(bug_id)

    best_path, best_file, best_test = get_best_test_class_for_injection(
        repo_path, test_dir, gen_test, bug_id, pos=pos)
    
    assert os.path.exists(best_path), f'File {best_path} does not exist'
    with codecs.open(best_path, 'r', encoding='utf-8', errors='ignore') as f:
        testf_lines = f.readlines()
        testf_content = ''.join(testf_lines)

    if proj == 'requests' and version != '2.10':
        needed_imports.append(import_node(import_name('unittest')))
    unhandled_imports= derive_unhandled_imports(testf_content, needed_imports)
    
    new_file_content, test_names = inject_with_imports(
        proj, best_path, best_test, testf_lines, gen_test, unhandled_imports, version)

    with open(best_path, 'w') as f:
        print(new_file_content, file=f)

    # Return name of test to execute
    test_names = [f"{best_file}::{test_name}" for test_name in test_names]

    return test_names

def get_best_retrieved_test_class(repo_path, test_dir, gen_test, bug_id, test_path):
    with open(test_path, 'r') as f:
        results = json.load(f)
    tests = results.get(bug_id, [])
    if len(tests) == 0:
        return insert_by_libro(repo_path, test_dir, gen_test)
        
    best_test = tests[0]
    best_file = best_test['file']
    best_path = os.path.join(repo_path, best_file)
    best_test_name = best_test['name']
    return best_path, best_file, best_test_name

def get_best_test_class_for_injection(repo_path, test_dir, gen_test, bug_id, pos):
    
    if pos == 'libro':
        return insert_by_libro(repo_path, test_dir, gen_test)
    else:
        return get_best_retrieved_test_class(repo_path, test_dir, gen_test, bug_id, pos)

def insert_by_libro(repo_path, test_dir, gen_test):
    # experimental similarity checker
    file_scores = defaultdict(float)
    gen_test_tokens = tokenize.generate_tokens(StringIO(gen_test).readline)
    test_tokens = set(token.string for token in gen_test_tokens if token.type == tokenize.NAME)
    
    # Get all matching directories
    test_dirs = glob.glob(os.path.join(repo_path, test_dir))
    
    for test_dir in test_dirs:
        for root, dirs, files in os.walk(test_dir, topdown=False):
            for name in [e for e in files if e.endswith('.py') and e.startswith('test_')]:
                filepath = path.join(root, name)
                with codecs.open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    file_cont = f.read()
                    # if 'abstract' in file_cont or '@RunWith(Parameterized.class)' in file_cont: 
                    #     if not is_injectable_test_class(file_cont, filepath, name.removesuffix('.java')):
                    #         continue
                    # if '@Ignore' in file_cont:
                    #     continue  # these files are ignored when testing
                    file_tokens = tokenize.generate_tokens(StringIO(file_cont).readline)
                    file_token_set = set(token.string for token in file_tokens if token.type == tokenize.NAME)
                    simsc = len(test_tokens & file_token_set)/len(test_tokens)
                    file_scores[filepath.removeprefix(repo_path)] += simsc

    # Identifying best file
    best_files = sorted(file_scores.keys(),
                        key=lambda x: (file_scores[x], x),
                        reverse=True)
    best_file = list(best_files)[0]
    if best_file.startswith('/'):
        best_file = best_file[1:]
    best_path = path.join(repo_path, best_file)

    return best_path, best_file, ""

def derive_unhandled_imports(test_class_content, needed_imports):
    tree = ast.parse(test_class_content)
    existing_imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            if node.names is not None:
                for alias in node.names:
                    name = import_name(alias.name, alias.asname)
                    existing_imports.append(import_node(name))
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                if node.names is not None:
                    for alias in node.names:
                        name = import_name(alias.name, alias.asname)
                        existing_imports.append(import_node(name, node.module))
                        
    unhandled_imports = []
    for needed_node in needed_imports:
        flag = False
        for exist_node in existing_imports:
            if exist_node == needed_node:
                flag = True
                break
        if not flag:
            unhandled_imports.append(needed_node)
        
    return unhandled_imports

def is_test_class_or_function(node):
    if isinstance(node, ast.ClassDef):
        if node.bases:
            for base in node.bases:
                if isinstance(base, ast.Name):
                    if 'Test' in base.id:
                        return True
                elif isinstance(base, ast.Attribute):
                    if 'Test' in base.attr:
                        return True
        for n in node.body:
            if isinstance(n, ast.FunctionDef) and 'test' in n.name:
                return True
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if 'test' in node.name:
            return True
    return False

def already_exists_in_code(code_content, test_name):
    tree = ast.parse(code_content)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == test_name:
            return True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name:
            return True
    return False

def style_align(node, inject_file_unittest_style, src_code):
    should_insert_to_class = False
    result_node = node
    test_name = node.name
    if inject_file_unittest_style:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            should_insert_to_class = True
            if len(node.args.args) == 0 or node.args.args[0].arg != 'self':
                node.args.args.insert(0, ast.arg(arg='self', annotation=None))
            result_node = node
            test_name = node.name
        elif isinstance(node, ast.ClassDef):
            if already_exists_in_code(src_code, node.name):
                # If the class already exists, delete it and insert into this class
                should_insert_to_class = True
                class_name = node.name
                for n in node.body:
                    if is_test_class_or_function(n):
                        result_node = n
                        test_name = n.name
                        return result_node, test_name, should_insert_to_class, class_name
    else:
        if isinstance(node, ast.ClassDef):
            for n in node.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_test_class_or_function(n):
                    test_name = n.name
                    if len(n.args.args) and n.args.args[0].arg == 'self':
                        n.args.args.pop(0)
                    result_node = n
                    return result_node, test_name, should_insert_to_class, None
    if already_exists_in_code(src_code, result_node.name):
        result_node.name = f'{result_node.name}_new'
        test_name = result_node.name
    
    return result_node, test_name, should_insert_to_class, None

def check_inject_file_tree(src_tree, node_tree):
    node_unittest_style = False
    for node in node_tree.body:
        if isinstance(node, ast.ClassDef) and is_test_class_or_function(node):
            node_unittest_style = True
            break
    src_is_pytest = False
    src_is_unittest = False
    for node in src_tree.body:
        if not src_is_pytest and isinstance(node, ast.FunctionDef) and 'test' in node.name:
            src_is_pytest = True
        if not src_is_unittest and isinstance(node, ast.ClassDef) and 'Test' in node.name:
            for n in node.body:
                if isinstance(n, ast.FunctionDef) and 'test' in n.name:
                    src_is_unittest  = True # If it stops here, be sure to take a look
                    break
    if src_is_unittest and src_is_pytest:
        # Both styles exist, follow the node's style
        return node_unittest_style
    # Otherwise, return whether it's unittest style
    
    return src_is_unittest
                

def handle_test_and_inject(proj, code, version, src_code, best_test):
    """
    Handle test functions,
    1. Split code into class and test
    Iterate through each block
        1. If it's an import, skip
        2. If it's not a test function or class, insert at the end
        3. For test functions:
            1. If source file is unittest style:
                1) If generated function is unittest style, insert directly at the end of the file
                2) If generated function is not unittest style, insert into the class
            2. If source file is not unittest style:
                Insert at the end after style alignment
    Args:
        code (str): Code content
        inject_file_unittest_style (bool): Whether the file is in unittest style
        src_code(str): Code of the file to be inserted into
    """
    tree = ast.parse(code)
    src_tree = ast.parse(src_code)
    inject_file_unittest_style = (proj == 'django') or check_inject_file_tree(src_tree, tree)
    src_lines = src_code.split('\n')
    new_body = []
    test_names = []
    
    for node in tree.body:
        test_name = None
        if isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom):
            continue
        elif isinstance(node, ast.FunctionDef):
            new_body.append(node)
                
        elif isinstance(node, ast.AsyncFunctionDef):
            new_body.append(node)
                
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            continue
        elif isinstance(node, ast.ClassDef):
            new_body.append(node)
        else:
            continue
    tree.body = new_body
    
    
    for node in tree.body:
        if not is_test_class_or_function(node):
            # Insert at the end
            if proj == 'requests' and version != '2.10':
                # Must insert before if __name__ == '__main__'
                insert_loc = -1
                for idx, n in enumerate(src_lines):
                    if 'if __name__' in n and '__main__' in n:
                        insert_loc = idx
                assert insert_loc != -1
                src_lines.insert(insert_loc, astor.to_source(node))
            else:
                # Insert at the end of the file
                src_lines.append(astor.to_source(node))
        else:
            # 1. Style matching, rename if names duplicate
            node, test_name, should_insert_to_class, class_name = style_align(node, inject_file_unittest_style, src_code)
            # 2. Insertion
            if should_insert_to_class:
                if best_test != '':
                    # Insert into class
                    if class_name is None:
                        class_name, end_lineno = get_insert_class(best_test, src_tree)
                    else:
                        end_lineno = get_insert_lineno(class_name, src_tree)
                    # 3. Insert into class, pay attention to indentation
                    src_lines = src_lines[:end_lineno] + ['\n'] + \
                        [f"    " + line for line in astor.to_source(node).split('\n')] + \
                        src_lines[end_lineno:]
                    test_names.append(f"{class_name}.{test_name}")
                else:
                    # Add class TestAutoGen(TestCase) to the end
                    class_def_node = ast.ClassDef(
                        name='TestAutoGen',
                        bases=[ast.Name(
                            id = 'TestCase' if proj == 'django' else 'unittest.TestCase', 
                            ctx=ast.Load()
                        )],
                        keywords=[],
                        body=[node],
                        decorator_list=[]
                    )
                    if proj == 'requests' and version != '2.10':
                        # Must insert before if __name__ == '__main__'
                        insert_loc = -1
                        for idx, n in enumerate(src_lines):
                            if 'if __name__' in n and '__main__' in n:
                                insert_loc = idx
                        assert insert_loc != -1
                        code = astor.to_source(class_def_node)
                        src_lines.insert(insert_loc, code)
                    else:
                        # Insert at the end of the file
                        code = astor.to_source(class_def_node)
                        src_lines.append(code)
                    test_names.append(f"TestAutoGen")
            else:
                # Insert at the end of the file
                if proj == 'requests' and version != '2.10':
                    # Must insert before if __name__ == '__main__'
                    insert_loc = -1
                    for idx, n in enumerate(src_lines):
                        if 'if __name__' in n and '__main__' in n:
                            insert_loc = idx
                    assert insert_loc != -1
                    src_lines.insert(insert_loc, astor.to_source(node))
                else:
                    # Insert at the end of the file
                    src_lines.append(astor.to_source(node))
                test_names.append(test_name)
    return '\n'.join(src_lines), test_names
            

def get_insert_class(best_test_name, tree):
    if '.' in best_test_name:
        best_class = best_test_name.split('.')[0]
        target_function_name = best_test_name.split('.')[-1]
    else:
        best_class = None
        target_function_name = best_test_name
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == target_function_name:
            raise ValueError(f"[get_insert_class] Should not reach hear?")
        elif isinstance(node, ast.ClassDef):
            class_name = node.name
            if best_class and class_name == best_class:
                return class_name, node.end_lineno
            for n in node.body:
                if isinstance(n, ast.FunctionDef) and n.name == target_function_name:
                    return class_name, n.end_lineno
    assert False, f'Function {best_test_name} not found'

def get_insert_lineno(class_name, tree):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node.end_lineno
    assert False, f'Class {class_name} not found'

def inject_with_imports(proj, best_classpath, best_test, testf_lines, gen_test, unhandled_imports, version):
    new_test_lines = testf_lines[:]
    # TODO: imports might conflict with existing ones, e.g., existing import X, adding from X import X
    # Adding necessary imports
    import_loc = -1
    for idx, line in enumerate(testf_lines):
        if 'from __future__ import ' in line:
            continue
        if 'import' in line:
            import_loc = idx
            break
    assert import_loc != -1, best_classpath
    new_test_lines = (
        new_test_lines[:import_loc] +
        [f'{ncp}\n' for ncp in unhandled_imports] +
        new_test_lines[import_loc:]
    )
    new_src_code = ''.join(new_test_lines)
    
    return handle_test_and_inject(proj, gen_test, version, new_src_code, best_test)


def get_most_common_item(iterator):
    counts = Counter(iterator)
    return max(counts.keys(), key=counts.__getitem__)


def parse_method(gen_test):
    """_summary_

    Args:
        gen_test (str): test code

    Returns:
        ast: Returns the syntax tree
    """
    tree = ast.parse(gen_test)
    return tree


def file_defines_attr(file_path, attr):
    with codecs.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        file_cont = f.read()
        try:
            tree = ast.parse(file_cont)
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name == attr:
                    return True
            # This one?
            elif isinstance(node, ast.ClassDef):
                if node.name == attr:
                    return True
                
    return False

def try_import(repo_path, env_name, node):
    cmd = f'eval "$(conda shell.bash hook)" && conda activate {env_name} && python -c "{node}"'
    result = sp.run(cmd, shell=True, executable='bash', capture_output=True, cwd=repo_path)
    return result.returncode == 0

def install_package(repo_path, env_name, name):
    cmd = f'eval "$(conda shell.bash hook)" && conda activate {env_name} && pip install {name}'
    result = sp.run(cmd, shell=True, executable='bash', capture_output=True, cwd=repo_path)
    return result.returncode == 0

def needed_imports(repo_path, src_dir, gen_test, env_name):
    """_summary_

    Args:
        repo_path (_type_): 
        src_dir (_type_): 
        gen_test (_type_): 

    Returns:
        module_paths: Module paths
        needed_class_stubs: Required classes
        needed_asserts: Required assertions
    """
    # 1. Get AST
    tree = parse_method(gen_test)

    # 2. Define node types to handle
    # Traverse AST to get all imports and calls
    func_def = []
    class_def = []
    # All attributes to be imported, split into prefix and func_name, i.e., prefix.func_name
    needed_attr = set()
    # Three types: import, from import, import as
    imports = []
    def_names = []
    for node in tree.body:
        # If it's np.array, need import numpy as np
        # If it's a direct function call, then from a import b
        # Save import and from import; if it's project_name.xxx, check if the file location is correct
        
        if isinstance(node, ast.Import):
            if node.names is not None:
                for alias in node.names:
                    name = import_name(alias.name, alias.asname)
                    imports.append(import_node(name))
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                if node.names is not None:
                    for alias in node.names:
                        name = import_name(alias.name, alias.asname)
                        imports.append(import_node(name, node.module))
        elif isinstance(node, ast.FunctionDef):
            to_remove = []
            for n in node.body:
                if isinstance(n, ast.Import):
                    if n.names is not None:
                        for alias in n.names:
                            name = import_name(alias.name, alias.asname)
                            imports.append(import_node(name))
                    to_remove.append(n)
                elif isinstance(n, ast.ImportFrom):
                    if n.module is not None:
                        if n.names is not None:
                            for alias in n.names:
                                name = import_name(alias.name, alias.asname)
                                imports.append(import_node(name, n.module))
                    to_remove.append(n)
            for n in to_remove:
                node.body.remove(n)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            prefix, func_name = get_call_info(node)
            if func_name not in func_def:
                needed_attr.add((prefix, func_name))
        
    # Traverse imports to check if paths are correct
    # Filter valid imports
    filtered_imports = []
    not_imported = []
    for node in imports:
        # Abandon static analysis, try importing directly
        result = try_import(repo_path, env_name, node)

        if result:
            filtered_imports.append(node)
        else:
            not_imported.append(node.imp_name)
    
    # not_imported list of tuples: (module, name)        
    # Iterate through not_imported, traverse all files to find the corresponding one
    for name in not_imported:
        flag = False
        
        # Try import name first
        node = import_node(name, None)
        result = try_import(repo_path, env_name, node)
        if result:
            filtered_imports.append(node)
            continue
        
        # Look for from ? import name
        for root, dirs, files in os.walk(path.join(repo_path, src_dir), topdown=False):
            if root.endswith('tests'):
                continue
            file_name = name.name.split('.')[-1] + '.py'
            for file in [e for e in files if e.endswith('.py')]:
                if file == '__init__.py' and root.split('/')[-1] == name.name:
                    from_ = root.replace(f'/{name.name}', '').replace(repo_path, '').replace('/', '.')
                    node = import_node(name, from_)
                    # result = try_import(repo_path, env_name, node)
                    # if result:
                    filtered_imports.append(node)
                    flag = True
                    break
                    
                elif file_name == file:
                    from_ = root.replace(repo_path, '').replace('/', '.')
                    node = import_node(name, from_)
                    # result = try_import(repo_path, env_name, node)
                    # if result:
                    filtered_imports.append(node)
                    flag = True
                    break
                else:
                    file_path = path.join(root, file)
                    if file_defines_attr(file_path, name.name):
                        from_ = file_path.replace(repo_path, '').replace('.py', '').replace('/', '.')
                        node = import_node(name, from_)
                        # result = try_import(repo_path, env_name, node)
                        # if result:
                        filtered_imports.append(node)
                        flag = True
                        break
            if flag:
                break
                    
        if not flag:
            # install_package(repo_path, env_name, name.name)
            print(f'Warning: {name} do not exists.')
    if (None, 'StringIO') in needed_attr:
        filtered_imports.append(import_node(import_name('StringIO'), 'io'))
        
    return filtered_imports, needed_attr

def proj_identifying_class(proj):
    # if proj == 'Closure':
    #     return 'google'
    # elif 'Jackson' in proj:
    #     return 'jackson'
    # elif 'sslcontext' in proj:
    #     return 'altindag.ssl'
    # else:
    return proj.lower()


def get_token_similarity(bug_report_tokens, test_tokens):
    bug_report_tokens = set(bug_report_tokens)
    test_tokens = set(test_tokens)
    return len(bug_report_tokens & test_tokens) / len(test_tokens)


def find_between(s, first, last):
    try:
        start = s.index(first) + len(first)
        end = s.index(last, start)
        return s[start:end]
    except ValueError:
        return ""


def process_result(result_json_path, gen_test_path, all_bugs):
    """
    Load execution result & return result dictionary (attaching parse error information)
    
    Returns:
    result_processed[bug_id][filename] = {
        'parse_error',
        'compile_error',
        'has_error',
        'buggy_output',
        'is_fib',
        'success',
        'test_file_path'
    }
    """
    with open(result_json_path, 'r') as f:
        result = json.load(f)
    
    # with open(os.path.join(os.path.dirname(__file__), '../data/SWE/invalid_bug_reports.txt')) as f:
    #     invalid_bugs = [e.strip().replace('-', '_') for e in f.readlines()]

    result_processed = defaultdict(dict)
    for bug_id, test_results in result.items():
        if bug_id not in all_bugs:
            continue
        for filename, test_result in test_results.items():
            if not filename.endswith('.txt'):
                filename = filename + '.txt'
            test_file = os.path.join(gen_test_path, filename)
            result_processed[bug_id][filename] = test_result

            if isinstance(test_result, str):
                result_processed[bug_id][filename] = {
                    'parse_error': True,
                    'compile_error': False,
                    'has_error': True,
                    'buggy_output': None,
                    'is_fib': False,
                    'success': False,
                    'test_file_path': test_file
                }
                continue

            result_processed[bug_id][filename]['fib_test_id'] = test_result['buggy']['failed_tests'][0].strip() if len(test_result['buggy']['failed_tests']) > 0 else None

            compile_error_in_fixed = (test_result['fixed']['compile_error']) if test_result['fixed'] is not None and not isinstance(test_result['fixed'], str) else False
            runtime_error_in_fixed = (test_result['fixed']['runtime_error']) if test_result['fixed'] is not None and not isinstance(test_result['fixed'], str) else False

            error_in_fixed = compile_error_in_fixed or runtime_error_in_fixed

            result_processed[bug_id][filename]['parse_error'] = False
            result_processed[bug_id][filename]['compile_error'] = test_result['buggy']['compile_error'] or compile_error_in_fixed
            result_processed[bug_id][filename]['has_error'] = test_result['buggy']['compile_error'] or test_result['buggy']['runtime_error'] or error_in_fixed
            result_processed[bug_id][filename]['test_file_path'] = test_file
            result_processed[bug_id][filename]['buggy_output'] = '\n'.join(test_result['buggy']['fib_error_msg']) if 'fib_error_msg' in test_result['buggy'] else None

            result_processed[bug_id][filename]['is_fib'] = test_result['buggy']['autogen_failed'] and (not error_in_fixed)
            result_processed[bug_id][filename]['success'] = test_result['success'] and (not error_in_fixed)

    return result_processed


def count_test_tokens(test_content):
    test_content = test_content.strip().strip('```')
    file_tokens = list(tokenize.generate_tokens(StringIO(test_content).readline))
    tokens_wo_comments = [token for token in file_tokens if token.type != tokenize.COMMENT]
    return len(file_tokens)

def count_assertions(test_content: str) -> int:
    """
    Calculate the number of assertions in a given Python code string.
    This includes the 'assert' keyword and function calls starting with 'assert'.
    """
    # Ignore syntax errors that might cause parsing failure
    try:
        # Use StringIO to fake the string as a file for tokenize to read
        tokens = tokenize.generate_tokens(StringIO(test_content).readline)
        
        # Count using a generator expression
        # 1. token.string == 'assert' captures the 'assert' keyword
        # 2. token.string.startswith('assert') captures 'assertEqual', 'assertIn', etc. assertion functions
        count = sum(1 for token in tokens 
                    if token.string == 'assert' or 
                       (token.type == tokenize.NAME and token.string.startswith('assert')))
        return count
    except (tokenize.TokenError, IndentationError):
        # If the code snippet has a syntax error and cannot be parsed, return 0
        return 0
