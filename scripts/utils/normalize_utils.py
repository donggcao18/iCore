import ast
from collections import defaultdict
import difflib
import re
import astor

"""
Common helper functions
"""
def normalize_test(test_content):
    '''Removes comments, normalizes method name and
    variable names that are declared within the test method.'''
    test_content = test_content.strip().strip('```')
    file_lines = test_content.split('\n')
    try:
        file_parse_tree = ast.parse(test_content)
    except Exception as e:
        return test_content
    replace_to = dict()
    var_counter = 0

    # finding names to replace (not replacing yet)
    class MethodVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            replace_to[node.name] = 'testMethodAutoGen'
            self.generic_visit(node)
    
    class VariableVisitor(ast.NodeVisitor):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Store):
                nonlocal var_counter
                replace_to[node.id] = f'var{var_counter}'
                var_counter += 1
            self.generic_visit(node)
            
    method_visitor = MethodVisitor()
    variable_visitor = VariableVisitor()
    
    method_visitor.visit(file_parse_tree)
    variable_visitor.visit(file_parse_tree)
    
    norm_test_lines = file_lines[:]
    line_delta = defaultdict(int)
    handled_lines = set()  # for removing comments
    
    class TokenVisitor(ast.NodeVisitor):
        def visit(self, node):
            if hasattr(node, 'lineno') and hasattr(node, 'col_offset'):
                line = node.lineno - 1
                col = node.col_offset
                handled_lines.add(line)
                tokstr = None
                if isinstance(node, ast.FunctionDef):
                    tokstr = node.name
                elif isinstance(node, ast.Name):
                    tokstr = node.id
                
                if tokstr and tokstr in replace_to:
                    prev_delta = line_delta[line]
                    norm_test_lines[line] = (
                        norm_test_lines[line][:col + prev_delta] +
                        norm_test_lines[line][col + prev_delta:].replace(
                            tokstr, replace_to[tokstr], 1))
                    line_delta[line] += len(replace_to[tokstr]) - len(tokstr)
            self.generic_visit(node)
    
    token_visitor = TokenVisitor()
    token_visitor.visit(file_parse_tree)
    
    noncomment_lines = [e for idx, e in enumerate(norm_test_lines)
                        if idx in handled_lines]
    return '\n'.join(noncomment_lines)

# Find test code snippets in fib_error_msg via fuzzy matching
def replace_code(code, msg_lines):
    try:
        tree = ast.parse(code)
        code = astor.to_source(tree)
    except Exception as e:
        code = code
    code_lines = [line.strip() for line in code.splitlines() if line.strip()]
    normalized_code = ' '.join(code_lines)

    def replace_if_match(line):
        if len(line) == 0:
            return line
        s = difflib.SequenceMatcher(None, normalized_code, line)
        match = s.find_longest_match(0, len(normalized_code), 0, len(line))
        # If the ratio of the matched part exceeds a certain threshold, it's considered a matching code snippet
        if match.size > 0.8 * len(line):
            return '[CODE]'
        return line

    return [replace_if_match(line.strip()) for line in msg_lines]


def replace_memory_address(lines):
    new_lines = []
    for line in lines:
        new_line = re.sub(r'0x[0-9a-fA-F]+', '[MEM_ADDR]', line)
        new_line = re.sub(r'\.py:[0-9]+', '.py:[LINE_NUM]', new_line)
        new_line = re.sub(r'line [0-9]+', 'line [LINE_NUM]', new_line)
        new_line = re.sub(r'in [0-9]+(\.[0-9]+)*s', 'in [TIME]s', new_line)
        new_line = re.sub(r'\^+', '^', new_line)
        new_line = re.sub(r'-+', '-', new_line)
        new_line = re.sub(r'_+', '_', new_line)
        new_line = re.sub(r'=+', '=', new_line)
        new_lines.append(new_line)
    return new_lines

def normalize_test_result(test_file, test_result, ignore_replace = []):
    fib_test_id = test_result['failed_tests'][0].strip() if len(test_result['failed_tests']) > 0 else ''
    if '::' in fib_test_id:
        testid_name = fib_test_id.split(' ')[0].split('::')
        fib_test_id = testid_name[0]
        test_name = testid_name[1] if len(testid_name) > 1 else None
        test_path = None
    else:
        test_name = fib_test_id.split(' ')[0]
        fib_test_id = fib_test_id.split(')')[0].split('(')[-1]
        test_path = fib_test_id.split('.')[:-1]
        test_path = '/'.join(test_path)
    
    test_file_path = test_file
    with open(test_file_path) as f:
        gen_test = f.read()
        gen_test = gen_test.strip()
    rep_lines = test_result['fib_error_msg']
    # print(rep_lines)
    if 'code' not in ignore_replace:   
        rep_lines = replace_code(gen_test, rep_lines)
    if 'memory_address' not in ignore_replace:
        rep_lines = replace_memory_address(rep_lines)
    rep = '\n'.join(rep_lines)
    rep = rep.replace(fib_test_id, '[TEST_ID]')
    if test_name is not None:
        rep = rep.replace(test_name, '[TEST_NAME]')
    if test_path is not None:
        rep = rep.replace(test_path, '[TEST_PATH]')
        
    return rep