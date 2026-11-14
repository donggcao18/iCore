"""Validate coverage, source grounding, test reconstruction, graphs and probe artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess

from scripts.export_pylint_diagnostics import patched_tests, sha, test_span
from scripts.probe_pylint_diagnostics import CSV, ROOT


def main():
    with CSV.open(encoding="utf-8", newline="") as handle:
        cases = {r["instance_id"]: {k: r[k] for k in ("base_commit", "test_patch")}
                 for r in csv.DictReader(handle) if r["repo"] == "pylint-dev/pylint"}
    output = ROOT / "diagnostic_path/pylint_verified_diagnostic_paths.jsonl"
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    ids = [r["instance_id"] for r in records]
    assert len(ids) == len(set(ids)) and set(ids) == set(cases)
    required = {"schema_version", "instance_id", "repo", "base_commit", "input_policy", "test_cases",
                "production_entry_points", "trigger", "observation", "navigation_path", "root_cause",
                "diagnostic_nodes", "diagnostic_edges", "mechanism_summary", "source_evidence",
                "runtime_evidence", "generation_handoff", "limitations", "diagnosis_status",
                "full_test_status", "review_status"}
    for record in records:
        assert required <= record.keys()
        instance = record["instance_id"]
        row = cases[instance]
        root = ROOT / "repos" / instance
        prefix = ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root)]
        assert subprocess.check_output([*prefix, "rev-parse", "HEAD"], text=True).strip() == record["base_commit"] == row["base_commit"]
        assert not subprocess.check_output([*prefix, "status", "--porcelain"], text=True).strip()
        assert record["input_policy"]["mode"] == "test_patch_only"
        assert record["input_policy"]["production_patch_read"] is False
        assert record["input_policy"]["test_patch_sha256"] == sha(row["test_patch"])
        test_sources = patched_tests(root, row["test_patch"])
        for test in record["test_cases"]:
            span = test["source"]
            text = test_sources[span["path"]]
            assert sha(text) == span["reconstructed_file_sha256"]
            assert test_span(text, test["function"]) == (span["start_line"], span["end_line"])
        source_ids = set()
        for source in record["source_evidence"]:
            assert source["id"] not in source_ids
            source_ids.add(source["id"])
            assert source["revision"] == record["base_commit"]
            text = (root / source["path"]).read_text(encoding="utf-8")
            assert sha(text) == source["file_text_sha256"]
            assert "\n".join(text.splitlines()[source["start_line"] - 1:source["end_line"]]) == source["excerpt"]
        runtime = record["runtime_evidence"]
        artifact = ROOT / runtime["artifact"]
        assert sha(artifact.read_text(encoding="utf-8")) == runtime["artifact_text_sha256"]
        assert sha((ROOT / runtime["probe_script"]).read_text(encoding="utf-8")) == runtime["probe_script_text_sha256"]
        probe = json.loads(artifact.read_text(encoding="utf-8"))
        assert probe["base_commit"] == record["base_commit"]
        assert probe["test_patch_sha256"] == sha(row["test_patch"])
        assert probe["full_test_executed"] is False
        for rel, value in probe["probe_input_file_sha256"].items():
            assert hashlib.sha256((root / rel).read_bytes()).hexdigest() == value
        evidence_ids = source_ids | {"runtime_probe"}
        nodes = {n["id"]: n for n in record["diagnostic_nodes"]}
        assert len(nodes) == len(record["diagnostic_nodes"])
        for node in nodes.values():
            assert node["source_ref"] in source_ids
            assert set(node["evidence_refs"]) <= evidence_ids
        assert set(record["root_cause"]["source_refs"]) <= source_ids
        graph = {key: [] for key in nodes}
        for edge in record["diagnostic_edges"]:
            assert edge["source"] in nodes and edge["target"] in nodes
            assert set(edge["evidence_refs"]) <= evidence_ids
            graph[edge["source"]].append(edge["target"])
        starts = [key for key, value in nodes.items() if value["role"] == "origin_candidate"]
        reachable = set(starts)
        pending = list(starts)
        while pending:
            for key in graph[pending.pop()]:
                if key not in reachable:
                    reachable.add(key)
                    pending.append(key)
        assert any(nodes[k]["role"] in ("observation", "predicted_observation") for k in reachable)
        for step in record["navigation_path"]:
            assert step["source_ref"] in source_ids
        for entry in record["production_entry_points"]:
            text = (root / entry["path"]).read_text(encoding="utf-8")
            assert test_span(text, entry["symbol"]) == (entry["start_line"], entry["end_line"])
        assert record["full_test_status"] == "not_run"
        assert record["review_status"] == "self_reviewed"
        assert record["generation_handoff"]["policy"] == "oracle_test_conditioned"
        print(f"Validated {instance}: sources, reconstructed tests, entry, graph, probe provenance; checkout unchanged")
    print(f"Validated all {len(records)} JSONL records. This checks artifact integrity, not independent causal correctness.")


if __name__ == "__main__":
    main()
