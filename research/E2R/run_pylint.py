"""Construct diagnostic paths for the Pylint cases in the normalized CSV."""

from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import hashlib
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from scripts.brt_harness.execution import git
from scripts.clone_pylint_verified import DEFAULT_CSV, load_cases

REPOS = ROOT / "repos"
ENDPOINTS = ROOT / "research/diagnostic_path/pylint_known_endpoints_inputs.jsonl"


@contextmanager
def snapshot(source: Path, commit: str):
    """Check out a base tree through a disposable Git index."""
    with tempfile.TemporaryDirectory(prefix="e2r-", dir=REPOS) as directory:
        temp = Path(directory)
        tree = temp / "repo"
        tree.mkdir()
        env = {**os.environ, "GIT_INDEX_FILE": str(temp / "index")}
        command = [
            "git", "-c", f"safe.directory={source.resolve().as_posix()}",
            "-c", "core.autocrlf=false", "-C", str(source),
        ]
        if os.name == "nt":
            # Match Git for Windows checkouts without symlink creation privileges.
            command.extend(["-c", "core.symlinks=false"])
        for args in (("read-tree", commit),
                     ("checkout-index", "--all", f"--prefix={tree.as_posix()}/")):
            result = subprocess.run(
                [*command, *args], env=env, capture_output=True, timeout=120,
            )
            if result.returncode:
                raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
        yield tree


def load_root_causes(path: Path) -> dict:
    roots = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            cause = record["given_root_cause"]
            roots[record["instance_id"]] = {
                "base_commit": record["base_commit"],
                "provenance": cause["provenance"],
                "description": cause["description"],
                "locations": [
                    {key: location[key] for key in ("path", "symbol", "start_line", "end_line")}
                    for location in cause["locations"]
                ],
            }
    return roots


def check_selector(tree: Path, selector: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Resolve the test function in the patched source without importing it."""
    filename, *symbols = selector.split("[", 1)[0].split("::")
    if not symbols:
        raise ValueError(f"Expected a test function selector, got {selector}")
    node = ast.parse((tree / filename).read_bytes(), filename=filename)
    for symbol in symbols:
        node = next(
            (item for item in node.body
             if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
             and item.name == symbol),
            None,
        )
        if node is None:
            raise ValueError(f"Cannot find {symbol!r} in the patched entry {selector}")
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise ValueError(f"Selector does not end at a test function: {selector}")
    return node


def apply_test_patch(tree: Path, patch: bytes) -> None:
    # Give git apply a local root so it cannot resolve paths under iCoRe.
    git(tree, "init", "--quiet")
    git(tree, "-c", "core.autocrlf=false", "apply", "--recount",
        "--whitespace=nowarn", "-", input=patch)


def run_agent(args: argparse.Namespace, tree: Path, output: Path, prompt: str) -> None:
    final_path = output / "path.json"
    command = [
        args.codex, "--ask-for-approval", "never", "exec",
        "--cd", str(tree), "--sandbox", "read-only", "--ephemeral", "--json",
        "--output-schema", str(HERE / "path.schema.json"),
        "--output-last-message", str(final_path),
    ]
    if args.model:
        command.extend(["--model", args.model])

    with (output / "codex-events.jsonl").open("wb") as events, (
        output / "codex-stderr.log"
    ).open("wb") as stderr:
        result = subprocess.run(
            [*command, "-"], cwd=tree, input=prompt.encode("utf-8"),
            stdout=events, stderr=stderr,
        )
    if result.returncode:
        raise RuntimeError(f"Codex exited with {result.returncode}. See {output / 'codex-stderr.log'}")
    path = json.loads(final_path.read_text(encoding="utf-8"))
    print(f"  {path['status']}: {final_path}", flush=True)


def run_case(row: dict, cause: dict, args: argparse.Namespace, output: Path, guide: str) -> None:
    instance = row["instance_id"]
    base_commit = row["base_commit"].lower()
    if cause["base_commit"] != base_commit or not cause["locations"]:
        raise ValueError(f"Missing root-cause locations for {instance} at {base_commit}")

    source = REPOS / instance
    if not (source / ".git").exists():
        source = REPOS / "pylint"
    if not (source / ".git").exists():
        raise ValueError(f"Missing local Pylint clone under {REPOS}")
    git(source, "cat-file", "-e", f"{base_commit}^{{commit}}")

    patch = row["test_patch"].encode("utf-8")
    selectors = json.loads(row["FAIL_TO_PASS"])
    if not patch.strip() or not selectors:
        raise ValueError(f"Missing test patch or FAIL_TO_PASS entry for {instance}")
    with snapshot(source, base_commit) as tree:
        apply_test_patch(tree, patch)
        for selector in selectors:
            check_selector(tree, selector)

        for index, selector in enumerate(selectors, start=1):
            destination = output / instance / f"test-{index:03d}"
            destination.mkdir(parents=True)
            request = {
                "instance_id": instance,
                "repo": row["repo"],
                "base_commit": base_commit,
                "entry_test": selector,
                "given_root_cause": {key: cause[key] for key in ("description", "locations", "provenance")},
            }
            request_json = json.dumps(request, indent=2, ensure_ascii=False)
            metadata = {
                **request,
                "source_clone": str(source),
                "dataset": str(args.csv.resolve()),
                "root_cause_source": str(args.endpoints.resolve()),
                "applied_test_patch_sha256": hashlib.sha256(patch).hexdigest(),
            }
            (destination / "input.json").write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(f"{instance}  {selector}", flush=True)
            if not args.prepare_only:
                run_agent(args, tree, destination, f"{guide}\n\nCase input\n\n{request_json}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--endpoints", type=Path, default=ENDPOINTS)
    parser.add_argument("--instance-id", action="append", default=[],
                        help="Select an instance. Repeat to select more than one.")
    parser.add_argument("--prepare-only", action="store_true",
                        help="Apply patches and verify entry functions without calling Codex.")
    parser.add_argument("--model", help="Override the Codex CLI's configured model.")
    args = parser.parse_args()
    args.codex = shutil.which("codex")
    if not args.prepare_only and not args.codex:
        parser.error("Codex CLI was not found on PATH.")

    try:
        cases = load_cases(args.csv, args.instance_id)
        roots = load_root_causes(args.endpoints)
        guide = (HERE / "DIAGNOSTIC_PATH.md").read_text(encoding="utf-8")
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Error loading inputs: {exc}\n")

    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = Path(tempfile.mkdtemp(prefix=f"{timestamp}-", dir=runs))
    print(f"Run output: {output}", flush=True)
    failed = False
    for row in cases:
        instance = row["instance_id"]
        try:
            run_case(row, roots[instance], args, output, guide)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            failed = True
            destination = output / instance
            destination.mkdir(exist_ok=True)
            (destination / "error.log").write_text(f"{exc}\n", encoding="utf-8")
            print(f"{instance}: {exc}", file=sys.stderr, flush=True)
    if failed:
        parser.exit(1)


if __name__ == "__main__":
    main()
