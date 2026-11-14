"""Map repair hunks to Python functions in disposable snapshots."""

import ast
from difflib import SequenceMatcher
import re
import tokenize


def definitions(root, path):
    if not path or path == "/dev/null" or not path.endswith(".py"):
        return [], []
    source = root / path
    if not source.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes snapshot: {path}")
    with tokenize.open(source) as handle:
        text = handle.read()
    tree = ast.parse(text, filename=path)
    result = []

    def visit(node, scope):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = (*scope, node.name)
            if not isinstance(node, ast.ClassDef):
                start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                result.append(dict(file=path, function=".".join(scope),
                                   line=start, end=node.end_lineno))
                scope = (*scope, "<locals>")
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, ())
    return result, text.splitlines()


def map_targets(buggy, fixed, patch):
    targets, unresolved = {}, []
    for diff in re.split(r"(?m)(?=^diff --git )", patch):
        if not diff.strip():
            continue
        lines = diff.splitlines()
        old = next((l[4:].removeprefix("a/") for l in lines if l.startswith("--- ")), None)
        new = next((l[4:].removeprefix("b/") for l in lines if l.startswith("+++ ")), None)
        try:
            before, old_source = definitions(buggy, old)
            after, new_source = definitions(fixed, new)
        except (OSError, SyntaxError, ValueError) as exc:
            unresolved.append(dict(file=old or new, reason=str(exc)))
            continue
        if not any(path and path.endswith(".py") for path in (old, new)):
            unresolved.append(dict(file=old or new or lines[0], reason="non-Python change"))
            continue
        # Compare materialized files because Git can apply hunks at shifted offsets.
        for tag, i, j, k, m in SequenceMatcher(None, old_source, new_source).get_opcodes():
            if tag == "equal":
                continue
            for side, path, source, start, stop in (
                (before, old, old_source, i, j), (after, new, new_source, k, m)
            ):
                for offset in range(start, stop):
                    if not source[offset].strip():
                        continue
                    number = offset + 1
                    matches = [d for d in side if d["line"] <= number <= d["end"]]
                    if not matches:
                        unresolved.append(dict(file=path, line=number, reason="outside Python function"))
                        continue
                    symbol = min(matches, key=lambda d: d["end"] - d["line"])
                    candidates = ([symbol] if side is before else
                                  [d for d in before if d["function"] == symbol["function"]])
                    for item in candidates:
                        targets[item["file"], item["line"], item["function"]] = item
                    if not candidates:
                        unresolved.append(dict(file=path, line=number, reason="no buggy symbol"))
    return [targets[key] for key in sorted(targets)], unresolved
