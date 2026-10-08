"""Augment the base oracle with existing tests related to the gold code patch.

All test snippets come from base_commit. Selection uses hindsight production
changes and is an oracle experiment, not a deployable bug-report-only retriever.
No target repository is checked out, imported, or executed.
"""

from __future__ import annotations

import argparse
import configparser
import io
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import tokenize
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from scripts.test_retrieval.extract_oracle import (
    DATASETS, HUNK_HEADER, ROOT, dataset_name, default_paths, ensure_repo,
    extract_instance, parse_patch, read_repo_rows, repository_name, run_git,
)
from scripts.test_retrieval.static_dependencies import DependencyIndex
from scripts.retrieval_formats import test_retrieval


@dataclass
class ProductionChange:
    old_path: str
    path: str
    old_lines: set[int] = field(default_factory=set)
    new_lines: set[int] = field(default_factory=set)
    new_file: bool = False
    deleted_file: bool = False


def safe_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if (not normalized or PurePosixPath(normalized).is_absolute()
            or ".." in PurePosixPath(normalized).parts or ":" in normalized
            or "\n" in normalized or "\r" in normalized):
        raise ValueError(f"Unsafe patch path: {path!r}")
    return normalized


def production_changes(patch: str) -> list[ProductionChange]:
    """Parse Git diffs and plain unified diffs used by SWT Verified."""
    changes = []
    current = None
    in_hunk = False
    old_line = new_line = 0
    old_remaining = new_remaining = 0
    git_header_pending = False
    lines = patch.splitlines()
    for position, line in enumerate(lines):
        if line.startswith("diff --git "):
            parts = shlex.split(line)
            if len(parts) != 4 or not parts[2].startswith("a/") or not parts[3].startswith("b/"):
                raise ValueError(f"Unsupported diff header: {line}")
            current = ProductionChange(safe_path(parts[2][2:]), safe_path(parts[3][2:]))
            changes.append(current)
            in_hunk = False
            git_header_pending = True
            continue
        if line.startswith("--- ") and (not in_hunk or not old_remaining and not new_remaining):
            if position + 1 >= len(lines) or not lines[position + 1].startswith("+++ "):
                raise ValueError("Production file header is missing its +++ path")
            paths = []
            for header, prefix in ((line, "a/"), (lines[position + 1], "b/")):
                value = header[4:].split("\t", 1)[0]
                if value.startswith('"'):
                    quoted = shlex.split(value)
                    if len(quoted) != 1:
                        raise ValueError(f"Unsupported file header: {header}")
                    value = quoted[0]
                if value == "/dev/null":
                    paths.append(None)
                elif value.startswith(prefix):
                    paths.append(safe_path(value[len(prefix):]))
                else:
                    raise ValueError(f"Unsupported file header: {header}")
            old_path, new_path = paths
            if old_path is None and new_path is None:
                raise ValueError("Production file headers cannot both use /dev/null")
            if not git_header_pending:
                current = ProductionChange(old_path or new_path, new_path or old_path)
                changes.append(current)
            elif ((old_path is not None and old_path != current.old_path)
                  or (new_path is not None and new_path != current.path)):
                raise ValueError("Production file paths disagree with the Git diff header")
            current.new_file = current.new_file or old_path is None
            current.deleted_file = current.deleted_file or new_path is None
            git_header_pending = False
            in_hunk = False
            continue
        if current is None:
            continue
        if line == "--- /dev/null" or line.startswith("new file mode "):
            current.new_file = True
        if line == "+++ /dev/null" or line.startswith("deleted file mode "):
            current.deleted_file = True
        match = HUNK_HEADER.match(line)
        if match:
            old_line, new_line = map(int, match.groups())
            # HUNK_HEADER only exposes starting lines; counts distinguish file
            # headers from header-like source lines inside an unfinished hunk.
            counts = re.match(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", line)
            old_remaining, new_remaining = (int(value) if value is not None else 1
                                            for value in counts.groups())
            in_hunk = True
            continue
        if not in_hunk or line.startswith("\\ No newline"):
            continue
        if line.startswith("+"):
            current.new_lines.add(new_line)
            new_line += 1
            new_remaining -= 1
        elif line.startswith("-"):
            current.old_lines.add(old_line)
            old_line += 1
            old_remaining -= 1
        elif line.startswith(" "):
            old_line += 1
            new_line += 1
            old_remaining -= 1
            new_remaining -= 1
        else:
            raise ValueError(f"Unexpected patch line: {line[:80]}")
    if patch.strip() and not changes:
        raise ValueError("Production patch has no supported file headers")
    return changes


def git_blobs(repo_dir: Path, commit: str, paths: list[str]) -> dict[str, bytes]:
    if not paths:
        return {}
    command = ["git", "-c", f"safe.directory={repo_dir.resolve().as_posix()}"]
    requests = "".join(f"{commit}:{safe_path(path)}\n" for path in paths).encode("utf-8")
    # Partial clones otherwise perform one network fetch per missing blob.
    # Disabling promisor remotes for this inspection prevents implicit fetches.
    settings = run_git("config", "--get-regexp", r"^remote\..*\.promisor$", cwd=repo_dir, check=False)
    local_command = list(command)
    for line in settings.stdout.splitlines():
        local_command += ["-c", line.split()[0] + "=false"]
    local_env = dict(os.environ, GIT_NO_LAZY_FETCH="1")
    check = subprocess.run(local_command + ["cat-file", "--batch-check"], cwd=repo_dir,
                           input=requests, capture_output=True, check=True, timeout=30, env=local_env)
    headers = check.stdout.splitlines()
    if len(headers) != len(paths):
        raise ValueError("Incomplete Git object inventory")
    missing = [path for path, header in zip(paths, headers) if header.endswith(b" missing")]
    if missing:
        tree = run_git("ls-tree", "-r", "-z", commit, cwd=repo_dir).stdout
        ids = {entry.split("\t", 1)[1]: entry.split("\t", 1)[0].split()[2]
               for entry in tree.split("\0") if entry}
        try:
            objects = sorted({ids[path] for path in missing})
        except KeyError as exc:
            raise ValueError(f"Path missing at {commit}: {exc}") from exc
        print(f"Fetching {len(objects)} missing historical blobs in one request...", flush=True)
        fetched = subprocess.run(command + ["-c", "fetch.negotiationAlgorithm=noop", "fetch", "--no-tags",
                                            "--no-write-fetch-head", "--recurse-submodules=no",
                                            "--filter=blob:none", "--stdin", "origin"],
                                 cwd=repo_dir, input=("\n".join(objects) + "\n").encode("ascii"),
                                 capture_output=True, timeout=120)
        if fetched.returncode:
            raise RuntimeError("Cannot fetch historical source blobs: " + fetched.stderr.decode("utf-8", errors="replace"))
    result = subprocess.run(
        local_command + ["cat-file", "--batch"], env=local_env,
        cwd=repo_dir, input=requests, timeout=30,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    stream = io.BytesIO(result.stdout)
    blobs = {}
    for path in paths:
        header = stream.readline().rstrip(b"\n").split()
        if len(header) != 3 or header[1] != b"blob":
            raise ValueError(f"Cannot read {commit}:{path}: {b' '.join(header)!r}")
        size = int(header[2])
        value = stream.read(size)
        if len(value) != size or stream.read(1) != b"\n":
            raise ValueError(f"Incomplete Git blob: {path}")
        blobs[path] = value
    return blobs


def decode_source(value: bytes) -> str:
    encoding, _ = tokenize.detect_encoding(io.BytesIO(value).readline)
    return value.decode(encoding)


def collection_settings(configs: dict[str, str]) -> tuple[dict, list[dict]]:
    errors = []
    for path in ("pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"):
        if path not in configs:
            continue
        try:
            if path.endswith(".toml"):
                import tomllib
                settings = tomllib.loads(configs[path]).get("tool", {}).get("pytest", {}).get("ini_options")
            else:
                parser = configparser.ConfigParser(interpolation=None, strict=False, inline_comment_prefixes=("#",))
                parser.read_string(configs[path])
                section = "tool:pytest" if path == "setup.cfg" else "pytest"
                settings = dict(parser[section]) if parser.has_section(section) else None
            if settings is None:
                continue
            def values(key):
                value = settings.get(key, [])
                return shlex.split(value) if isinstance(value, str) else value
            result = {"config_file": path, "ignore_patterns": values("norecursedirs")}
            if "python_files" in settings:
                result["test_patterns"] = values("python_files")
            return result, errors
        except (ValueError, configparser.Error, ImportError) as exc:
            errors.append({"file": path, "kind": "collection_config_error", "message": str(exc)})
    return {}, errors


def base_sources(repo_dir: Path, commit: str) -> tuple[dict[str, str], list[dict], dict]:
    files = run_git("ls-tree", "-r", "--name-only", "-z", commit, cwd=repo_dir).stdout.split("\0")
    paths = [path for path in files if path.endswith(".py") or path in
             {"pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"}]
    sources = {}
    configs = {}
    errors = []
    for path, value in git_blobs(repo_dir, commit, paths).items():
        try:
            source = decode_source(value)
            (sources if path.endswith(".py") else configs)[path] = source
        except (UnicodeError, SyntaxError) as exc:
            errors.append({"file": path, "kind": "decode_error", "message": str(exc)})
    settings, config_errors = collection_settings(configs)
    return sources, errors + config_errors, settings


def apply_production_patch(repo_dir: Path, commit: str, patch: str,
                           changes: list[ProductionChange]) -> dict[str, str | None]:
    """Apply all file hunks together; retain deletion/rename information."""
    if not changes:
        return {}
    old_paths = sorted({change.old_path for change in changes if not change.new_file})
    blobs = git_blobs(repo_dir, commit, old_paths)
    after = {}
    with tempfile.TemporaryDirectory(prefix="oracle-code-") as directory:
        root = Path(directory)
        for path, value in blobs.items():
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(value)
        patch_file = root / "__oracle_production.patch"
        # SWT production diffs omit the final newline. Git needs the diff's
        # last line terminated; preserve all context whitespace and content.
        patch_file.write_text(patch if patch.endswith("\n") else patch + "\n", encoding="utf-8", newline="")
        # Some Verified diffs also trim trailing blank context lines while
        # retaining the original hunk counts. Recount the supplied lines;
        # this changes no added/deleted content and fabricates no source.
        run_git("apply", "--recount", "--whitespace=nowarn", str(patch_file), cwd=root)
        for change in changes:
            if change.path.endswith(".py"):
                after[change.path] = None if change.deleted_file else decode_source((root / change.path).read_bytes())
    return after


def changed_symbols(before: DependencyIndex, after: DependencyIndex,
                    changes: list[ProductionChange]) -> list[dict]:
    """Map each changed line to its narrowest containing definition."""
    pairs: set[tuple[str, str]] = set()
    for change in changes:
        for index, path, lines in ((before, change.old_path, change.old_lines),
                                   (after, change.path, change.new_lines)):
            symbols = [s for s in index.symbols.values() if s.file == path and not s.original]
            for line in lines:
                containing = [s for s in symbols if s.start <= line <= s.end]
                if containing:
                    symbol = min(containing, key=lambda s: (s.end - s.start, -s.start, s.kind == "module"))
                    pairs.add((change.path, symbol.name))
        if change.old_path != change.path and not change.old_lines and not change.new_lines:
            pairs.add((change.path, "<module>"))
    records = []
    for path, name in sorted(pairs):
        change = next(c for c in changes if c.path == path)
        old = before.symbols.get(f"{change.old_path}::{name}")
        new = after.symbols.get(f"{path}::{name}")
        symbol = new or old
        records.append({
            "id": f"{path}::{name}", "file": path, "name": name,
            "kind": symbol.kind,
            "change": "added" if old is None else "deleted" if new is None else "modified",
            "old_span": [old.start, old.end] if old else None,
            "new_span": [new.start, new.end] if new else None,
            "base_targets": [],
        })
        record = records[-1]
        if old:
            record["base_targets"].append({"id": old.id, "reason": "existing_symbol", "confidence": 1.0})
        elif new:
            # Added helpers may be called from an existing function whose body
            # did not otherwise map to an old-line hunk.
            for edge in after.edges.values():
                if edge.target == new.id and edge.kind in {"call", "reference"}:
                    caller = after.symbols[edge.source]
                    base_id = f"{change.old_path if caller.file == path else caller.file}::{caller.name}"
                    if base_id in before.symbols and before.symbols[base_id].kind == "function":
                        record["base_targets"].append({"id": base_id, "reason": "existing_caller", "confidence": 0.8})
            if not record["base_targets"] and new.context:
                base_id = f"{change.old_path}::{after.symbols[new.context].name}"
                if base_id in before.symbols:
                    record["base_targets"].append({"id": base_id, "reason": "class_fallback", "confidence": 0.35})
        record["base_targets"] = list({target["id"]: target for target in record["base_targets"]}.values())
        if not record["base_targets"]:
            record["unresolved_reason"] = "No corresponding base symbol or existing caller"
    return records


def retrieve(index: DependencyIndex, changes: list[dict], max_depth: int = 8,
             top_k: int = 10, fallback: bool = True) -> tuple[list[dict], list[dict]]:
    matches: dict[str, dict[str, dict]] = {}
    for change in changes:
        for target in change["base_targets"]:
            for test, path in index.reverse_paths(target["id"], max_depth).items():
                confidence = min([target["confidence"], *[edge.confidence for edge in path]])
                evidence = {"changed_symbol": change["id"], "base_target": target["id"],
                            "mapping": target["reason"], "confidence": confidence,
                            "distance": len(path), "path": [asdict(edge) for edge in path]}
                previous = matches.setdefault(test, {}).get(change["id"])
                if previous is None or (confidence, -len(path)) > (previous["confidence"], -previous["distance"]):
                    matches[test][change["id"]] = evidence
    if not matches and fallback:
        # Module proximity is explicitly low-confidence and only a last resort.
        target_files = {index.symbols[target["id"]].file: change["id"]
                        for change in changes for target in change["base_targets"]}
        for edge in index.edges.values():
            if index.symbols[edge.target].file not in target_files:
                continue
            for test, path in index.reverse_paths(edge.target, max_depth).items():
                changed = target_files[index.symbols[edge.target].file]
                matches.setdefault(test, {})[changed] = {
                    "changed_symbol": changed, "base_target": edge.target, "mapping": "module_proximity",
                    "confidence": 0.2, "distance": len(path), "path": [asdict(item) for item in path],
                }
    selected = []
    covered = set()
    remaining = set(matches)
    while remaining and (top_k == 0 or len(selected) < top_k):
        def rank(sid):
            evidence = list(matches[sid].values())
            return (-max(item["confidence"] for item in evidence),
                    -len(set(matches[sid]) - covered), -len(evidence),
                    min(item["distance"] for item in evidence), sid)
        chosen = min(remaining, key=rank)
        selected.append(chosen)
        covered.update(matches[chosen])
        remaining.remove(chosen)
    return ([index.tests[sid] for sid in selected],
            [{"file": index.tests[sid]["file"], "name": index.tests[sid]["name"],
              "source": "production_patch", "matched_symbols": sorted(matches[sid]),
              "evidence": list(matches[sid].values())} for sid in selected])


def merge_tests(base: list[dict], retrieved: list[dict], top_k: int) -> list[dict]:
    result = []
    seen = set()
    for item in base + retrieved:
        key = (item["file"], item["name"])
        if key not in seen:
            result.append(item)
            seen.add(key)
        if top_k and len(result) >= top_k:
            break
    return result


def augment_instance(row: dict[str, str], repo_dir: Path, *, max_depth=8, top_k=10,
                     fallback=True) -> tuple[list[dict], list[dict], dict]:
    sources, decode_errors, settings = base_sources(repo_dir, row["base_commit"])
    try:
        known_tests = {change.path for change in parse_patch(row["test_patch"]) if change.path.endswith(".py")}
    except ValueError:
        known_tests = set()
    before = DependencyIndex(sources, test_patterns=settings.get("test_patterns"),
                             ignore_patterns=settings.get("ignore_patterns", ()), extra_test_files=known_tests).build()
    changes = production_changes(row["patch"])
    patched = apply_production_patch(repo_dir, row["base_commit"], row["patch"], changes)
    # Only modified files are needed to map new definitions/callers. Base graph
    # traversal always uses the full immutable snapshot above.
    after_sources = {c.path: patched[c.path] for c in changes if c.path in patched and patched[c.path] is not None}
    after = DependencyIndex(after_sources).build()
    symbols = changed_symbols(before, after, changes)
    retrieved, evidence = retrieve(before, symbols, max_depth, top_k, fallback)
    original_error = None
    try:
        _, base, _ = extract_instance(row, repo_dir)
    except (ValueError, SyntaxError, RuntimeError) as exc:
        base = []
        original_error = str(exc)
    combined = merge_tests(base, retrieved, top_k)
    # Validate the source boundary, including snippets from the old extractor.
    for item in combined:
        sid = f"{item['file']}::{item['name']}"
        if sid not in before.tests or item["code_content"] != before.tests[sid]["code_content"]:
            raise ValueError(f"Selected snippet does not match the base snapshot: {sid}")
    base_keys = {(item["file"], item["name"]) for item in base}
    selected_keys = {(item["file"], item["name"]) for item in combined}
    evidence_map = {(item["file"], item["name"]): item for item in evidence}
    selected = []
    for item in combined:
        key = (item["file"], item["name"])
        entry = dict(evidence_map.get(key, {"file": key[0], "name": key[1], "matched_symbols": [], "evidence": []}))
        entry["sources"] = (["test_patch_base"] if key in base_keys else []) + (["production_patch"] if key in evidence_map else [])
        entry["available_at_base"] = True
        selected.append(entry)
    report = before.report()
    report["collection_config_file"] = settings.get("config_file")
    report["decode_errors"] = decode_errors
    manifest = {"base_commit": row["base_commit"], "selection_uses_gold_patch": True,
                "parameters": {"top_k": top_k, "max_depth": max_depth, "fallback": fallback},
                "changed_symbols": symbols, "selected_tests": selected,
                "production_candidates": evidence,
                "counts": {"original_base": len(base), "production_selected": len(retrieved),
                           "augmented": len(combined), "added": len(selected_keys - base_keys)},
                "analysis": report, "patched_parse_errors": after.diagnostics}
    if original_error:
        manifest["original_oracle_error"] = original_error
    provenance = {key: row[key] for key in ("production_patch_source_dataset", "production_patch_repair",
                                           "original_production_patch_sha256") if row.get(key)}
    if provenance:
        manifest["production_patch_provenance"] = provenance
    if not symbols:
        manifest["retrieval_reason"] = "No parsed Python production symbols changed"
    elif not retrieved:
        manifest["retrieval_reason"] = "No base tests reached the changed symbols within the depth limit"
    return retrieved, combined, manifest


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for attempt in range(30):
        try:
            temp.replace(path)
            break
        except PermissionError:
            # Windows readers/virus scanners can briefly deny delete sharing
            # while an otherwise writable JSON file is open.
            if attempt == 29:
                raise
            time.sleep(0.1)


def main():
    from scripts.code_retrieval.extract_oracle import (
        default_paths as code_default_paths, extract_instance as extract_code_instance, store_instance,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=dataset_name, required=True)
    parser.add_argument("--repo", type=repository_name, required=True)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--repo-dir", type=Path)
    parser.add_argument("--clone-url")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--code-output-dir", type=Path, help="Code oracle directory; defaults to retrieval_results/code/oracle/<dataset>/<repo>")
    parser.add_argument("--instance-id", action="append")
    parser.add_argument("--top-k", type=int, default=10, help="Maximum tests per variant; 0 means all")
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--no-fallback", action="store_true", help="Disable module-proximity fallback")
    args = parser.parse_args()
    if args.top_k < 0 or args.max_depth < 1:
        parser.error("--top-k must be nonnegative and --max-depth must be positive")
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
    output = args.output_dir or default_output
    code_output = args.code_output_dir or code_default_paths(args.dataset, args.repo)[1]
    filenames = ["related_tests_oracle_code_base.json", "related_tests_oracle_base_augmented.json", "oracle_augmented_manifest.json"]
    outputs = [_load(output / filename) for filename in filenames]
    for row in rows:
        # Persist code retrieval independently before full test-graph analysis.
        code_values = extract_code_instance(row, repo_dir)
        store_instance(code_output, row["instance_id"], code_values)
        print(f"{row['instance_id']}: saved {len(code_values[0])} base oracle code objects", flush=True)
        retrieved, combined, manifest = augment_instance(row, repo_dir, max_depth=args.max_depth,
                                                         top_k=args.top_k, fallback=not args.no_fallback)
        manifest["code_oracle_directory"] = str(code_output)
        for destination, value in zip(outputs, (retrieved, combined, manifest)):
            destination[row["instance_id"]] = value
        for filename, value in zip(filenames, outputs):
            _save(output / filename, test_retrieval(value) if filename.startswith("related_tests_") else value)
        counts = manifest["counts"]
        print(f"{row['instance_id']}: base={counts['original_base']}, production={len(retrieved)}, augmented={len(combined)}", flush=True)
    print(f"Wrote augmented oracle context to {output}")


if __name__ == "__main__":
    main()
