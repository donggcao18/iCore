"""Extract observed pytest call paths to functions touched by an injecting diff."""

import argparse
import ast
import json
import os
import re
import sys
import tokenize
from pathlib import Path

import pytest


def patched_functions(checkout, patch):
    changed = {}
    path = None
    line_number = None
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            path = line_number = None
        elif line.startswith("+++ ") and line_number is None:
            target = line[4:]
            if target.startswith("b/") and target.endswith(".py"):
                path = checkout / target[2:]
                if not path.resolve().is_relative_to(checkout):
                    raise ValueError(f"Patch path is outside the checkout: {target}")
        elif match := re.match(r"^@@ .* \+(\d+)(?:,\d+)? @@", line):
            line_number = int(match[1])
        elif path is not None and line_number is not None:
            if line.startswith("+"):
                changed.setdefault(path, {})[line_number] = line[1:]
            if line.startswith(("+", " ")):
                line_number += 1

    roots = {}
    for path, lines in changed.items():
        with tokenize.open(path) as source:
            code = source.read()
        source_lines = code.splitlines()
        if any(n > len(source_lines) or source_lines[n - 1] != value
               for n, value in lines.items()):
            raise ValueError(f"Patch postimage does not match {path}")
        functions = [
            node for node in ast.walk(ast.parse(code, filename=str(path)))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for n, value in lines.items():
            if not value.strip() or value.lstrip().startswith("#"):
                continue
            enclosing = [node for node in functions if node.lineno <= n <= node.end_lineno]
            if enclosing:
                node = max(enclosing, key=lambda item: item.lineno)
                start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                roots.setdefault((path.resolve(), start, node.name), set()).add(n)
    if not roots:
        raise ValueError("No added or replaced Python lines mapped to a function")
    return roots


class CallPaths:
    def __init__(self, checkout, selector, roots):
        self.checkout = checkout
        self.selector = selector
        self.roots = roots
        self.root_names = {name for _, _, name in roots}
        self.entries = set()
        self.paths = {}
        self.outcome = None

    def describe(self, code, path, call_line):
        return (
            path.relative_to(self.checkout).as_posix(),
            code.co_qualname,
            code.co_firstlineno,
            call_line,
        )

    def profile(self, frame, event, arg):
        if event != "call":
            return
        code = frame.f_code
        if code in self.entries:
            return
        if code.co_name not in self.root_names:
            return
        path = Path(code.co_filename).resolve()
        root = (path, code.co_firstlineno, code.co_name)
        if root not in self.roots:
            return
        chain = []
        while frame is not None:
            code = frame.f_code
            path = Path(code.co_filename).resolve()
            caller = frame.f_back
            if path.is_relative_to(self.checkout):
                chain.append(self.describe(code, path, caller.f_lineno if caller else None))
            else:
                chain.append(("<external>", code.co_qualname, code.co_firstlineno, None))
            if code in self.entries:
                route = tuple(reversed(chain))
                self.paths[route] = self.paths.get(route, 0) + 1
                return
            frame = caller

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        if item.nodeid != self.selector:
            yield
            return
        # pytest's collected callable identifies the entry even when test names repeat.
        entry = getattr(item.obj, "__code__", None)
        self.entries = {entry} if entry is not None else set()
        previous = sys.getprofile()
        sys.setprofile(self.profile)
        try:
            yield
        finally:
            sys.setprofile(previous)

    def pytest_runtest_logreport(self, report):
        if report.nodeid == self.selector and report.when == "call":
            self.outcome = report.outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("pytest_args", nargs="*")
    args = parser.parse_args()
    checkout = args.checkout.resolve()
    output = args.output.resolve()
    tracer = CallPaths(
        checkout, args.selector, patched_functions(checkout, args.patch.read_text(encoding="utf-8-sig"))
    )
    os.chdir(checkout)
    exit_code = pytest.main([args.selector, *args.pytest_args], plugins=[tracer])
    routes = []
    for route, hits in sorted(tracer.paths.items(), key=lambda item: (len(item[0]), repr(item[0]))):
        routes.append({
            "hits": hits,
            "frames": [
                {"file": file, "function": name, "definition_line": line, "caller_line": call}
                for file, name, line, call in route
            ],
        })
    result = {
        "selector": args.selector,
        "pytest_exit_code": int(exit_code),
        "test_outcome": tracer.outcome,
        "mapped_functions": [
            {"file": path.relative_to(checkout).as_posix(), "definition_line": start,
             "name": name, "changed_lines": sorted(lines)}
            for (path, start, name), lines in sorted(tracer.roots.items())
        ],
        "observed_routes": routes,
        "selected_route": routes[0] if tracer.outcome == "failed" and routes else None,
    }
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if result["selected_route"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
