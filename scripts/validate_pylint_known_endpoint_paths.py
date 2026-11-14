"""Check artifact integrity for the independently run known-endpoint path agent.

This checks coverage, grounding and graph structure, not causal correctness.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "diagnostic_path/pylint_known_endpoints_inputs.jsonl"
OUTPUT = ROOT / "diagnostic_path/pylint_verified_known_endpoints_diagnostic_paths.jsonl"


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reconstruct_tests(root, patch):
    result = {}
    for section in re.split(r"(?=^diff --git )", patch, flags=re.M):
        if not section.strip():
            continue
        lines = section.splitlines()
        rel = next(s[len("+++ b/"):] for s in lines if s.startswith("+++ b/"))
        assert rel.startswith("tests/") and ".." not in Path(rel).parts
        base = (root / rel).read_text(encoding="utf-8").splitlines()
        output, position, active = [], 0, False
        for line in lines:
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", line)
            if match:
                start = int(match.group(1)) - 1
                assert start >= position
                output.extend(base[position:start])
                position, active = start, True
            elif active and not line.startswith("\\ No newline"):
                if line.startswith((" ", "-")):
                    assert base[position] == line[1:]
                    if line.startswith(" "):
                        output.append(base[position])
                    position += 1
                elif line.startswith("+"):
                    output.append(line[1:])
                else:
                    raise ValueError(f"Unexpected diff line {line!r}")
        output.extend(base[position:])
        result[rel] = "\n".join(output) + "\n"
    return result


def check_source(obj, root, tests):
    rel = obj["path"]
    resolved = (root / rel).resolve()
    assert root.resolve() in resolved.parents, rel
    text = tests[rel] if obj.get("revision_kind") == "base_plus_test_patch_in_memory" else resolved.read_text(encoding="utf-8")
    lines = text.splitlines()
    start, end = obj["start_line"], obj["end_line"]
    assert 1 <= start <= end <= len(lines), (rel, start, end)
    if "file_text_sha256" in obj:
        assert text_hash(text) == obj["file_text_sha256"], rel
    if "excerpt" in obj:
        assert "\n".join(lines[start - 1:end]) == obj["excerpt"], (rel, start, end)


def check_sequence(sequence, nodes, edges, label):
    assert isinstance(sequence, list) and sequence, label
    assert all(isinstance(key, str) and key in nodes for key in sequence), label
    pairs = {(edge["source"], edge["target"]) for edge in edges}
    assert all((a, b) in pairs for a, b in zip(sequence, sequence[1:])), label


def main():
    inputs = {r["instance_id"]: r for r in map(json.loads, INPUT.read_text(encoding="utf-8").splitlines())}
    records = [json.loads(line) for line in OUTPUT.read_text(encoding="utf-8").splitlines()]
    ids = [r["instance_id"] for r in records]
    assert len(ids) == len(set(ids)) and set(ids) == set(inputs)
    required = {"schema_version", "instance_id", "repo", "base_commit", "input_provenance",
                "endpoint_verification", "test_entry", "entry_to_root_path", "diagnostic_nodes",
                "path_edges", "origin_to_observation", "mechanism_edges", "mechanism_summary",
                "evidence", "investigation_log", "generation_handoff", "status", "full_test_status", "limitations"}
    for record in records:
        assert required <= record.keys(), (record["instance_id"], required - record.keys())
        case = inputs[record["instance_id"]]
        root = ROOT / case["checkout"]
        assert record["schema_version"] == "2.0"
        assert record["base_commit"] == case["base_commit"]
        assert record["input_provenance"]["input_file_sha256"] == text_hash(INPUT.read_text(encoding="utf-8"))
        git = ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root)]
        assert subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip() == case["base_commit"]
        assert not subprocess.check_output([*git, "status", "--porcelain"], text=True).strip()
        tests = reconstruct_tests(root, case["test_entry"]["test_patch"])
        evidence = {e["id"]: e for e in record["evidence"]}
        assert len(evidence) == len(record["evidence"])
        for item in evidence.values():
            if item["kind"] == "source":
                check_source(item, root, tests)
                if item.get("revision_kind") != "base_plus_test_patch_in_memory":
                    assert item["revision"] == case["base_commit"]
            elif item["kind"] == "probe":
                artifact = ROOT / item["artifact"]
                assert text_hash(artifact.read_text(encoding="utf-8")) == item["artifact_text_sha256"]
        nodes = {n["id"]: n for n in record["diagnostic_nodes"]}
        assert len(nodes) == len(record["diagnostic_nodes"])
        for item in nodes.values():
            check_source(item, root, tests)
            assert set(item["evidence_refs"]) <= evidence.keys()
            if item.get("revision_kind") != "base_plus_test_patch_in_memory":
                assert item["revision"] == case["base_commit"]
        for edge in record["path_edges"] + record["mechanism_edges"]:
            assert edge["source"] in nodes and edge["target"] in nodes
            assert set(edge["evidence_refs"]) <= evidence.keys()
            assert edge["support"] in {"source_supported", "observed_in_isolated_probe", "observed_in_full_test", "unresolved"}
        for field, edges_field in (("entry_to_root_path", "path_edges"), ("origin_to_observation", "mechanism_edges")):
            if record[field]:
                check_sequence(record[field], nodes, record[edges_field], field)
        if record["status"] not in ("partial", "endpoint_conflict"):
            check_sequence(record["entry_to_root_path"], nodes, record["path_edges"], "entry path")
            check_sequence(record["origin_to_observation"], nodes, record["mechanism_edges"], "mechanism path")
            first = nodes[record["entry_to_root_path"][0]]
            last = nodes[record["entry_to_root_path"][-1]]
            assert any(first["path"] == e["path"] and first["symbol"].endswith(e["symbol"]) for e in case["test_entry"]["production_entries"])
            # Supplied root labels can describe a statement, not a canonical
            # function name (e.g. 'modify_sys_path unconditional pop'). Match
            # the actual revision-qualified origin span rather than its label.
            assert any(last["path"] == e["path"]
                       and last["start_line"] <= e["end_line"]
                       and e["start_line"] <= last["end_line"]
                       for e in case["given_root_cause"]["locations"])
        assert record["status"] in {"source_supported", "probe_supported", "partial", "endpoint_conflict"}
        print(f"Validated {case['instance_id']}: {record['status']}; sources, routes and evidence grounded; checkout clean")
    print(f"Validated {len(records)} records; integrity checks do not establish independent causal correctness.")


if __name__ == "__main__":
    main()
