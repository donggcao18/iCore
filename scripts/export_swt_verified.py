"""Export all SWT-bench Verified repositories as one local SWE-style CSV.

The source dataset stores the test diff in ``patch`` and the production fix in
``test_patch``. This export swaps those two fields so the existing oracle
extractor receives the actual test diff in ``test_patch``.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path


DATASET = "eth-sri/SWT-bench_Verified_bm25_27k_zsb"
ROOT = Path(__file__).resolve().parents[1]
FIELDS = (
    "repo", "instance_id", "base_commit", "patch", "test_patch",
    "problem_statement", "hints_text", "created_at", "version",
    "FAIL_TO_PASS", "PASS_TO_PASS", "environment_setup_commit",
    "difficulty", "source_dataset",
)


def fetch_page(offset: int, length: int) -> dict:
    query = urllib.parse.urlencode({
        "dataset": DATASET, "config": "default", "split": "test",
        "offset": offset, "length": length,
    })
    request = urllib.request.Request(
        f"https://datasets-server.huggingface.co/rows?{query}",
        headers={"User-Agent": "iCoRe-SWT-Verified-export/1.0"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def unwrap_test_patch(value: str) -> str:
    text = value.strip("\r\n")
    if not (text.startswith("<patch>\n") and text.endswith("\n</patch>")):
        raise ValueError("Unexpected SWT-bench test patch wrapper")
    # Remove the wrapper's separator newline, preserving a space-only context
    # line at the end of the final hunk (Git requires it for hunk line counts).
    diff = text[len("<patch>\n"):-len("</patch>")]
    diff = diff[:-1] if diff.endswith("\n") else diff
    if not diff.endswith("\n"):
        diff += "\n"
    if not diff.startswith("diff --git "):
        raise ValueError("SWT-bench test patch has no diff --git header")
    return diff


def export_rows() -> tuple[list[dict], int]:
    selected: list[dict] = []
    offset = 0
    total: int | None = None
    while total is None or offset < total:
        page = fetch_page(offset, 100)
        if total is None:
            total = page["num_rows_total"]
        elif page["num_rows_total"] != total:
            raise ValueError("Dataset row count changed during export; retry")
        batch = page["rows"]
        if not batch:
            raise ValueError(f"Dataset ended unexpectedly at row {offset}")
        for item in batch:
            source = item["row"]
            row = {name: source.get(name, "") for name in FIELDS if name != "source_dataset"}
            row["patch"] = source["test_patch"]
            row["test_patch"] = unwrap_test_patch(source["patch"])
            row["source_dataset"] = DATASET
            selected.append(row)
        offset += len(batch)
    if len({row["instance_id"] for row in selected}) != len(selected) or not selected:
        raise ValueError("No unique instances found in the dataset")
    return selected, total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Destination CSV path")
    args = parser.parse_args()
    output = args.output or ROOT / "data/swt-bench-verified/test.csv"
    rows, total = export_rows()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Exported {len(rows)} instances across {len({row['repo'] for row in rows})} "
          f"repositories from {total} {DATASET} test rows to {output}")


if __name__ == "__main__":
    main()
