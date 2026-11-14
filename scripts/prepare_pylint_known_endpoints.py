"""Prepare endpoint/function/test inputs without passing previously built paths.

The preparer can read prior records; the path-construction agent receives only
the whitelisted endpoint facts below and existing buggy-function extracts.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    old_path = ROOT / "diagnostic_path/pylint_verified_diagnostic_paths.jsonl"
    old = {r["instance_id"]: r for r in map(json.loads, old_path.read_text(encoding="utf-8").splitlines())}
    functions_path = ROOT / "retrieval_results/oracle/code/swt-bench-verified/pylint/code_retrieval_oracle_base.json"
    functions = json.loads(functions_path.read_text(encoding="utf-8"))
    csv_path = ROOT / "data/swt-bench-verified/oracle_input_pylint_pytest.csv"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = [{key: row[key] for key in ("instance_id", "repo", "base_commit", "test_patch", "source_dataset")}
                for row in csv.DictReader(handle) if row["repo"] == "pylint-dev/pylint"]
    output = []
    for row in rows:
        instance = row["instance_id"]
        previous = old[instance]
        assert previous["base_commit"] == row["base_commit"]
        refs = {s["id"]: s for s in previous["source_evidence"]}
        locations = [{key: refs[ref][key] for key in ("path", "symbol", "start_line", "end_line")}
                     for ref in previous["root_cause"]["source_refs"]]
        docs = []
        for symbol_id, doc in functions[instance].items():
            if doc["node_type"] not in ("class_function", "top-level function", "global_var"):
                continue
            content = (ROOT / "repos" / instance / doc["path"]).read_text(encoding="utf-8")
            start, end = doc["code_start_line"], doc["code_end_line"]
            exact = "\n".join(content.splitlines()[start - 1:end])
            assert exact.strip() == doc["code_content"].strip(), (instance, symbol_id)
            docs.append({"symbol_id": symbol_id, "path": doc["path"],
                         "qualified_symbol": symbol_id.split("::", 1)[1],
                         "start_line": start, "end_line": end, "code": exact,
                         "revision": row["base_commit"],
                         "file_text_sha256": hashlib.sha256(content.encode()).hexdigest()})
        output.append({
            "schema_version": "2.0-input", "instance_id": instance, "repo": row["repo"],
            "base_commit": row["base_commit"], "checkout": f"repos/{instance}",
            "source_dataset": row["source_dataset"],
            "given_root_cause": {"status": "supplied_candidate_not_gold",
                                 "provenance": "Prior source/probe-supported candidate; only description and locations supplied, not the previous path or justification",
                                 "description": previous["root_cause"]["transition_or_omission"],
                                 "locations": locations},
            "production_functions": docs,
            "production_functions_provenance": {"source": functions_path.relative_to(ROOT).as_posix(),
                                                "selection": "Privileged production-patch-based selection from an existing oracle artifact; current preparer reads only buggy snippets, not production patches/manifests/fixed snippets",
                                                "content": "Exact checked buggy source; function/global definitions only, excluding broad class/module summaries"},
            "test_entry": {"cases": previous["test_cases"],
                           "production_entries": [{key: e[key] for key in ("symbol", "path", "start_line", "end_line", "resolution")}
                                                  for e in previous["production_entry_points"]],
                           "test_patch": row["test_patch"]},
            "excluded_previous_fields": ["navigation_path", "diagnostic_nodes", "diagnostic_edges", "mechanism_summary", "generation_handoff", "runtime_evidence", "investigation_log", "root_cause.justification"],
        })
    path = ROOT / "diagnostic_path/pylint_known_endpoints_inputs.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in output), encoding="utf-8")
    print(f"Prepared {len(output)} known-endpoint cases: {path}")
    for row in output:
        print(row["instance_id"], len(row["production_functions"]), "buggy definitions")


if __name__ == "__main__":
    main()
