"""Separate bounded-path oracle: historical tests related within 2 or 3 edges."""

from __future__ import annotations

import argparse
import ast
from collections import deque
from functools import lru_cache
import hashlib
from pathlib import Path
import re

from scripts.retrieval_formats import test_retrieval
from scripts.test_retrieval.augment_oracle import (
    _load, _save, apply_production_patch, base_sources, changed_symbols, production_changes,
)
from scripts.test_retrieval.extract_oracle import (
    DATASETS, ROOT, dataset_name, default_paths, ensure_repo, extract_instance,
    read_repo_rows, repository_name,
)
from scripts.test_retrieval.static_dependencies import DependencyIndex, FUNCTIONS


POLICY = "relaxed_exact_object_paths_v1"
FILES = ("related_tests_oracle_code_base.json", "related_tests_oracle_base_augmented.json",
         "oracle_augmented_manifest.json")


def body_nodes(node):
    """Definition decorators execute here; their nested bodies do not."""
    if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
        for decorator in node.decorator_list:
            yield decorator
        return
    if isinstance(node, (ast.Lambda, ast.GeneratorExp)):
        return
    yield node
    if isinstance(node, ast.AnnAssign):
        for child in (node.target, node.value):
            if child is not None:
                yield from body_nodes(child)
        return
    for child in ast.iter_child_nodes(node):
        yield from body_nodes(child)


def constant_value(node, values):
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left, right = constant_value(node.left, values), constant_value(node.comparators[0], values)
        op = node.ops[0]
        if isinstance(op, ast.In): return left in right
        if isinstance(op, ast.NotIn): return left not in right
        if isinstance(op, ast.Eq): return left == right
        if isinstance(op, ast.NotEq): return left != right
        if isinstance(op, ast.Is): return left is right
        if isinstance(op, ast.IsNot): return left is not right
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not constant_value(node.operand, values)
    return ast.literal_eval(node)


class RelaxedIndex(DependencyIndex):
    """Opt-in resolver improvements; the strict resolver/output remain intact."""

    def __init__(self, sources, **kwargs):
        self.plugin_modules = set()
        self.builtin_modules = set()
        super().__init__(sources, **kwargs)
        # Discover pytest's registered built-ins from its historical configuration.
        for symbol in list(self.symbols.values()):
            if symbol.kind != "module" or not symbol.file.endswith("_pytest/config/__init__.py"):
                continue
            bindings = {target.id: statement.value for statement in symbol.node.body
                        if isinstance(statement, ast.Assign) for target in statement.targets
                        if isinstance(target, ast.Name)}

            def strings(node, seen=frozenset()):
                if isinstance(node, ast.Name) and node.id in bindings and node.id not in seen:
                    return strings(bindings[node.id], seen | {node.id})
                if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.BitOr)):
                    return strings(node.left, seen) + strings(node.right, seen)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"set", "list", "tuple"} and node.args:
                    return strings(node.args[0], seen)
                if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                    return [item.value for item in node.elts
                            if isinstance(item, ast.Constant) and isinstance(item.value, str)]
                return []

            if "default_plugins" in bindings:
                self.plugin_modules.update("_pytest." + name for name in strings(bindings["default_plugins"]))
            if "builtin_plugins" in bindings:
                self.builtin_modules.update("_pytest." + name for name in strings(bindings["builtin_plugins"]))
            self.builtin_modules.update(self.plugin_modules)
            for node in symbol.node.body:
                if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                        and ast.unparse(node.value.func) == "builtin_plugins.add" and node.value.args
                        and isinstance(node.value.args[0], ast.Constant) and isinstance(node.value.args[0].value, str)):
                    self.builtin_modules.add("_pytest." + node.value.args[0].value)
        self.uses = lru_cache(maxsize=None)(self._uses)

    def fixture_for(self, scope, name):
        symbol = self.symbols[scope]
        decorators = list(getattr(symbol.node, "decorator_list", []))
        if symbol.context:
            decorators += self.symbols[symbol.context].node.decorator_list
        for dec in decorators:
            if isinstance(dec, ast.Call) and ast.unparse(dec.func).endswith("parametrize") and dec.args:
                try:
                    names = ast.literal_eval(dec.args[0])
                    names = [part.strip() for part in names.split(",")] if isinstance(names, str) else names
                    indirect = next((ast.literal_eval(kw.value) for kw in dec.keywords if kw.arg == "indirect"), False)
                    if name in names and indirect is not True and (not isinstance(indirect, (list, tuple)) or name not in indirect):
                        return None
                except (ValueError, TypeError): pass
        local = super().fixture_for(scope, name)
        if local:
            return local
        modules = set(self.plugin_modules)
        parent = Path(symbol.file).parent.as_posix()
        ancestors = {symbol.file}
        parts = parent.split("/")
        ancestors.update("/".join(parts[:n] + ["conftest.py"]) for n in range(len(parts), -1, -1))
        for path in ancestors:
            root = self.symbols.get(f"{path}::<module>")
            if not root:
                continue
            for node in root.node.body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in node.targets):
                    try:
                        value = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        continue
                    modules.update([value] if isinstance(value, str) else
                                   [item for item in value if isinstance(item, str)] if isinstance(value, (list, tuple)) else [])
        modules = {"_pytest." + name if "_pytest." + name in self.builtin_modules else name for name in modules}
        from scripts.test_retrieval.static_dependencies import module_name
        matches = [sid for sid, (exported, _) in self.fixtures.items()
                   if exported == name and module_name(self.symbols[sid].file) in modules]
        return matches[0] if len(matches) == 1 else None

    def canonical(self, sid):
        return self.symbols[sid].original or sid

    def requested_fixtures(self, sid):
        """Only explicit fixture requests, including dependencies of fixtures."""
        symbol = self.symbols[sid]
        if sid not in self.tests and self.canonical(sid) not in self.fixtures:
            return []
        names = {arg.arg for arg in [*symbol.node.args.posonlyargs, *symbol.node.args.args,
                                     *symbol.node.args.kwonlyargs] if arg.arg not in {"self", "cls"}}
        decorators = list(symbol.node.decorator_list)
        if sid in self.tests and symbol.context:
            decorators += self.symbols[symbol.context].node.decorator_list
        for decorator in decorators:
            if isinstance(decorator, ast.Call) and ast.unparse(decorator.func).endswith(".usefixtures"):
                for arg in decorator.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        names.add(arg.value)
        return [(name, fixture) for name in sorted(names)
                if (fixture := self.fixture_for(sid, name)) and fixture != sid]

    def getter(self, sid):
        symbol = self.symbols[sid]
        return symbol.kind == "function" and any(
            ast.unparse(dec).split(".")[-1] in {"property", "cached_property"}
            for dec in symbol.node.decorator_list)

    def resolve(self, node, scope, context=None, seen=frozenset()):
        result = super().resolve(node, scope, context, seen)
        if not isinstance(node, ast.Attribute):
            return result
        owners = self.expand(self.resolve(node.value, scope, context, seen), seen, context)
        # Values produced by properties, plus typed __getattr__ (e.g. pytest.mark).
        if len(owners) != 1:
            return result
        owner = next(iter(owners))
        if owner.target not in self.symbols or not owner.instance or self.symbols[owner.target].kind != "class":
            return result
        member = self._member(owner.target, node.attr)
        if member and self.getter(member):
            return self._returns(member, seen)
        if not result:
            dynamic = self._member(owner.target, "__getattr__")
            if dynamic:
                return self._returns(dynamic, seen)
        return result

    def _uses(self, sid):
        symbol = self.symbols[sid]
        uses = super().direct_test_uses(sid)
        for statement in symbol.node.body:
            for node in body_nodes(statement):
                targets, kind = [], None
                if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                    owners = self.expand(self.resolve(node.value, sid, symbol.context), context=symbol.context)
                    if len(owners) == 1:
                        owner = next(iter(owners))
                        if owner.instance and owner.target in self.symbols and self.symbols[owner.target].kind == "class":
                            member = self._member(owner.target, node.attr)
                            if member and self.getter(member):
                                targets, kind = [member], "property_read"
                # body_nodes yields the decorator expression as a separate root.
                for declaration in (statement,) if isinstance(statement, (*FUNCTIONS, ast.ClassDef)) else ():
                    if node in declaration.decorator_list:
                        refs = self.expand(self.resolve(node, sid, symbol.context), context=symbol.context)
                        if len(refs) == 1:
                            ref = next(iter(refs))
                            if ref.instance and ref.target in self.symbols and self.symbols[ref.target].kind == "class":
                                method = self._member(ref.target, "__call__")
                                if method:
                                    targets, kind = [method], "decorator_application"
                if targets:
                    uses.append({"kind": kind, "targets": [self.canonical(t) for t in targets],
                                 "unambiguous": True, "line": node.lineno, "end_line": node.end_lineno,
                                 "expression": ast.unparse(node)})
        return uses

    def binding(self, sid, use, incoming):
        """Bind literal arguments only, for inexpensive early-exit checks."""
        result = {}
        function = self.symbols[sid].node
        args = function.args.posonlyargs + function.args.args
        for arg, default in zip(args[-len(function.args.defaults):], function.args.defaults):
            try: result[arg.arg] = constant_value(default, {})
            except (ValueError, KeyError, TypeError): pass
        try:
            call = ast.parse(use["expression"], mode="eval").body
            if not isinstance(call, ast.Call): return result
            if args and args[0].arg in {"self", "cls"}: args = args[1:]
            for arg, value in zip(args, call.args):
                try: result[arg.arg] = constant_value(value, incoming)
                except (ValueError, KeyError, TypeError): pass
            for kw in call.keywords:
                if kw.arg:
                    try: result[kw.arg] = constant_value(kw.value, incoming)
                    except (ValueError, KeyError, TypeError): pass
        except (SyntaxError, ValueError): pass
        return result

    def terminal(self, statement, sid):
        if isinstance(statement, (ast.Return, ast.Raise)):
            return True
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            refs = self.expand(self.resolve(call.func, sid, self.symbols[sid].context), context=self.symbols[sid].context)
            if len(refs) == 1:
                ref = next(iter(refs))
                if ref.target == "external:sys.exit": return True
                target = self.symbols.get(ref.target)
                if target and target.kind == "function" and target.node.body:
                    last = target.node.body[-1]
                    return isinstance(last, ast.Raise) or (isinstance(last, ast.Expr)
                           and isinstance(last.value, ast.Call) and ast.unparse(last.value.func) == "sys.exit")
        return False

    def reachable(self, sid, use, values):
        """Prune known literal branches; otherwise retain possible dependencies."""
        statements = self.symbols[sid].node.body
        for statement in statements:
            if isinstance(statement, ast.If):
                try: branch = statement.body if constant_value(statement.test, values) else statement.orelse
                except (ValueError, KeyError, TypeError): continue
                rejected = statement.orelse if branch is statement.body else statement.body
                if any(n.lineno <= use["line"] <= n.end_lineno for n in rejected): return False
                if any(self.terminal(n, sid) for n in branch) and use["line"] > statement.end_lineno: return False
            # Conventional getopt help handling exits before later CLI work.
            if isinstance(statement, ast.For):
                argv = values.get("argv", values.get("args"))
                if isinstance(argv, (list, tuple)) and argv and argv[0] in {"--help", "-h"}:
                    for node in ast.walk(statement):
                        if isinstance(node, ast.If):
                            try: choices = ast.literal_eval(node.test.comparators[0]) if isinstance(node.test, ast.Compare) else []
                            except (ValueError, TypeError): continue
                            if isinstance(choices, (tuple, list)) and argv[0] in choices and any(self.terminal(n, sid) for n in node.body):
                                if use["line"] >= statement.lineno: return False
        return True


def features(code):
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*|--[a-z][a-z-]*", code))
    return tokens - {"def", "class", "self", "assert", "return", "pytest", "testdir", "pytester",
                     "makepyfile", "runpytest", "inline_run", "None", "True", "False", "test", "pass"}


def unittest_program(node):
    for call in ast.walk(node):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr not in {"makepyfile", "makefile"}:
            continue
        for argument in [*call.args, *[kw.value for kw in call.keywords]]:
            for text in ast.walk(argument):
                if isinstance(text, ast.Constant) and isinstance(text.value, str):
                    qualified = re.search(r"class\s+\w+\s*\([^)]*unittest\.TestCase", text.value)
                    imported = "from unittest import TestCase" in text.value and re.search(r"class\s+\w+\s*\([^)]*\bTestCase\b", text.value)
                    if qualified or imported:
                        return True
    return False


def retrieve_relaxed(index, changes, *, max_depth=3, top_k=10, original=(), gold=(), framework_models=True):
    if max_depth not in (2, 3): raise ValueError("Relaxed max_depth must be 2 or 3")
    targets, matches, rejected = {}, {}, []
    for change in changes:
        for target in change["base_targets"]:
            symbol = index.symbols[target["id"]]
            if target["reason"] != "existing_symbol" or symbol.kind not in {"function", "class", "variable"}:
                rejected.append({"changed_symbol": change["id"], "base_target": target["id"], "reason": "not_an_exact_existing_object"})
                continue
            targets.setdefault(target["id"], []).append(change["id"])
    original_keys = {(t["file"], t["name"]) for t in original}
    gold_features = [features(t["code_content"]) for t in gold]
    if not targets:
        return [], [], {"rejected_targets": rejected, "matching_tests_before_cap": 0,
                        "original_tests_without_relation": [{"file": t["file"], "name": t["name"]} for t in original]}
    runner_hook = index.exports.get("_pytest.runner.pytest_runtest_call")
    unittest_run = next((sid for sid, s in index.symbols.items() if s.file.endswith("_pytest/unittest.py")
                         and s.name == "TestCaseFunction.runtest"), None)
    for test in sorted(index.tests):
        symbol = index.symbols[test]
        has_unittest_program = unittest_program(symbol.node)
        queue = deque([(test, [], {})])
        visited = set()
        while queue:
            sid, path, bound = queue.popleft()
            key = (sid, tuple(sorted((k, repr(v)) for k, v in bound.items())))
            if key in visited: continue
            visited.add(key)
            canonical = index.canonical(sid)
            if path and canonical in targets:
                for changed in targets[canonical]:
                    confidence = min(e["confidence"] for e in path)
                    evidence = {"changed_symbol": changed, "base_target": canonical, "mapping": "existing_symbol",
                                "confidence": confidence, "distance": len(path), "path": path}
                    prior = matches.setdefault(test, {}).get(changed)
                    if prior is None or (confidence, -len(path)) > (prior["confidence"], -prior["distance"]):
                        matches[test][changed] = evidence
            if len(path) >= max_depth or index.symbols[sid].kind != "function": continue
            edges = []
            for use in index.uses(sid):
                if not use["unambiguous"] or not index.reachable(sid, use, bound): continue
                for target in use["targets"]:
                    edges.append((target, use, index.binding(target, use, bound) if index.symbols[target].kind == "function" else {}))
            for name, fixture in index.requested_fixtures(sid):
                current = index.symbols[sid]
                edges.append((fixture, {"kind": "fixture", "expression": name, "line": current.start,
                                       "end_line": current.start}, {}))
            if framework_models and has_unittest_program and runner_hook and unittest_run:
                current = index.symbols[sid]
                if current.file.endswith("_pytest/pytester.py") and current.name.rsplit(".", 1)[-1] in {
                        "runpytest", "runpytest_inprocess", "runpytest_subprocess", "inline_run"}:
                    edges.append((runner_hook, {"kind": "runner_dispatch", "expression": "unittest program executed by pytester",
                                               "line": current.start, "end_line": current.end}, {}))
                if sid == runner_hook:
                    edges.append((unittest_run, {"kind": "unittest_dispatch", "expression": "unittest item runtest dispatch",
                                                "line": current.start, "end_line": current.end}, {}))
            for target, use, bindings in edges:
                if target not in index.symbols: continue
                kind = use["kind"]
                confidence = .8 if kind.endswith("dispatch") else .9 if kind in {"fixture", "property_read", "decorator_application"} else 1.0
                edge = {"source": canonical, "target": index.canonical(target), "kind": kind, "confidence": confidence,
                        "location": {"file": index.symbols[sid].file, "line": use["line"], "end_line": use["end_line"],
                                     "expression": use["expression"]}}
                queue.append((target, path + [edge], bindings))
    def rank(sid):
        evidence = list(matches[sid].values())
        candidate_features = features(index.tests[sid]["code_content"])
        similarity = max((len(candidate_features & g) / max(1, len(g)) for g in gold_features), default=0)
        return (min(e["distance"] for e in evidence), -max(e["confidence"] for e in evidence),
                -(tuple(index.tests[sid][k] for k in ("file", "name")) in original_keys), -similarity, -len(evidence), sid)
    selected = sorted(matches, key=rank)
    if top_k: selected = selected[:top_k]
    tests = [index.tests[sid] for sid in selected]
    evidence = [{"file": index.tests[sid]["file"], "name": index.tests[sid]["name"],
                 "sources": (["test_patch_base"] if (index.tests[sid]["file"], index.tests[sid]["name"]) in original_keys else []) + ["production_patch"],
                 "matched_symbols": sorted(matches[sid]), "evidence": list(matches[sid].values()), "available_at_base": True}
                for sid in selected]
    audit = {"rejected_targets": rejected, "matching_tests_before_cap": len(matches),
             "original_tests_without_relation": [{"file": t["file"], "name": t["name"]} for t in original
                                                 if f"{t['file']}::{t['name']}" not in matches]}
    return tests, evidence, audit


def augment_relaxed_instance(row, repo_dir, *, max_depth=3, top_k=10, framework_models=True):
    sources, decode_errors, settings = base_sources(repo_dir, row["base_commit"])
    patched_tests, original, test_manifest = extract_instance(row, repo_dir)
    index = RelaxedIndex(sources, test_patterns=settings.get("test_patterns"),
                         ignore_patterns=settings.get("ignore_patterns", ()),
                         extra_test_files={t["file"] for t in patched_tests})
    changes = production_changes(row["patch"])
    patched = apply_production_patch(repo_dir, row["base_commit"], row["patch"], changes)
    after = DependencyIndex({c.path: patched[c.path] for c in changes if patched.get(c.path) is not None}).build()
    symbols = changed_symbols(index, after, changes)
    gold_keys = {(t["file"], t["name"]) for t in test_manifest if t["fail_to_pass"] or t["kind"] == "new"}
    hints = [t for t in patched_tests if (t["file"], t["name"]) in gold_keys] or patched_tests
    tests, evidence, audit = retrieve_relaxed(index, symbols, max_depth=max_depth, top_k=top_k,
                                            original=original, gold=hints, framework_models=framework_models)
    manifest = {"selection_policy": POLICY, "base_commit": row["base_commit"], "selection_uses_gold_patch": True,
                "parameters": {"max_depth": max_depth, "top_k": top_k, "fallback": False,
                               "framework_models": framework_models}, "changed_symbols": symbols,
                "selected_tests": evidence, "retrieval_audit": audit,
                "gold_test_hints": [{"file": t["file"], "name": t["name"]} for t in hints],
                "counts": {"original_base": len(original), "augmented": len(tests),
                           "added": sum("test_patch_base" not in t["sources"] for t in evidence)},
                "analysis": {**index.report(), "analysis_mode": "lazy_bounded_paths",
                             "functions_inspected": index.uses.cache_info().currsize}, "decode_errors": decode_errors,
                "registered_builtin_plugin_modules": sorted(index.plugin_modules)}
    if not tests: manifest["retrieval_reason"] = "No resolved exact-object relation within the bounded path policy"
    return tests, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=dataset_name, required=True)
    parser.add_argument("--repo", type=repository_name, required=True)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--repo-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--instance-id", action="append")
    parser.add_argument("--max-depth", type=int, choices=(2, 3), default=3)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--no-framework-models", action="store_true")
    args = parser.parse_args(argv)
    if args.top_k < 0: parser.error("--top-k must be nonnegative")
    csv_path, strict_dir = default_paths(args.dataset, args.repo)
    csv_path = args.csv or (ROOT / "data/swt-bench-verified/oracle_input_pylint_pytest.csv"
                           if args.dataset == "swt-verified" and args.repo in {"pylint-dev/pylint", "pytest-dev/pytest"} else csv_path)
    output = (args.output_dir or strict_dir / f"relaxed_d{args.max_depth}").resolve()
    if output == strict_dir.resolve(): parser.error("Use a separate relaxed output directory; strict outputs are protected")
    repo_dir = args.repo_dir or ROOT / "repos" / args.repo.split("/")[1]
    ensure_repo(repo_dir, f"https://github.com/{args.repo}.git")
    rows = read_repo_rows(csv_path, args.repo)
    if args.dataset == "swt-verified" and any(r.get("source_dataset") != DATASETS["swt-verified"] for r in rows):
        parser.error("CSV must be a normalized SWT Verified export")
    if args.instance_id:
        unknown = set(args.instance_id) - {r["instance_id"] for r in rows}
        if unknown: parser.error(f"Unknown instance IDs: {sorted(unknown)}")
        rows = [r for r in rows if r["instance_id"] in args.instance_id]
    if not rows: parser.error("No selected repository instances")
    outputs = [_load(output / name) for name in FILES]
    if any(m.get("selection_policy") != POLICY or m.get("parameters") != {
            "max_depth": args.max_depth, "top_k": args.top_k, "fallback": False,
            "framework_models": not args.no_framework_models} for m in outputs[2].values()):
        parser.error("Existing output settings differ; choose another relaxed output directory")
    for row in rows:
        tests, manifest = augment_relaxed_instance(row, repo_dir, max_depth=args.max_depth, top_k=args.top_k,
                                                   framework_models=not args.no_framework_models)
        for dest, value in zip(outputs, (tests, tests, manifest)): dest[row["instance_id"]] = value
        for name, value in zip(FILES, outputs):
            _save(output / name, test_retrieval(value) if name.startswith("related_tests_") else value)
        print(f"{row['instance_id']}: {len(tests)} relaxed tests; added={manifest['counts']['added']}", flush=True)
    _save(output / "run_summary.json", {"selection_policy": POLICY, "input_csv": str(csv_path),
                                       "input_csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
                                       "instances": len(outputs[2]), "tests": sum(len(t) for t in outputs[1].values()),
                                       "empty_instances": [iid for iid, t in outputs[1].items() if not t]})


if __name__ == "__main__":
    main()
