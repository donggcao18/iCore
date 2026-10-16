"""Add generated source without rewriting existing code or inferring imports."""

import ast
import codecs
import difflib
import json
import re
import textwrap
from collections import defaultdict


CONTRACT_FIELDS = ('trigger', 'expected_behavior', 'distinguishing_assertion')


def parse_candidate(answer):
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError('Model returned no candidate')
    text = re.sub(r'<think>.*?</think>', '', answer, flags=re.S).strip()
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if match:
        text = match.group(1)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('Candidate must be a JSON object')
    for field in (*CONTRACT_FIELDS, 'test_code'):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f'Missing nonempty candidate field: {field}')
    for field in ('additional_imports', 'helpers'):
        value.setdefault(field, '')
        if not isinstance(value[field], str):
            raise ValueError(f'{field} must be source text')
    return value


def bindings(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            for field in ('body', 'orelse', 'finalbody'):
                names.update(bindings(ast.Module(body=getattr(node, field, []), type_ignores=[])))
            for handler in getattr(node, 'handlers', []):
                names.update(bindings(ast.Module(body=handler.body, type_ignores=[])))
    return names


def materialize(source, host, candidate):
    """Return full source, exact runner selector, and one reproducible test diff."""
    coding = re.search(r'coding[:=]\s*([-\w.]+)', '\n'.join(source.splitlines()[:2]))
    if coding and codecs.lookup(coding.group(1)).name not in {'utf-8', 'ascii'}:
        raise ValueError('Non-UTF8 host encodings require a byte-preserving patch adapter')
    tree = ast.parse(source)
    imports = ast.parse(candidate['additional_imports'])
    helpers = ast.parse(candidate['helpers'])
    test_code = textwrap.dedent(candidate['test_code']).strip() + '\n'
    tests = ast.parse(test_code)
    if len(tests.body) != 1 or not isinstance(tests.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise ValueError('test_code must contain exactly one test function, without a class wrapper')
    test = tests.body[0]
    if not test.name.startswith('test_brt_'):
        raise ValueError('Use a unique test_brt_ name')
    args = test.args.posonlyargs + test.args.args
    if host.class_name and (not args or args[0].arg != 'self'):
        raise ValueError('A class host requires an explicit self argument')
    if not host.class_name and args and args[0].arg in {'self', 'cls'}:
        raise ValueError('A module host cannot accept self/cls')
    for node in imports.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)) or getattr(node, 'module', '') == '__future__':
            raise ValueError('additional_imports accepts ordinary imports only')
        if any(alias.name == '*' for alias in node.names):
            raise ValueError('Wildcard imports cannot be checked for collisions')
    for node in helpers.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)):
            raise ValueError('helpers accepts definitions and constants only')
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test'):
            raise ValueError('Place the single generated test in test_code')
        if isinstance(node, ast.ClassDef) and any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                               and n.name.startswith('test') for n in node.body):
            raise ValueError('Helper classes cannot contain additional tests')
    existing = bindings(tree)
    existing_imports = {ast.dump(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))}
    import_nodes = [n for n in imports.body if ast.dump(n) not in existing_imports]
    added = bindings(ast.Module(body=import_nodes + helpers.body, type_ignores=[]))
    if existing & added:
        raise ValueError(f'New imports/helpers shadow existing names: {sorted(existing & added)}')
    # Also reject generated definitions shadowing one another.
    groups = [bindings(ast.Module(body=[n], type_ignores=[])) for n in import_nodes + helpers.body]
    if sum(map(len, groups)) != len(set().union(*groups)):
        raise ValueError('Generated imports/helpers define the same name')
    host_class = next((n for n in tree.body if isinstance(n, ast.ClassDef)
                       and n.name == host.class_name), None)
    if host.class_name and host_class is None:
        raise ValueError('Selected host class no longer exists')
    if test.name in existing | added or (host_class and test.name in bindings(host_class)):
        raise ValueError('Generated test name already exists')
    lines = source.splitlines(keepends=True)
    edits = defaultdict(list)
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'
    if host_class:
        if host_class.lineno == host_class.end_lineno:
            raise ValueError('Single-line class hosts are unsupported; choose another host')
        indent = ' ' * host_class.body[0].col_offset
        class_start = min([host_class.lineno] + [n.lineno for n in host_class.decorator_list]) - 1
        if candidate['helpers'].strip():
            edits[class_start].append((1, '\n' + candidate['helpers'].strip() + '\n\n'))
        edits[host_class.end_lineno].append((2, '\n' + textwrap.indent(test_code, indent)))
    else:
        edits[len(lines)].append((2, '\n\n' + candidate['helpers'].strip() + '\n\n' + test_code))
    # Insert imports after the docstring and future imports. Existing
    # bytes, including decorators and class setup, are otherwise untouched.
    insertion = 0
    for position, node in enumerate(tree.body):
        if ((position == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
             and isinstance(node.value.value, str))
                or isinstance(node, ast.ImportFrom) and node.module == '__future__'):
            insertion = node.end_lineno
        else:
            break
    if insertion == 0:
        # Keep encoding cookies, shebangs, and leading comments in their place.
        insertion = tree.body[0].lineno - 1 if tree.body else len(lines)
        if tree.body and getattr(tree.body[0], 'decorator_list', []):
            insertion = min(n.lineno for n in tree.body[0].decorator_list) - 1
    additions = '\n'.join(ast.get_source_segment(candidate['additional_imports'], n)
                          for n in import_nodes)
    if additions.strip():
        edits[insertion].append((0, '\n' + additions.strip() + '\n\n'))
    for position in sorted(edits, reverse=True):
        lines.insert(position, ''.join(code for _, code in sorted(edits[position])))
    result = ''.join(lines)
    compile(result, host.file, 'exec')
    selector = host.file + '::' + (host.class_name + '::' if host.class_name else '') + test.name
    patch = ''.join(difflib.unified_diff(source.splitlines(keepends=True), result.splitlines(keepends=True),
                                       fromfile='a/' + host.file, tofile='b/' + host.file))
    # difflib omits Git's special marker for a missing terminal newline.
    if source and not source.endswith('\n'):
        raise ValueError('Host lacks a terminal newline; choose another host')
    return dict(file=host.file, source=result, selector=selector, patch=patch)
