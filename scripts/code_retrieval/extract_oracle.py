"""Store patch-guided production-code context in the generator's JSON format.

The base variant selects objects using the gold production patch but supplies
their historical buggy source. The patched variant contains the developer fix
and is a separate hindsight comparison. Neither variant changes the checkout.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.test_retrieval.extract_oracle import (
    DATASETS, ROOT, dataset_name, default_paths as test_default_paths,
    ensure_repo, read_repo_rows, repository_name,
)
from scripts.test_retrieval.static_dependencies import DependencyIndex, Symbol


OUTPUT_FILES = (
    "code_retrieval_oracle_base.json",
    "code_retrieval_oracle_patched.json",
    "oracle_code_manifest.json",
)


def default_paths(dataset: str, repo: str) -> tuple[Path, Path]:
    csv_path, test_output = test_default_paths(dataset, repo)
    return csv_path, ROOT / "retrieval_results/code/oracle" / test_output.parent.name / repo.split("/")[1]


def code_document(index: DependencyIndex, symbol: Symbol, revision: str) -> dict:
    parent = index.symbols.get(symbol.parent)
    if symbol.kind == "function":
        node_type = "class_function" if parent and parent.kind == "class" else "top-level function" if parent and parent.kind == "module" else "function"
    else:
        node_type = {"class": "class", "variable": "global_var", "module": "file"}[symbol.kind]
    return {
        "obj_name": (Path(symbol.file).stem if symbol.kind == "module" else
                     symbol.name.rsplit(".", 1)[-1] if node_type == "class_function" else symbol.name),
        "node_type": node_type,
        "path": symbol.file,
        "parent": parent.name if parent and parent.kind != "module" else None,
        "qualified_name": symbol.name,
        "code_start_line": symbol.start,
        "code_end_line": symbol.end,
        "code_content": "\n".join(index.sources[symbol.file].splitlines()[symbol.start - 1:symbol.end]),
        "revision": revision,
        "selection_evidence": [],
    }


def serialize_code_oracle(before: DependencyIndex, after: DependencyIndex,
                          symbols: list[dict], base_commit: str) -> tuple[dict, dict, dict]:
    base: dict[str, dict] = {}
    patched: dict[str, dict] = {}
    records = []
    for changed in symbols:
        record = dict(changed)
        record["selected_base"] = []
        record["selected_patched"] = []
        for target in changed["base_targets"]:
            symbol = before.symbols[target["id"]]
            doc = base.setdefault(symbol.id, code_document(before, symbol, "base"))
            evidence = {"changed_symbol": changed["id"], "reason": target["reason"], "confidence": target["confidence"]}
            if evidence not in doc["selection_evidence"]:
                doc["selection_evidence"].append(evidence)
            record["selected_base"].append(symbol.id)
        symbol = after.symbols.get(changed["id"])
        if symbol:
            doc = patched.setdefault(symbol.id, code_document(after, symbol, "patched"))
            doc["selection_evidence"].append({"changed_symbol": changed["id"], "reason": "changed_lines", "confidence": 1.0})
            record["selected_patched"].append(symbol.id)
        records.append(record)
    manifest = {
        "base_commit": base_commit,
        "selection_uses_gold_patch": True,
        "base_contains_developer_fix": False,
        "patched_contains_developer_fix": True,
        "changed_symbols": records,
        "counts": {"changed_symbols": len(symbols), "base_objects": len(base), "patched_objects": len(patched)},
        "base_parse_errors": before.diagnostics,
        "patched_parse_errors": after.diagnostics,
    }
    return base, patched, manifest


def extract_instance(row: dict[str, str], repo_dir: Path) -> tuple[dict, dict, dict]:
    # Shared production-patch handling also drives the augmented test oracle.
    from scripts.test_retrieval.augment_oracle import (
        apply_production_patch, changed_symbols, decode_source, git_blobs, production_changes,
    )

    changes = production_changes(row["patch"])
    paths = sorted({change.old_path for change in changes
                    if not change.new_file and change.old_path.endswith(".py")})
    sources = {path: decode_source(blob) for path, blob in git_blobs(repo_dir, row["base_commit"], paths).items()}
    after_sources = {path: source for path, source in apply_production_patch(
        repo_dir, row["base_commit"], row["patch"], changes).items() if source is not None}
    before = DependencyIndex(sources)
    after = DependencyIndex(after_sources).build()
    symbols = changed_symbols(before, after, changes)
    base, patched, manifest = serialize_code_oracle(before, after, symbols, row["base_commit"])
    # If a historical grammar cannot be parsed, keep explicit file context
    # instead of silently dropping the affected file from code retrieval.
    for index, output, revision in ((before, base, "base"), (after, patched, "patched")):
        for diagnostic in index.diagnostics:
            path = diagnostic["file"]
            source = index.sources[path]
            output[f"{path}::<module>"] = {
                "obj_name": Path(path).stem, "node_type": "file", "path": path,
                "parent": None, "qualified_name": "<module>", "revision": revision,
                "code_start_line": 1, "code_end_line": len(source.splitlines()),
                "code_content": source,
                "selection_evidence": [{"reason": "unparsed_changed_file", "confidence": 0.2}],
            }
    manifest["counts"].update(base_objects=len(base), patched_objects=len(patched))
    manifest["non_python_files"] = [change.path for change in changes
                                    if not change.path.endswith(".py") and not change.old_path.endswith(".py")]
    if not base:
        manifest["base_empty_reason"] = "No affected object or mapped caller exists in parsed base source"
    return base, patched, manifest


def store_instance(output_dir: Path, instance_id: str, values: tuple[dict, dict, dict]) -> None:
    """Preserve other instances when processing an explicit subset."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, value in zip(OUTPUT_FILES, values):
        destination = output_dir / filename
        output = json.loads(destination.read_text(encoding="utf-8")) if destination.exists() else {}
        output[instance_id] = value
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(output, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=dataset_name, required=True)
    parser.add_argument("--repo", type=repository_name, required=True)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--repo-dir", type=Path)
    parser.add_argument("--clone-url")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--instance-id", action="append")
    args = parser.parse_args()
    default_csv, default_output = default_paths(args.dataset, args.repo)
    csv_path = args.csv or default_csv
    if not csv_path.is_file():
        parser.error(f"Dataset CSV not found: {csv_path}")
    rows = read_repo_rows(csv_path, args.repo)
    if args.dataset == "swt-verified" and any(row.get("source_dataset") != DATASETS["swt-verified"] for row in rows):
        parser.error("SWT CSV must first be normalized by scripts.export_swt_verified")
    if args.instance_id:
        requested = set(args.instance_id)
        missing = requested - {row["instance_id"] for row in rows}
        if missing:
            parser.error(f"Unknown instance IDs: {', '.join(sorted(missing))}")
        rows = [row for row in rows if row["instance_id"] in requested]
    if not rows:
        parser.error(f"No {args.repo} rows found")
    repo_dir = (args.repo_dir or ROOT / "repos" / args.repo.split("/")[1]).resolve()
    ensure_repo(repo_dir, args.clone_url or f"https://github.com/{args.repo}.git")
    output_dir = args.output_dir or default_output
    for row in rows:
        values = extract_instance(row, repo_dir)
        store_instance(output_dir, row["instance_id"], values)
        print(f"{row['instance_id']}: {len(values[0])} base code objects, {len(values[1])} patched code objects", flush=True)
    print(f"Wrote code oracle retrieval to {output_dir}")


if __name__ == "__main__":
    main()
