"""Read-only, evidence-scoped tools over the Python files in one Git commit."""

from __future__ import annotations

import io
import os
import re
import subprocess
import tokenize
import warnings
from collections import defaultdict
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from scripts.test_retrieval.static_dependencies import DependencyIndex


RESULT_LIMIT = 100
SOURCE_LINES = 100


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
        for symbol in self.index.symbols.values():
            if symbol.kind == "function":
                self.scopes[symbol.file].append(symbol)
        for label in (*case.roots, case.entry):
            if label is not None and (label not in self.index.symbols
                                      or self.index.symbols[label].kind != "function"):
                raise ValueError(f"{case.instance_id}: {label!r} is not a function in {case.commit}")


class ToolError(ValueError):
    """An invalid or unsupported model action."""


class Navigation:
    def __init__(self, snapshot, issue, entry=None):
        self.snapshot = snapshot
        self.public = {"issue": {"type": "issue", "text": issue}}
        self.viewer = None
        self.inspected = {}
        self.parents = {}
        if entry:
            symbol = snapshot.index.symbols[entry]
            self.public["entry"] = {"type": "entry", "path": symbol.file,
                                    "line_number": symbol.node.lineno, "symbol": entry}

    def action(self, action, turn):
        tool = action.get("tool")
        args = {key: value for key, value in action.items() if key != "tool"}
        schemas = {
            "find_file": ({"file_name", "evidence"}, {"dir"}),
            "search_dir": ({"search_term", "evidence"}, {"dir"}),
            "search_file": ({"search_term", "evidence"}, {"file"}),
            "open": ({"path"}, {"line_number"}),
            "goto": ({"line_number"}, set()),
            "scroll_up": (set(), set()),
            "scroll_down": (set(), set()),
            "submit": ({"ranked", "diagnosis", "evidence"}, set()),
        }
        if not isinstance(tool, str) or tool not in schemas:
            raise ToolError("Choose one of the documented tools.")
        required, optional = schemas[tool]
        if not required <= args.keys() <= required | optional:
            raise ToolError(f"Invalid fields for {tool}.")
        if tool == "submit":
            return self.submit(**args)
        observation = f"o{turn}"
        result, parents = getattr(self, tool)(**args)
        if result["type"] == "source":
            self.viewer = observation
            self.inspected[observation] = {
                symbol.id for symbol in self.snapshot.scopes.get(result["path"], ())
                if result["start"] <= symbol.node.lineno
                and symbol.node.body[0].lineno <= result["end"]
            }
        self.public[observation] = result
        self.parents[observation] = tuple(dict.fromkeys(parents))
        return {"id": observation, **result}

    def observation(self, evidence):
        if not isinstance(evidence, str) or evidence not in self.public:
            raise ToolError("Use an evidence ID from the issue, supplied entry, or prior tool results.")
        return self.public[evidence]

    def check_term(self, term, evidence, *, allow_glob=False):
        origin = self.observation(evidence)
        if not isinstance(term, str) or not 1 <= len(term.strip()) <= 100 or "\n" in term or "\r" in term:
            raise ToolError("Use a search term of 1–100 characters on one line.")
        term = term.strip()
        if allow_glob and term in {"*", "*.py"}:
            return term
        exposed = [origin.get("text", ""), origin.get("path", ""), origin.get("symbol", ""),
                   *origin.get("source", ()), *(row["path"] for row in origin.get("files", ())),
                   *(row["text"] for row in origin.get("matches", ()))]
        pattern = literal_pattern(term)
        if not any(pattern.search(value) for value in exposed):
            raise ToolError("The search term must appear in the cited public evidence.")
        return term

    def path_evidence(self, path, *, directory=False):
        if not isinstance(path, str) or not path or "\\" in path or ":" in path:
            raise ToolError("Use a repository-relative path with forward slashes.")
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or ".." in parsed.parts:
            raise ToolError("Use a repository-relative path inside the snapshot.")
        path = parsed.as_posix()
        if directory and path == ".":
            return path, "issue"
        for oid, obs in self.public.items():
            paths = [obs.get("path", ""), *(row["path"] for row in obs.get("files", ()))]
            if any(seen == path or (directory and seen.startswith(path + "/")) for seen in paths):
                return path, oid
            if obs["type"] == "issue" and literal_pattern(path).search(obs["text"]):
                return path, oid
        raise ToolError("Use a path shown in the issue, supplied entry, or prior tool results.")

    def directory_paths(self, directory):
        path, parent = self.path_evidence(directory, directory=True)
        prefix = "" if path == "." else path + "/"
        return (file for file in self.snapshot.lines if file.startswith(prefix)), parent

    def find_file(self, file_name, evidence, dir="."):
        term = self.check_term(file_name, evidence, allow_glob=True)
        paths, parent = self.directory_paths(dir)
        files = [{"path": path} for path in paths if fnmatchcase(PurePosixPath(path).name, term)]
        return self.bounded_results("files", files), (evidence, parent)

    def search_dir(self, search_term, evidence, dir="."):
        term = self.check_term(search_term, evidence)
        paths, parent = self.directory_paths(dir)
        files = []
        for path in paths:
            count = sum(term in line for line in self.snapshot.lines[path])
            if count:
                files.append({"path": path, "matching_lines": count})
        return self.bounded_results("files", files), (evidence, parent)

    def search_file(self, search_term, evidence, file=None):
        term = self.check_term(search_term, evidence)
        if file is None:
            path, parent = self.current_view()["path"], self.viewer
        else:
            path, parent = self.path_evidence(file)
        matches = [{"line_number": number, "text": line[:240]}
                   for number, line in enumerate(self.file_lines(path), 1) if term in line]
        return {"path": path, **self.bounded_results("matches", matches)}, (evidence, parent)

    @staticmethod
    def bounded_results(key, rows):
        result = {"type": key, "total": len(rows), key: rows}
        if len(rows) > RESULT_LIMIT:
            result[key] = []
            result["message"] = "Too many results. Narrow the search or select a smaller directory."
        return result

    def file_lines(self, path):
        if path not in self.snapshot.lines:
            raise ToolError("The path does not name a Python file in the snapshot.")
        return self.snapshot.lines[path]

    def line_evidence(self, path, line_number):
        if type(line_number) is not int or not 1 <= line_number <= len(self.file_lines(path)):
            raise ToolError("line_number must be an existing positive line number in this file.")
        for oid, obs in self.public.items():
            if obs.get("path") != path:
                continue
            if (obs.get("line_number") == line_number
                    or obs.get("start", 1) <= line_number <= obs.get("end", 0)
                    or any(hit["line_number"] == line_number for hit in obs.get("matches", ()))):
                return oid
        raise ToolError("Use a line shown by search_file or in a source window, or scroll to inspect nearby lines.")

    def window(self, path, start):
        lines = self.file_lines(path)
        start = max(1, min(start, max(1, len(lines) - SOURCE_LINES + 1)))
        end = min(start + SOURCE_LINES - 1, len(lines))
        source = [f"{number}: {line[:240]}" for number, line in enumerate(lines[start - 1:end], start)]
        return {"type": "source", "path": path, "start": start, "end": end, "source": source,
                "total_lines": len(lines)}

    def current_view(self):
        if self.viewer is None:
            raise ToolError("Open a file first.")
        return self.public[self.viewer]

    def open(self, path, line_number=None):
        path, parent = self.path_evidence(path)
        if line_number is None:
            entry = self.public.get("entry", {})
            if entry.get("path") == path:
                return self.window(path, entry["line_number"] - SOURCE_LINES // 2), (parent, "entry")
            return self.window(path, 1), (parent,)
        line_parent = self.line_evidence(path, line_number)
        return self.window(path, line_number - SOURCE_LINES // 2), (parent, line_parent)

    def goto(self, line_number):
        path = self.current_view()["path"]
        parent = self.line_evidence(path, line_number)
        return self.window(path, line_number - SOURCE_LINES // 2), (self.viewer, parent)

    def scroll_up(self):
        view = self.current_view()
        return self.window(view["path"], view["start"] - SOURCE_LINES), (self.viewer,)

    def scroll_down(self):
        view = self.current_view()
        return self.window(view["path"], view["start"] + SOURCE_LINES), (self.viewer,)

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
