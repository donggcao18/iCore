"""Read-only, evidence-scoped tools over the Python files in one Git commit."""

from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import tokenize
import warnings
from collections import defaultdict
from importlib.util import resolve_name
from pathlib import PurePosixPath

from scripts.test_retrieval.static_dependencies import DependencyIndex, module_name


SOURCE_LINES = 100
TOOL_FIELDS = {
    "GetRepoStructure": (),
    "GetImportOfFile": ("file",),
    "SearchClass": ("file", "class"),
    "SearchFunction": ("file", "function"),
    "SearchClassMethod": ("file", "class", "method"),
    "Exit": (),
}


def read_git(case, *args, payload=None):
    command = ["git", "-C", str(case.checkout), *args]
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    result = subprocess.run(command, input=payload, capture_output=True, timeout=120, env=env)
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip() or "Git object unavailable"
        raise ValueError(f"{case.instance_id}: Git could not read {case.checkout} ({message})")
    return result.stdout


def check_checkout(case):
    if not case.checkout.is_dir():
        raise ValueError(f"{case.instance_id}: checkout directory is missing: {case.checkout}")
    if read_git(case, "rev-parse", "--show-prefix").strip():
        raise ValueError(f"{case.instance_id}: {case.checkout} belongs to a parent Git checkout. "
                         "Use the Pylint repository root for this case.")
    try:
        read_git(case, "cat-file", "-e", f"{case.commit}^{{commit}}")
    except ValueError as exc:
        raise ValueError(f"{case.instance_id}: pinned commit {case.commit} is unavailable in {case.checkout}") from exc


def git_sources(case):
    blobs = defaultdict(list)
    tree = read_git(case, "ls-tree", "-rz", "--full-tree", f"{case.commit}^{{commit}}")
    for record in tree.split(b"\0"):
        if not record:
            continue
        header, path = record.split(b"\t", 1)
        mode, _, oid = header.split()
        if mode in {b"100644", b"100755"} and path.endswith(b".py"):
            blobs[oid].append(path.decode("utf-8"))
    if not blobs:
        raise ValueError(f"{case.instance_id}: no tracked Python files in the pinned commit")

    sources = {}
    data = io.BytesIO(read_git(case, "cat-file", "--batch", payload=b"\n".join(blobs) + b"\n"))
    for oid, paths in blobs.items():
        header = data.readline().split()
        if len(header) != 3 or header[:2] != [oid, b"blob"]:
            raise ValueError(f"{case.instance_id}: missing Git blob {oid.decode()}")
        content = data.read(int(header[2]))
        if data.read(1) != b"\n":
            raise ValueError(f"{case.instance_id}: incomplete Git blob {oid.decode()}")
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(content).readline)
            source = content.decode(encoding)
        except (SyntaxError, UnicodeError):
            source = content.decode("utf-8", errors="replace")
        for path in paths:
            sources[path] = source
    return dict(sorted(sources.items()))


def literal_pattern(text):
    """Match a literal without treating a substring of an identifier as evidence."""
    left = r"(?<!\w)" if re.match(r"\w", text) else ""
    right = r"(?!\w)" if re.match(r"\w", text[-1]) else ""
    return re.compile(left + re.escape(text) + right, re.IGNORECASE)


class Snapshot:
    def __init__(self, case):
        sources = git_sources(case)
        # Some history snapshots deliberately include lint fixtures with invalid escapes.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            self.index = DependencyIndex(sources)
        self.lines = {path: source.splitlines() for path, source in sources.items()}
        self.scopes = defaultdict(list)
        self.children = defaultdict(list)
        self.structure = defaultdict(list)
        for path in self.lines:
            parsed = PurePosixPath(path)
            self.structure[parsed.parent.as_posix()].append(parsed.name)
        for symbol in self.index.symbols.values():
            self.children[symbol.parent].append(symbol)
            if symbol.kind == "function":
                self.scopes[symbol.file].append(symbol)
        for label in (*case.roots, case.entry):
            if label is not None and (label not in self.index.symbols
                                      or self.index.symbols[label].kind != "function"):
                raise ValueError(f"{case.instance_id}: {label!r} is not a function in {case.commit}")

    def imports_of(self, module):
        package = module_name(module.file)
        if PurePosixPath(module.file).name != "__init__.py":
            package = package.rpartition(".")[0]
        imports = (node for node in ast.walk(module.node) if isinstance(node, (ast.Import, ast.ImportFrom)))
        for node in sorted(imports, key=lambda node: (node.lineno, node.col_offset)):
            row = {"line_number": node.lineno, "text": ast.unparse(node), "targets": []}
            for alias in node.names:
                target = self.import_target(node, alias.name, package)
                if target is None:
                    continue
                module_id = self.index.modules.get(target)
                if module_id is None and isinstance(node, ast.ImportFrom):
                    module_id = self.index.modules.get(target.rpartition(".")[0])
                if module_id is None:
                    continue
                path = self.index.symbols[module_id].file
                match = {"file": path}
                symbol_id = self.index.exports.get(target)
                if symbol_id and self.index.symbols[symbol_id].file == path:
                    match["symbol"] = symbol_id
                row["targets"].append(match)
            yield row

    @staticmethod
    def import_target(node, name, package):
        if isinstance(node, ast.Import):
            return name
        prefix = "." * node.level + (node.module or "")
        try:
            module = resolve_name(prefix, package)
        except ImportError:
            return None
        return ".".join(filter(None, [module, name]))


class ToolError(ValueError):
    """An invalid or unsupported model action."""


class Navigation:
    def __init__(self, snapshot, issue, entry=None):
        self.snapshot = snapshot
        self.public = {"issue": {"type": "issue", "text": issue}}
        self.inspected = {}
        self.parents = {}
        self.exited = False
        if entry:
            symbol = snapshot.index.symbols[entry]
            self.public["entry"] = {"type": "entry", "path": symbol.file,
                                    "line_number": symbol.node.lineno, "symbol": entry}

    def action(self, action, turn):
        if self.exited:
            if action.keys() != {"ranked", "diagnosis", "evidence"}:
                raise ToolError("After Exit, return ranked, diagnosis, and evidence as one JSON object.")
            return self.submit(**action)
        tool = action.get("tool")
        if not isinstance(tool, str) or tool not in TOOL_FIELDS:
            raise ToolError("Choose one of the documented tools. Call Exit before giving the final answer.")
        fields = TOOL_FIELDS[tool]
        if action.keys() != {"tool", *fields}:
            raise ToolError(f"Invalid fields for {tool}. Required: {', '.join(fields) or 'none'}.")
        if tool == "Exit":
            if not any(self.inspected.values()):
                raise ToolError("Inspect at least one function or method before calling Exit.")
            self.exited = True
            return {"type": "exit", "message": "Return the final JSON object with ranked, diagnosis, and evidence."}

        args = [action[field] for field in fields]
        result, parents = getattr(self, tool)(*args)
        observation = f"o{turn}"
        if result["type"] == "source":
            self.inspected[observation] = {
                symbol.id for symbol in self.snapshot.scopes[result["path"]]
                if result["start"] <= symbol.node.lineno
                and symbol.node.body[0].lineno <= result["end"]
            }
            result["inspected"] = sorted(self.inspected[observation])
        self.public[observation] = result
        self.parents[observation] = tuple(dict.fromkeys(parents))
        return {"id": observation, **result}

    def observation(self, evidence):
        if not isinstance(evidence, str) or evidence not in self.public:
            raise ToolError("Use an evidence ID from the issue, supplied entry, or prior tool results.")
        return self.public[evidence]

    def path_evidence(self, path):
        message = "Use the exact Python path shown by GetRepoStructure, an import, the issue, or the supplied entry."
        if not isinstance(path, str) or path not in self.snapshot.lines:
            raise ToolError(message)
        if self.public.get("entry", {}).get("path") == path:
            return "entry"
        if literal_pattern(path).search(self.public["issue"]["text"]):
            return "issue"
        for oid, result in reversed(self.public.items()):
            if result.get("path") == path or any(
                target["file"] == path for row in result.get("imports", ()) for target in row["targets"]
            ):
                return oid
            if result["type"] == "structure":
                directory, name = path.rsplit("/", 1) if "/" in path else (".", path)
                if name in result["directories"].get(directory, ()):
                    return oid
        raise ToolError(message)

    def symbol_evidence(self, path, name):
        pattern = literal_pattern(name)
        for oid, result in reversed(self.public.items()):
            if result["type"] == "issue" and pattern.search(result["text"]):
                return oid
            if result.get("path") == path:
                if (result.get("symbol") == f"{path}::{name}"
                        or f"{path}::{name}" in result.get("inspected", ())
                        or name in result.get("definitions", ())
                        or name in result.get("members", ())
                        or any(pattern.search(line) for line in result.get("source", ()))):
                    return oid
            if any(target.get("symbol") == f"{path}::{name}"
                   for row in result.get("imports", ()) for target in row["targets"]):
                return oid
        raise ToolError("Choose a symbol shown in the issue, supplied entry, imports, declarations, or source.")

    def GetRepoStructure(self):
        return {"type": "structure", "directories": dict(self.snapshot.structure)}, ("issue",)

    def GetImportOfFile(self, file):
        parent = self.path_evidence(file)
        module_id = f"{file}::<module>"
        module = self.snapshot.index.symbols.get(module_id)
        if module is None:
            raise ToolError("This Python file could not be parsed at the pinned commit.")
        definitions = [symbol.name for symbol in self.snapshot.children[module_id]
                       if symbol.kind in {"function", "class"}]
        return {"type": "imports", "path": file, "imports": list(self.snapshot.imports_of(module)),
                "definitions": definitions}, (parent,)

    def SearchClass(self, file, class_name):
        return self.read_symbol(file, class_name, kind="class")

    def SearchFunction(self, file, function):
        return self.read_symbol(file, function, kind="function")

    def SearchClassMethod(self, file, class_name, method):
        self.validate_name(class_name)
        self.validate_name(method)
        if "." in method:
            raise ToolError("Supply the class and the unqualified method name as separate fields.")
        return self.read_symbol(file, f"{class_name}.{method}", kind="function", class_name=class_name)

    @staticmethod
    def validate_name(name):
        if not isinstance(name, str) or not all(part.isidentifier() for part in name.split(".")):
            raise ToolError("Supply an exact Python name using dots for nested definitions.")

    def read_symbol(self, file, name, *, kind, class_name=None):
        path_parent = self.path_evidence(file)
        self.validate_name(name)
        try:
            symbol_parent = self.symbol_evidence(file, name)
            parents = (path_parent, symbol_parent)
        except ToolError:
            if class_name is None:
                raise
            # Source may show the class and a bare method name on separate lines.
            parents = (path_parent, self.symbol_evidence(file, class_name),
                       self.symbol_evidence(file, name.rsplit(".", 1)[1]))
        index = self.snapshot.index.symbols
        symbol = index.get(f"{file}::{name}")
        if symbol is None or symbol.kind != kind:
            raise ToolError("No matching definition in this file. Use the exact name from public evidence.")
        parent = index[symbol.parent]
        if class_name is not None and (parent.kind != "class" or parent.name != class_name):
            raise ToolError("The method must be defined directly on the named class.")
        if kind == "function" and class_name is None and parent.kind == "class":
            raise ToolError("Use SearchClassMethod for a method defined on a class.")
        start = max(symbol.start, symbol.node.lineno - SOURCE_LINES // 4)
        end = min(symbol.end, start + SOURCE_LINES - 1)
        source = [f"{number}: {line[:240]}"
                  for number, line in enumerate(self.snapshot.lines[file][start - 1:end], start)]
        result = {"type": "source", "path": file, "symbol": symbol.id, "start": start, "end": end,
                  "source": source, "total_lines": len(self.snapshot.lines[file]),
                  "truncated": start > symbol.start or end < symbol.end}
        if kind == "class":
            result["members"] = [member.name for member in self.snapshot.children[symbol.id]
                                 if member.kind in {"function", "class"}]
        return result, parents

    def submit(self, ranked, diagnosis, evidence):
        if not isinstance(ranked, list) or not 1 <= len(ranked) <= 5:
            raise ToolError("Rank one to five functions as path.py::qualified_name.")
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 8:
            raise ToolError("Cite one to eight read IDs in evidence.")
        if not all(isinstance(item, str) for item in ranked) or len(ranked) != len(set(ranked)):
            raise ToolError("Rank each function at most once.")
        if any(self.observation(item)["type"] != "source" for item in evidence):
            raise ToolError("Diagnosis evidence must cite source reads.")
        reads = [next((oid for oid in (*evidence, *self.inspected)
                       if name in self.inspected.get(oid, ())), None) for name in ranked]
        if None in reads or reads[0] not in evidence:
            raise ToolError("Inspect each ranked function and cite a window showing the first-ranked definition.")
        if not isinstance(diagnosis, str) or not diagnosis.strip() or len(diagnosis) > 1800:
            raise ToolError("Write a nonempty diagnosis of at most 1800 characters.")
        return {"type": "submission", "ranked": ranked, "read_ids": reads,
                "diagnosis": diagnosis.strip(), "evidence": evidence}

    def chain(self, observation):
        chain = {}

        def visit(oid):
            if oid not in chain:
                for parent in self.parents.get(oid, ()):
                    visit(parent)
                chain[oid] = None

        visit(observation)
        return list(chain)
