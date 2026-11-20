"""Load public bug reports and private function labels for the teacher pilot."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from scripts.clone_pylint_verified import DEFAULT_CSV, load_cases


ROOT = Path(__file__).resolve().parents[2]

# Function-level candidates from the earlier iCoRe known-endpoints annotations.
# These labels are for the engineering pilot, not a causal benchmark.
PYLINT_ROOTS = {
    4970: ("pylint/checkers/similar.py::Run", "pylint/checkers/similar.py::Similar.run"),
    6386: ("pylint/config/utils.py::_preprocess_options",),
    6903: ("pylint/lint/run.py::_query_cpu", "pylint/lint/run.py::_cpu_count"),
    7080: ("pylint/lint/expand_modules.py::expand_modules",),
    7277: ("pylint/__init__.py::modify_sys_path",),
    8898: ("pylint/config/argument.py::_regexp_csv_transfomer",),
}


@dataclass(frozen=True)
class Case:
    instance_id: str
    checkout: Path
    commit: str
    issue: str
    roots: tuple[str, ...]
    entry: str | None = None


def load_pylint(selected: list[str]) -> list[Case]:
    cases = []
    for row in load_cases(DEFAULT_CSV, selected):
        instance = row["instance_id"]
        number = int(instance.rsplit("-", 1)[1])
        if number not in PYLINT_ROOTS:
            if selected:
                raise ValueError(f"No pilot root label for {instance}")
            continue
        cases.append(Case(instance, ROOT / "repos" / instance, row["base_commit"],
                          row["problem_statement"].strip(), PYLINT_ROOTS[number]))
    return cases


def load_manifest(path: Path, selected: list[str]) -> list[Case]:
    cases = {}
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                instance, checkout, commit, issue = (
                    row[key] for key in ("instance_id", "checkout", "commit", "issue")
                )
                if not all(isinstance(value, str) and value.strip()
                           for value in (instance, checkout, commit, issue)):
                    raise ValueError("instance_id, checkout, commit, and issue must be nonempty strings")
                if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", commit):
                    raise ValueError("commit must be a full Git object ID")
                roots = row["roots"]
                if not isinstance(roots, list) or not roots or not all(
                    isinstance(root, str) and "::" in root for root in roots
                ) or len(set(roots)) != len(roots):
                    raise ValueError("roots must be a nonempty list of unique path.py::Symbol IDs")
                entry = row.get("entry")
                if entry is not None and (not isinstance(entry, str) or "::" not in entry):
                    raise ValueError("entry must be a path.py::Symbol ID")
                if instance in cases:
                    raise ValueError(f"Duplicate instance_id {instance}")
                cases[instance] = Case(instance, (path.parent / checkout).resolve(), commit.lower(),
                                       issue.strip(), tuple(roots), entry)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    missing = set(selected) - cases.keys()
    if missing:
        raise ValueError(f"Unknown instance IDs: {', '.join(sorted(missing))}")
    return [case for instance, case in cases.items() if not selected or instance in selected]
