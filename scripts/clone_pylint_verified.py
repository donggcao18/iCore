"""Create independent buggy Pylint checkouts for the local Verified cases.

Run from any directory with Python 3.9+ and Git. No third-party modules needed.
Neither the production fix nor the reference-test patch is applied.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "data/swt-bench-verified/oracle_input_pylint_pytest.csv"
REPO = "pylint-dev/pylint"
REMOTE = "https://github.com/pylint-dev/pylint.git"


def git(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        ["git", *args], check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def load_cases(csv_path: Path, selected: list[str]) -> list[dict]:
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"repo", "instance_id", "base_commit"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"CSV must contain {sorted(required)}")
        rows = [row for row in reader if row["repo"] == REPO]
    if not rows:
        raise ValueError(f"No {REPO} cases in {csv_path}")
    seen = set()
    for row in rows:
        instance = row["instance_id"]
        if not re.fullmatch(r"pylint-dev__pylint-[0-9]+", instance):
            raise ValueError(f"Invalid instance ID: {instance!r}")
        if instance in seen:
            raise ValueError(f"Duplicate instance ID: {instance}")
        seen.add(instance)
        if not re.fullmatch(r"[0-9a-fA-F]{40}", row["base_commit"]):
            raise ValueError(f"Invalid base commit for {instance}")
    missing = set(selected) - seen
    if missing:
        raise ValueError(f"Unknown Pylint instance(s): {', '.join(sorted(missing))}")
    return [row for row in rows if not selected or row["instance_id"] in selected]


def clone_case(row: dict, destination: Path, remote: str) -> dict:
    commit = row["base_commit"].lower()
    if destination.exists():
        # Never reset, clean, or repurpose an existing directory.
        if not (destination / ".git").is_dir():
            raise ValueError(f"Existing destination is not an independent clone: {destination}")
        head = git("-C", str(destination), "rev-parse", "HEAD", capture=True)
        origin = git("-C", str(destination), "remote", "get-url", "origin", capture=True)
        if head != commit or origin != remote:
            raise ValueError(
                f"Refusing to modify {destination}: HEAD or origin differs from this case. "
                "Use a different --output-dir or inspect the folder manually."
            )
        print(f"Preserving existing checkout (including local edits): {destination}", flush=True)
    else:
        # Independent object storage; no worktree/shared-clone dependency on repos/pylint.
        # Partial clone avoids downloading every historical blob up front.
        git("clone", "--filter=blob:none", "--no-checkout", "--", remote, str(destination))
        if subprocess.run(
            ["git", "-C", str(destination), "cat-file", "-e", f"{commit}^{{commit}}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode:
            git("-C", str(destination), "fetch", "origin", commit)
        git("-C", str(destination), "checkout", "-b", "diagnostic-path", commit)
        head = git("-C", str(destination), "rev-parse", "HEAD", capture=True)
        if head != commit:
            raise ValueError(f"Unexpected HEAD in {destination}: {head}")
    return {
        "instance_id": row["instance_id"], "repo": REPO,
        "base_commit": commit, "checkout": str(destination), "origin": remote,
        "patches_applied_by_this_script": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                        help="Normalized Verified CSV with buggy base_commit values")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "repos")
    parser.add_argument("--instance-id", action="append", default=[],
                        help="Only clone this instance; repeat to select multiple cases")
    parser.add_argument("--remote", default=REMOTE, help="Git URL or local mirror path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and list cases without Git/network/filesystem changes")
    args = parser.parse_args()
    try:
        cases = load_cases(args.csv, args.instance_id)
        output = args.output_dir.resolve()
        for row in cases:
            print(f"{row['instance_id']} @ {row['base_commit']} -> {output / row['instance_id']}",
                  flush=True)
        if args.dry_run:
            print(f"Dry run: {len(cases)} checkout(s); no changes made.")
            return
        output.mkdir(parents=True, exist_ok=True)
        for row in cases:
            destination = output / row["instance_id"]
            record = clone_case(row, destination, args.remote)
            record["source_csv"] = str(args.csv.resolve())
            # Per-clone local metadata, outside the agent's tracked source tree.
            metadata = destination / ".git" / "icore-instance.json"
            if not metadata.exists():
                metadata.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"Ready: {len(cases)} buggy checkout(s) in {output}")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Error: {exc}\nExisting folders were not reset or deleted.\n")


if __name__ == "__main__":
    main()
