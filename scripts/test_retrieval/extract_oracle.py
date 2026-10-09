"""Extract changed tests as generator-ready oracle retrieval context.

The patched output is a hindsight oracle: it includes developer-written tests
from ``test_patch``. The base output contains only matching test functions
that already existed at the buggy commit; newly added targets have no base test.
Select a dataset and repository on the command line.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scripts.retrieval_formats import test_retrieval


ROOT = Path(__file__).resolve().parents[2]
DATASETS = {
    "lite": "SWE-bench/SWE-bench_Lite",
    "swt-verified": "eth-sri/SWT-bench_Verified_bm25_27k_zsb",
}
DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class FileChange:
    path: str
    old_lines: set[int]
    new_lines: set[int]
    first_changed_line: int
    new_file: bool = False


@dataclass(frozen=True)
class TestFunction:
    name: str
    start: int
    end: int
    code_content: str


def run_git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *(["-c", f"safe.directory={cwd.resolve().as_posix()}"] if cwd else []), *args],
        cwd=cwd, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def ensure_repo(repo_dir: Path, clone_url: str) -> None:
    if (repo_dir / ".git").exists():
        return
    if repo_dir.exists():
        raise ValueError(f"{repo_dir} exists but is not a Git checkout")
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    run_git("clone", "--filter=blob:none", "--no-checkout", clone_url, str(repo_dir))


def read_repo_rows(csv_path: Path, repo: str) -> list[dict[str, str]]:
    csv.field_size_limit(10_000_000)
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle) if row["repo"] == repo]


def parse_patch(patch: str) -> list[FileChange]:
    changes: list[FileChange] = []
    current: FileChange | None = None
    old_line = new_line = 0
    in_hunk = False
    for line in patch.splitlines():
        match = DIFF_HEADER.match(line)
        if match:
            old_path, new_path = match.groups()
            if old_path != new_path:
                raise ValueError(f"Renamed test path needs explicit handling: {line}")
            if Path(new_path).is_absolute() or ".." in Path(new_path).parts:
                raise ValueError(f"Unsafe patch path: {new_path}")
            current = FileChange(new_path, set(), set(), 10**9)
            changes.append(current)
            in_hunk = False
            continue
        if current is None:
            continue
        if line == "--- /dev/null" or line.startswith("new file mode "):
            current.new_file = True
            continue
        match = HUNK_HEADER.match(line)
        if match:
            old_line, new_line = (int(value) for value in match.groups())
            in_hunk = True
            continue
        if not in_hunk or line.startswith("\\ No newline"):
            continue
        if line.startswith("+"):
            current.new_lines.add(new_line)
            current.first_changed_line = min(current.first_changed_line, new_line)
            new_line += 1
        elif line.startswith("-"):
            current.old_lines.add(old_line)
            current.first_changed_line = min(current.first_changed_line, new_line)
            old_line += 1
        elif line.startswith(" "):
            old_line += 1
            new_line += 1
        else:
            raise ValueError(f"Unexpected patch line: {line[:80]}")
    return changes


def source_at_commit(repo_dir: Path, commit: str, path: str) -> str | None:
    exists = run_git("cat-file", "-e", f"{commit}:{path}", cwd=repo_dir, check=False)
    if exists.returncode:
        return None
    return run_git("show", f"{commit}:{path}", cwd=repo_dir).stdout


def patched_sources(repo_dir: Path, commit: str, patch: str, changes: list[FileChange]) -> tuple[dict[str, str | None], dict[str, str]]:
    base: dict[str, str | None] = {}
    with tempfile.TemporaryDirectory(prefix="oracle-") as temp_name:
        temp_dir = Path(temp_name)
        for change in changes:
            old_source = source_at_commit(repo_dir, commit, change.path)
            if old_source is None and not change.new_file:
                raise ValueError(f"Missing base file {change.path} at {commit}")
            base[change.path] = old_source
            if old_source is not None:
                destination = temp_dir / change.path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(old_source.encode("utf-8"))
        patch_path = temp_dir / "oracle.patch"
        patch_path.write_text(patch, encoding="utf-8", newline="")
        run_git("apply", "--whitespace=nowarn", str(patch_path), cwd=temp_dir)
        after = {
            change.path: (temp_dir / change.path).read_text(encoding="utf-8")
            for change in changes
        }
    return base, after


def test_functions(source: str | None, filename: str) -> dict[str, TestFunction]:
    if source is None:
        return {}
    lines = source.splitlines()
    tree = ast.parse(source, filename=filename)
    functions: dict[str, TestFunction] = {}

    def make_function(node: ast.FunctionDef | ast.AsyncFunctionDef, class_node: ast.ClassDef | None = None) -> None:
        if not node.name.startswith("test_"):
            return
        start = min((decorator.lineno for decorator in node.decorator_list), default=node.lineno)
        body = "\n".join(lines[start - 1:node.end_lineno])
        if class_node is not None:
            class_start = min((decorator.lineno for decorator in class_node.decorator_list), default=class_node.lineno)
            header = "\n".join(lines[class_start - 1:class_node.lineno])
            name = f"{class_node.name}.{node.name}"
            body = f"{header}\n{body}"
        else:
            name = node.name
        functions[name] = TestFunction(name, start, node.end_lineno, body)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            make_function(node)
        elif isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    make_function(method, node)
    return functions


def fail_to_pass_targets(value: str) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    for label in json.loads(value):
        parts = label.split("::")
        if len(parts) < 2:
            raise ValueError(f"Unsupported FAIL_TO_PASS label: {label}")
        function = re.sub(r"\[.*\]$", "", parts[-1])
        class_name = ".".join(parts[1:-1])
        targets.append((parts[0], f"{class_name}.{function}" if class_name else function))
    return targets


def extract_instance(row: dict[str, str], repo_dir: Path) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, object]]]:
    changes = [change for change in parse_patch(row["test_patch"]) if change.path.endswith(".py")]
    if not changes:
        raise ValueError(f"No Python test files in {row['instance_id']}")
    base_sources, after_sources = patched_sources(repo_dir, row["base_commit"], row["test_patch"], changes)
    base_functions = {change.path: test_functions(base_sources[change.path], change.path) for change in changes}
    after_functions = {change.path: test_functions(after_sources[change.path], change.path) for change in changes}
    labels = fail_to_pass_targets(row["FAIL_TO_PASS"])
    label_set = set(labels)
    candidates: dict[tuple[str, str], dict[str, object]] = {}

    for change in changes:
        before = base_functions[change.path]
        after = after_functions[change.path]
        for name, function in after.items():
            old_function = before.get(name)
            changed_after = any(function.start <= line <= function.end for line in change.new_lines)
            changed_before = old_function is not None and any(
                old_function.start <= line <= old_function.end for line in change.old_lines
            )
            if changed_after or changed_before:
                candidates[(change.path, name)] = {
                    "file": change.path,
                    "name": name,
                    "kind": "new" if old_function is None else "modified",
                    "evidence": ["changed_lines"],
                    "fail_to_pass": (change.path, name) in label_set,
                    "line": function.start,
                }

    for path, name in labels:
        if path not in after_functions or name not in after_functions[path]:
            raise ValueError(f"{row['instance_id']}: FAIL_TO_PASS target missing after patch: {path}::{name}")
        key = (path, name)
        if key not in candidates:
            candidates[key] = {
                "file": path,
                "name": name,
                "kind": "new" if name not in base_functions[path] else "modified",
                "evidence": ["fail_to_pass_label"],
                "fail_to_pass": True,
                "line": after_functions[path][name].start,
            }
        elif "fail_to_pass_label" not in candidates[key]["evidence"]:
            candidates[key]["evidence"].append("fail_to_pass_label")

    ordered = sorted(candidates.values(), key=lambda item: (
        not item["fail_to_pass"],
        next((index for index, key in enumerate(labels) if key == (item["file"], item["name"])), len(labels)),
        next(index for index, change in enumerate(changes) if change.path == item["file"]),
        item["line"],
    ))
    patched: list[dict[str, str]] = []
    base: list[dict[str, str]] = []
    manifest: list[dict[str, object]] = []
    for item in ordered:
        path, name = item["file"], item["name"]
        patched.append({"name": name, "file": path, "code_content": after_functions[path][name].code_content})
        old_function = base_functions[path].get(name)
        if old_function is not None:
            base.append({"name": name, "file": path, "code_content": old_function.code_content})
        manifest.append({key: value for key, value in item.items() if key != "line"} | {"available_at_base": old_function is not None})
    return patched, base, manifest


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")


def dataset_name(value: str) -> str:
    for short_name, full_name in DATASETS.items():
        if value in (short_name, full_name):
            return short_name
    raise argparse.ArgumentTypeError(
        f"Unknown dataset {value!r}; choose lite or swt-verified "
        "(full Hugging Face IDs also work)"
    )


def repository_name(value: str) -> str:
    parts = value.split("/")
    if (len(parts) != 2 or any(part in ("", ".", "..") for part in parts)
            or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)):
        raise argparse.ArgumentTypeError("--repo must be owner/name, e.g. pylint-dev/pylint")
    return value


def default_paths(dataset: str, repo: str) -> tuple[Path, Path]:
    name = repo.split("/")[1]
    if dataset == "lite":
        return (ROOT / "data/swe-bench-lite/test.csv",
                ROOT / "retrieval_results/oracle/test/lite" / name)
    return (ROOT / "data/swt-bench-verified/test.csv",
            ROOT / "retrieval_results/oracle/test/swt-bench-verified" / name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=dataset_name, required=True,
                        help="lite or swt-verified; full Hugging Face dataset IDs also work")
    parser.add_argument("--repo", type=repository_name, required=True,
                        help="Benchmark repository, e.g. pytest-dev/pytest")
    parser.add_argument("--csv", type=Path, help="Override the dataset's default local CSV")
    parser.add_argument("--repo-dir", type=Path, help="Existing clone or destination for a new clone")
    parser.add_argument("--clone-url", help="Clone URL if --repo-dir does not exist")
    parser.add_argument("--output-dir", type=Path,
                        help="Directory for oracle JSON files")
    parser.add_argument("--instance-id", action="append", help="Process only this ID; repeat for several IDs")
    args = parser.parse_args()

    repo_name = args.repo.split("/")[1]
    default_csv, default_output_dir = default_paths(args.dataset, args.repo)
    csv_path = args.csv or default_csv
    repo_dir = args.repo_dir or ROOT / "repos" / repo_name
    clone_url = args.clone_url or f"https://github.com/{args.repo}.git"
    output_dir = args.output_dir or default_output_dir

    if not csv_path.is_file():
        hint = (" Export it first with: python -m scripts.export_swt_verified"
                if args.dataset == "swt-verified" else "")
        parser.error(f"Dataset CSV not found: {csv_path}.{hint}")
    rows = read_repo_rows(csv_path, args.repo)
    if args.dataset == "swt-verified" and any(
        row.get("source_dataset") != DATASETS["swt-verified"]
        or not row.get("test_patch", "").startswith("diff --git ")
        for row in rows
    ):
        parser.error("SWT Verified CSV must be normalized by scripts.export_swt_verified")
    if args.instance_id:
        requested = set(args.instance_id)
        rows = [row for row in rows if row["instance_id"] in requested]
        missing = requested - {row["instance_id"] for row in rows}
        if missing:
            parser.error(f"Unknown {args.repo} instance IDs: {', '.join(sorted(missing))}")
    if not rows:
        parser.error(f"No {args.repo} rows found")
    ensure_repo(repo_dir, clone_url)

    patched_output: dict[str, list[dict[str, str]]] = {}
    base_output: dict[str, list[dict[str, str]]] = {}
    manifest_output: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        instance_id = row["instance_id"]
        patched, base, manifest = extract_instance(row, repo_dir)
        patched_output[instance_id] = patched
        base_output[instance_id] = base
        manifest_output[instance_id] = manifest
        print(f"{instance_id}: {len(patched)} changed tests, {len(base)} available at base")

    write_json(output_dir / "related_tests_oracle_patched.json", test_retrieval(patched_output))
    write_json(output_dir / "related_tests_oracle_base.json", test_retrieval(base_output))
    write_json(output_dir / "oracle_manifest.json", manifest_output)
    print(f"Wrote oracle context and manifest to {output_dir}")


if __name__ == "__main__":
    main()
