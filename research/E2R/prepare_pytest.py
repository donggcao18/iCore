"""Prepare patched pytest test entries and base-side root-cause candidates."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile

from run_pylint import DEFAULT_CSV, HERE, REPOS, apply_test_patch, check_selector, git
from scripts.code_retrieval.extract_oracle import extract_instance
from scripts.test_retrieval.augment_oracle import git_blobs

REPO = "pytest-dev/pytest"
FUNCTION_TYPES = {"function", "top-level function", "class_function"}


def endpoint_locations(row, repo):
    base, _, _ = extract_instance(row, repo)
    # A fix can add a missing member without editing an existing function.
    # In that case the enclosing buggy class is the available endpoint.
    functions = {key: obj for key, obj in base.items() if obj["node_type"] in FUNCTION_TYPES}
    candidates = functions or {key: obj for key, obj in base.items() if obj["node_type"] == "class"}
    if not candidates:
        raise ValueError(f"No base-side endpoint for {row['instance_id']}")
    return [
        {
            "path": obj["path"], "symbol": key.split("::", 1)[1],
            "start_line": obj["code_start_line"], "end_line": obj["code_end_line"],
        }
        for key, obj in sorted(candidates.items())
    ]


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@contextmanager
def python_snapshot(repo, commit):
    """Export the exact Python blobs from the local partial clone."""
    entries = git(repo, "ls-tree", "-rz", commit).split(b"\0")
    paths = []
    for entry in entries:
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        if metadata.split()[0] in {b"100644", b"100755"} and path.endswith(b".py"):
            paths.append(path.decode("utf-8"))
    with tempfile.TemporaryDirectory(prefix="e2r-pytest-", dir=REPOS) as directory:
        tree = Path(directory)
        for name, content in git_blobs(repo, commit, paths).items():
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        yield tree


def prepare(row, repo, csv_path, output):
    patch = row["test_patch"].encode("utf-8")
    selectors = json.loads(row["FAIL_TO_PASS"])
    if not selectors or not patch.strip():
        raise ValueError("Missing FAIL_TO_PASS selectors or test patch")
    root = {
        "description": "Base-side edit locations from the reference production patch.",
        "provenance": "Selected using the dataset production fix. Treat them as candidates and establish the mechanism from the buggy source.",
        "locations": endpoint_locations(row, repo),
    }
    record = {
        "instance_id": row["instance_id"], "repo": REPO, "base_commit": row["base_commit"],
        "source_clone": str(repo), "dataset": str(csv_path), "snapshot_scope": "tracked_python_files",
        "applied_test_patch_sha256": hashlib.sha256(patch).hexdigest(),
        "given_root_cause": root,
    }
    entries = []
    with python_snapshot(repo, row["base_commit"]) as tree:
        apply_test_patch(tree, patch)
        for index, selector in enumerate(selectors, start=1):
            node = check_selector(tree, selector)
            names = selector.split("[", 1)[0].split("::")
            location = {"path": names[0], "symbol": ".".join(names[1:]),
                        "start_line": node.lineno, "end_line": node.end_lineno}
            entry = {"entry_test": selector, "entry_location": location,
                     "case_dir": f"test-{index:03d}"}
            destination = output / row["instance_id"] / entry["case_dir"]
            destination.mkdir(parents=True)
            write_json(destination / "input.json", {**record, **entry})
            lines = (tree / location["path"]).read_text(encoding="utf-8").splitlines()
            first = min((decorator.lineno for decorator in node.decorator_list), default=node.lineno)
            excerpt = "\n".join(f"{n:4} {lines[n - 1]}" for n in range(first, node.end_lineno + 1))
            (destination / "entry_source.txt").write_text(excerpt + "\n", encoding="utf-8")
            entries.append(entry)
    return {**record, "entries": entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--repo", type=Path, default=REPOS / "pytest")
    parser.add_argument("--instance-id", action="append", default=[])
    args = parser.parse_args()
    csv_path = args.csv.resolve()
    repo = args.repo.resolve()
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["repo"] == REPO]
    if not rows:
        parser.error(f"No pytest instances in {csv_path}")
    for row in rows:
        if (not re.fullmatch(r"pytest-dev__pytest-[0-9]+", row["instance_id"])
                or not re.fullmatch(r"[0-9a-fA-F]{40}", row["base_commit"])):
            parser.error(f"Invalid pytest instance or base commit: {row['instance_id']!r}")
    requested = set(args.instance_id)
    missing = requested - {row["instance_id"] for row in rows}
    if missing:
        parser.error(f"Unknown pytest instances: {', '.join(sorted(missing))}")
    rows = [row for row in rows if not requested or row["instance_id"] in requested]

    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = Path(tempfile.mkdtemp(prefix=f"{stamp}-pytest-", dir=runs))
    print(f"Run output: {output}", flush=True)
    failed = False
    with (output / "inputs.jsonl").open("w", encoding="utf-8") as inputs:
        for row in rows:
            instance = row["instance_id"]
            try:
                record = prepare(row, repo, csv_path, output)
            except (OSError, ValueError, RuntimeError, SyntaxError) as exc:
                print(f"{instance}: {exc}", flush=True)
                failed = True
                continue
            inputs.write(json.dumps(record, ensure_ascii=False) + "\n")
            inputs.flush()
            print(f"{instance}: {len(record['entries'])} entry selector(s)", flush=True)
    if failed:
        parser.exit(1)


if __name__ == "__main__":
    main()
