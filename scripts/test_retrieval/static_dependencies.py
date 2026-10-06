"""Conservative Python dependencies without importing or executing a repository.

Edges describe possible uses, not proven execution. Unknown receivers are never
matched against every method with the same name. Fixture/lifecycle edges model
test-runner behavior; contextual copies of inherited methods resolve ``self``
against the concrete test class without mixing unrelated subclasses.
"""

from __future__ import annotations

import ast
import fnmatch
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath

from scripts.test_retrieval.extract_oracle import test_functions


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Ref:
    target: str
    instance: bool = False


@dataclass(frozen=True)
class Import:
    target: str


@dataclass
class Symbol:
    id: str
    file: str
    name: str
    kind: str
    node: ast.AST
    parent: str | None
    start: int
    end: int
    context: str | None = None
    original: str | None = None


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    kind: str
    confidence: float = 1.0


def module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[0] in {"src", "lib"}:
        parts.pop(0)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class DependencyIndex:
    def __init__(self, sources: dict[str, str], *, test_patterns=None, ignore_patterns=(), extra_test_files=()):
        self.sources = sources
        self.test_patterns = tuple(test_patterns or ("test_*.py", "unittest*.py", "*_test.py"))
        self.ignore_patterns = tuple(ignore_patterns)
        self.extra_test_files = set(extra_test_files)
        self.symbols: dict[str, Symbol] = {}
        self.bindings: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        self.members: dict[str, dict[str, str]] = defaultdict(dict)
        self.attributes: dict[str, dict[str, list[tuple[str, ast.expr]]]] = defaultdict(lambda: defaultdict(list))
        self.modules: dict[str, str] = {}
        self.exports: dict[str, str] = {}
        self.tests: dict[str, dict[str, str]] = {}
        self.fixtures: dict[str, tuple[str, bool]] = {}
        self.edges: dict[tuple[str, str, str], Edge] = {}
        self.diagnostics: list[dict] = []
        self._unresolved: set[tuple[str, int, str]] = set()
        self._specializations: dict[tuple[str, str], str] = {}
        # Keep caches local to this snapshot so processing many commits does
        # not retain every historical AST through a class-level cache.
        self.bases = lru_cache(maxsize=None)(self.bases)
        self._member = lru_cache(maxsize=None)(self._member)
        self._ast_visitor = lru_cache(maxsize=None)(self._ast_visitor)
        self.fixture_for = lru_cache(maxsize=None)(self.fixture_for)
        for path, source in sorted(sources.items()):
            try:
                tree = ast.parse(source, filename=path)
            except (SyntaxError, ValueError, RecursionError) as exc:
                self.diagnostics.append({"file": path, "kind": "parse_error", "message": str(exc)})
                continue
            root = self._add(path, "<module>", "module", tree, None)
            self.modules[module_name(path)] = root
            self._collect(tree.body, root, "", None)
        for sid, symbol in list(self.symbols.items()):
            if symbol.kind == "module":
                continue
            if self.symbols[symbol.parent].kind == "module":
                self.exports[f"{module_name(symbol.file)}.{symbol.name}"] = sid
            if symbol.kind == "function":
                fixture = self._fixture_metadata(symbol.node)
                if fixture:
                    self.fixtures[sid] = fixture
        for path in sources:
            if f"{path}::<module>" not in self.symbols or not self._test_file(path):
                continue
            for name, function in test_functions(sources[path], path).items():
                sid = f"{path}::{name}"
                if sid in self.symbols:
                    self.tests[sid] = {"file": path, "name": name, "code_content": function.code_content}

    def _test_file(self, path: str) -> bool:
        if path in self.extra_test_files:
            return True
        parsed = PurePosixPath(path)
        if any(fnmatch.fnmatchcase(str(parent), pattern) or fnmatch.fnmatchcase(parent.name, pattern)
               for parent in parsed.parents for pattern in self.ignore_patterns):
            return False
        return any(fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(parsed.name, pattern)
                   for pattern in self.test_patterns)

    def _add(self, path, name, kind, node, parent, context=None) -> str:
        sid = f"{path}::{name}"
        decorators = getattr(node, "decorator_list", [])
        start = min([getattr(node, "lineno", 1)] + [dec.lineno for dec in decorators])
        end = getattr(node, "end_lineno", None) or len(self.sources[path].splitlines())
        self.symbols[sid] = Symbol(sid, path, name, kind, node, parent, start, end, context)
        return sid

    def _collect(self, statements, scope: str, prefix: str, class_id: str | None):
        for node in statements:
            symbol = self.symbols[scope]
            if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
                name = f"{prefix}.{node.name}" if prefix else node.name
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                sid = self._add(symbol.file, name, kind, node, scope, class_id)
                self.bindings[scope][node.name].append(Ref(sid))
                if symbol.kind == "class":
                    self.members[scope][node.name] = sid
                if kind == "function":
                    args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
                    if node.args.vararg:
                        args.append(node.args.vararg)
                    if node.args.kwarg:
                        args.append(node.args.kwarg)
                    for arg in args:
                        self.bindings[sid][arg.arg].append(arg.annotation)
                self._collect(node.body, sid, name, sid if kind == "class" else class_id)
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if isinstance(node, ast.Import):
                        name = alias.asname or alias.name.split(".")[0]
                        target = alias.name if alias.asname else name
                    else:
                        package = module_name(symbol.file).split(".")
                        if PurePosixPath(symbol.file).name != "__init__.py":
                            package = package[:-1]
                        if node.level:
                            package = package[:len(package) - node.level + 1]
                            target = ".".join(package + ([node.module] if node.module else []) + [alias.name])
                        else:
                            target = ".".join(filter(None, [node.module, alias.name]))
                        name = alias.asname or alias.name
                    self.bindings[scope][name].append(Import(target))
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value or getattr(node, "annotation", None)
                for target in targets:
                    if isinstance(target, ast.Name):
                        if symbol.kind in {"module", "class"}:
                            name = f"{prefix}.{target.id}" if prefix else target.id
                            sid = self._add(symbol.file, name, "variable", node, scope, class_id)
                            self.bindings[scope][target.id] = [Ref(sid)]
                            self.bindings[sid]["<value>"].append(value)
                            if symbol.kind == "class":
                                self.members[scope][target.id] = sid
                        else:
                            self.bindings[scope][target.id].append(value)
                    elif (class_id and isinstance(target, ast.Attribute)
                          and isinstance(target.value, ast.Name) and target.value.id in {"self", "cls"}):
                        self.attributes[class_id][target.attr].append((scope, value))
            # Bindings inside conditionals/loops belong to the surrounding scope.
            for field in ("body", "orelse", "finalbody"):
                child = getattr(node, field, None)
                if isinstance(child, list):
                    self._collect(child, scope, prefix, class_id)
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    self._collect(handler.body, scope, prefix, class_id)

    def _import(self, target: str, seen: frozenset) -> set[Ref]:
        if target in seen:
            return set()
        seen = seen | {target}
        if target in self.exports:
            return {Ref(self.exports[target])}
        if target in self.modules:
            return {Ref(self.modules[target])}
        module, _, name = target.rpartition(".")
        if module in self.modules:
            return self._name(name, self.modules[module], None, seen)
        # Keep external qualified names to distinguish pytest.fixture, ast visitors, etc.
        return {Ref("external:" + target)}

    def _name(self, name, scope, context, seen) -> set[Ref]:
        if name in {"self", "cls"} and context:
            return {Ref(context, name == "self")}
        if name in self.bindings[scope]:
            key = (scope, name, context)
            if key in seen:
                return set()
            seen = seen | {key}
            result = set()
            for value in self.bindings[scope][name]:
                if isinstance(value, Ref):
                    result.add(value)
                elif isinstance(value, Import):
                    result.update(self._import(value.target, seen))
                elif value is not None:
                    result.update(self.resolve(value, scope, context, seen))
                else:
                    fixture = self.fixture_for(scope, name)
                    if fixture:
                        result.update(self._returns(fixture, seen))
            return result
        symbol = self.symbols[scope]
        if symbol.parent:
            # Python method bodies do not inherit the class's lexical namespace.
            parent = self.symbols[symbol.parent]
            if symbol.kind == "function" and parent.kind == "class":
                return self._name(name, parent.parent, context, seen)
            return self._name(name, symbol.parent, context, seen)
        result = set()
        for value in self.bindings[scope].get("*", []):
            if isinstance(value, Import):
                result.update(self._import(value.target[:-1] + name, seen))
        return result

    def expand(self, refs: set[Ref], seen=frozenset(), context=None) -> set[Ref]:
        result = set()
        for ref in refs:
            symbol = self.symbols.get(ref.target)
            key = ("expand", ref.target, context)
            if symbol and symbol.kind == "variable" and key not in seen:
                for value in self.bindings[symbol.id]["<value>"]:
                    result.update(self.expand(self.resolve(value, symbol.parent, context, seen | {key}),
                                              seen | {key}, context))
            else:
                result.add(ref)
        return result

    def bases(self, class_id, seen=frozenset()) -> list[str]:
        if class_id in seen:
            return []
        symbol = self.symbols[class_id]
        if symbol.kind != "class":
            return []
        return sorted({ref.target for base in symbol.node.bases
                       for ref in self.expand(self.resolve(base, symbol.parent, None, seen | {class_id}))
                       if ref.target in self.symbols and self.symbols[ref.target].kind == "class"})

    def _member(self, class_id, name, seen=frozenset()) -> str | None:
        if class_id in seen:
            return None
        if name in self.members[class_id]:
            return self.members[class_id][name]
        for base in self.bases(class_id, seen):
            result = self._member(base, name, seen | {class_id})
            if result:
                return result
        return None

    def _attribute(self, class_id, name, context, seen) -> set[Ref]:
        key = ("attribute", class_id, name, context)
        if key in seen:
            return set()
        seen = seen | {key}
        if name in self.attributes[class_id]:
            result = set()
            for scope, value in self.attributes[class_id][name]:
                result.update(self.resolve(value, scope, context, seen))
            return result
        for base in self.bases(class_id):
            result = self._attribute(base, name, context, seen)
            if result:
                return result
        return set()

    def _specialize(self, method, context) -> str:
        symbol = self.symbols[method]
        if symbol.parent == context or not context:
            return method
        key = (method, context)
        if key not in self._specializations:
            sid = f"{method}@{context}"
            self.symbols[sid] = Symbol(sid, symbol.file, symbol.name, symbol.kind, symbol.node,
                                       symbol.parent, symbol.start, symbol.end, context, method)
            self.bindings[sid] = self.bindings[method]
            self._specializations[key] = sid
            self.add_edge(sid, method, "inherited_implementation")
        return self._specializations[key]

    def resolve(self, node, scope, context=None, seen=frozenset()) -> set[Ref]:
        if node is None or len(seen) > 40:
            return set()
        if isinstance(node, ast.Name):
            return self._name(node.id, scope, context, seen)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Forward-reference type annotations, not arbitrary generated source.
            try:
                return self.resolve(ast.parse(node.value, mode="eval").body, scope, context, seen | {("str", node.value)})
            except (SyntaxError, RecursionError):
                return set()
        if isinstance(node, ast.Attribute):
            result = set()
            for ref in self.expand(self.resolve(node.value, scope, context, seen), seen, context):
                symbol = self.symbols.get(ref.target)
                if symbol and symbol.kind == "module":
                    result.update(self._import(f"{module_name(symbol.file)}.{node.attr}", seen))
                elif symbol and symbol.kind == "class":
                    member = self._member(symbol.id, node.attr)
                    if member:
                        if self.symbols[member].kind == "function":
                            member = self._specialize(member, symbol.id)
                        result.add(Ref(member))
                    elif node.attr in {"visit", "generic_visit"} and self._ast_visitor(symbol.id):
                        result.add(Ref(self._dispatcher(symbol.id)))
                    elif ref.instance:
                        result.update(self._attribute(symbol.id, node.attr, symbol.id, seen))
                elif ref.target.startswith("external:"):
                    result.update(self._import(ref.target[9:] + "." + node.attr, seen))
            return result
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "super" and context:
                original = self.symbols[scope].original or scope
                owner = self.symbols[original].context or context
                return {Ref(base, True) for base in self.bases(owner)}
            result = set()
            for ref in self.expand(self.resolve(node.func, scope, context, seen), seen, context):
                symbol = self.symbols.get(ref.target)
                if symbol and symbol.kind == "class":
                    result.add(Ref(symbol.id, True))
                elif symbol and symbol.kind == "function":
                    result.update(self._returns(symbol.id, seen))
            return result
        if isinstance(node, (ast.IfExp, ast.BoolOp)):
            values = [node.body, node.orelse] if isinstance(node, ast.IfExp) else node.values
            return set().union(*(self.resolve(value, scope, context, seen) for value in values))
        return set()

    def _returns(self, sid, seen) -> set[Ref]:
        key = ("returns", sid)
        if key in seen:
            return set()
        symbol = self.symbols[sid]
        result = set()
        annotation = getattr(symbol.node, "returns", None)
        if annotation:
            result.update(Ref(ref.target, True) for ref in self.expand(
                self.resolve(annotation, sid, symbol.context, seen | {key})))
        for node in self._walk(symbol.node):
            if isinstance(node, (ast.Return, ast.Yield)):
                result.update(self.resolve(node.value, sid, symbol.context, seen | {key}))
        return result

    @staticmethod
    def _walk(root):
        def walk(node):
            yield node
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (*FUNCTIONS, ast.ClassDef)):
                    for expression in child.decorator_list:
                        yield from walk(expression)
                    if isinstance(child, FUNCTIONS):
                        for expression in child.args.defaults + [x for x in child.args.kw_defaults if x]:
                            yield from walk(expression)
                    continue
                yield from walk(child)
        if isinstance(root, (*FUNCTIONS, ast.ClassDef, ast.Module)):
            for node in getattr(root, "decorator_list", []):
                yield from walk(node)
            for node in root.body:
                if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
                    # Delegate to the same nested-definition exclusion above.
                    for expression in node.decorator_list:
                        yield from walk(expression)
                else:
                    yield from walk(node)
        else:
            yield from walk(root)

    def _ast_visitor(self, class_id) -> bool:
        symbol = self.symbols[class_id]
        return any(ref.target in {"external:ast.NodeVisitor", "external:ast.NodeTransformer"}
                   for base in symbol.node.bases for ref in self.resolve(base, symbol.parent))

    def _dispatcher(self, class_id) -> str:
        sid = class_id + ".<ast_dispatch>"
        if sid not in self.symbols:
            symbol = self.symbols[class_id]
            self.symbols[sid] = Symbol(sid, symbol.file, symbol.name + ".<ast_dispatch>",
                                       "dispatch", symbol.node, class_id, symbol.start, symbol.end)
            for name, method in self.members[class_id].items():
                if name.startswith("visit_") and self.symbols[method].kind == "function":
                    self.add_edge(sid, method, "ast_dispatch", 0.65)
        return sid

    @staticmethod
    def _fixture_metadata(node):
        for decorator in node.decorator_list:
            func = decorator.func if isinstance(decorator, ast.Call) else decorator
            name = ast.unparse(func).split(".")[-1]
            if name not in {"fixture", "yield_fixture"}:
                continue
            keywords = {kw.arg: kw.value for kw in getattr(decorator, "keywords", [])}
            exported = keywords.get("name")
            autouse = keywords.get("autouse")
            return (exported.value if isinstance(exported, ast.Constant) and isinstance(exported.value, str) else node.name,
                    isinstance(autouse, ast.Constant) and autouse.value is True)
        return None

    def fixture_for(self, scope, name) -> str | None:
        symbol = self.symbols[scope]
        context = symbol.context
        if context:
            method = self._member(context, name)
            if method in self.fixtures:
                return self._specialize(method, context)
        root = f"{symbol.file}::<module>"
        for value in self.bindings[root].get(name, []):
            refs = self._import(value.target, frozenset()) if isinstance(value, Import) else {value} if isinstance(value, Ref) else set()
            for ref in refs:
                if ref.target in self.fixtures:
                    return ref.target
        parent = PurePosixPath(symbol.file).parent
        for directory in [parent, *parent.parents]:
            conftest = str(directory / "conftest.py")
            for fixture, (exported, _) in self.fixtures.items():
                if self.symbols[fixture].file == conftest and exported == name:
                    return fixture
        # Explicit fixture name= aliases in the test module.
        return next((fixture for fixture, (exported, _) in self.fixtures.items()
                     if self.symbols[fixture].file == symbol.file and exported == name), None)

    def add_edge(self, source, target, kind, confidence=1.0):
        if source != target and target in self.symbols:
            self.edges[(source, target, kind)] = Edge(source, target, kind, confidence)

    def _fixture_edges(self, sid):
        symbol = self.symbols[sid]
        if not isinstance(symbol.node, FUNCTIONS):
            return
        original = symbol.original or sid
        is_test = sid in self.tests
        if not is_test and original not in self.fixtures:
            return
        # Directly parameterized arguments override fixtures unless indirect.
        excluded = set()
        requested = []
        decorators = list(symbol.node.decorator_list)
        if symbol.context:
            decorators += self.symbols[symbol.context].node.decorator_list
        for decorator in decorators:
            if not isinstance(decorator, ast.Call):
                continue
            name = ast.unparse(decorator.func).split(".")[-1]
            if name == "usefixtures":
                requested += [arg.value for arg in decorator.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
            if name == "parametrize" and decorator.args:
                names = decorator.args[0]
                if isinstance(names, ast.Constant) and isinstance(names.value, str):
                    names = [item.strip() for item in names.value.split(",")]
                elif isinstance(names, (ast.List, ast.Tuple)):
                    names = [item.value for item in names.elts if isinstance(item, ast.Constant)]
                else:
                    names = []
                indirect = next((kw.value for kw in decorator.keywords if kw.arg == "indirect"), None)
                if not (isinstance(indirect, ast.Constant) and indirect.value is True):
                    indirect_names = {x.value for x in indirect.elts if isinstance(x, ast.Constant)} if isinstance(indirect, (ast.List, ast.Tuple)) else set()
                    excluded.update(set(names) - indirect_names)
        requested += [arg.arg for arg in symbol.node.args.posonlyargs + symbol.node.args.args + symbol.node.args.kwonlyargs
                      if arg.arg not in excluded | {"self", "cls"}]
        if is_test:
            for fixture, (exported, autouse) in self.fixtures.items():
                if autouse and self.fixture_for(sid, exported) == fixture:
                    requested.append(exported)
        for name in requested:
            fixture = self.fixture_for(sid, name)
            if fixture:
                self.add_edge(sid, fixture, "fixture")

    def build(self):
        for sid in self.tests:
            symbol = self.symbols[sid]
            if symbol.context:
                for hook in ("setup_method", "teardown_method", "setup_class", "teardown_class",
                             "setUp", "tearDown", "setUpClass", "tearDownClass"):
                    method = self._member(symbol.context, hook)
                    if method:
                        self.add_edge(sid, self._specialize(method, symbol.context), "lifecycle")
            for hook in ("setup_module", "teardown_module", "setup_function", "teardown_function"):
                method = f"{symbol.file}::{hook}"
                if method in self.symbols:
                    self.add_edge(sid, method, "lifecycle")
        processed = set()
        while True:
            pending = [sid for sid in self.symbols if sid not in processed]
            if not pending:
                break
            for sid in pending:
                processed.add(sid)
                symbol = self.symbols[sid]
                if symbol.kind == "dispatch":
                    continue
                self._fixture_edges(sid)
                root = symbol.node.value if symbol.kind == "variable" and getattr(symbol.node, "value", None) else symbol.node
                for node in self._walk(root):
                    if isinstance(node, ast.Call):
                        refs = self.expand(self.resolve(node.func, sid, symbol.context), context=symbol.context)
                        internal = [ref for ref in refs if ref.target in self.symbols]
                        for ref in internal:
                            self.add_edge(sid, ref.target, "call", 0.8 if len(internal) > 1 else 1.0)
                            if self.symbols[ref.target].kind == "class":
                                constructor = self._member(ref.target, "__init__")
                                if constructor:
                                    self.add_edge(sid, self._specialize(constructor, ref.target), "constructor")
                        if not refs and isinstance(node.func, ast.Attribute):
                            self._unresolved.add((symbol.file, node.lineno, ast.unparse(node.func)))
                    elif isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
                        for ref in self.resolve(node, sid, symbol.context):
                            self.add_edge(sid, ref.target, "reference")
        return self

    def reverse_paths(self, target: str, max_depth: int) -> dict[str, list[Edge]]:
        reverse = defaultdict(list)
        for edge in self.edges.values():
            reverse[edge.target].append(edge)
        routes = {target: []}
        queue = deque([target])
        while queue:
            current = queue.popleft()
            path = routes[current]
            if len(path) >= max_depth:
                continue
            for edge in sorted(reverse[current], key=lambda e: (-e.confidence, e.kind, e.source)):
                if edge.source not in routes:
                    routes[edge.source] = [edge, *path]
                    queue.append(edge.source)
        return {sid: path for sid, path in routes.items() if sid in self.tests}

    def report(self) -> dict:
        return {"parsed_files": len(self.modules), "test_functions": len(self.tests),
                "test_file_patterns": list(self.test_patterns), "ignored_test_paths": list(self.ignore_patterns),
                "symbols": len(self.symbols), "edges": len(self.edges),
                "parse_errors": self.diagnostics,
                "unresolved_attribute_calls": [{"file": file, "line": line, "expression": expr}
                                               for file, line, expr in sorted(self._unresolved)]}
