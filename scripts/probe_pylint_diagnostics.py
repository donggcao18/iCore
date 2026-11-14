"""Reproduce bounded diagnostic observations using only buggy source/test inputs.

These AST-extracted-function probes do not execute the complete patched tests,
import Pylint, inspect production fixes, or establish buggy/fixed contrasts.
"""

from __future__ import annotations

import __future__
import argparse
import ast
import configparser
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import multiprocessing
import os
import platform
import posixpath
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data/swt-bench-verified/oracle_input_pylint_pytest.csv"
OUT = ROOT / "diagnostic_path/pylint_verified"


def execute(nodes, namespace, filename):
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(filename), "exec",
                 flags=__future__.annotations.compiler_flag), namespace)


def extract(root, rel, names, namespace):
    path = root / rel
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = []
    for name in names:
        parts = name.split(".")
        body = tree.body
        for part in parts:
            node = next(n for n in body if getattr(n, "name", None) == part)
            body = getattr(node, "body", [])
        selected.append(node)
    execute(selected, namespace, path)


def probe_4970(root):
    path = root / "pylint/checkers/similar.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Import) and any(a.name == "astroid" for a in node.names):
            continue
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(("astroid", "pylint")):
            continue
        if isinstance(node, ast.If) or getattr(node, "name", None) in ("SimilarChecker", "register"):
            continue
        selected.append(node)
    ns = {"__name__": "isolated_similar_probe"}
    execute(selected, ns, path)
    fixtures = [str(root / "tests/input" / name) for name in ("similar1", "similar2")]
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        try:
            ns["Run"](["--duplicates=0", *fixtures])
        except SystemExit as exc:
            code = exc.code
    assert code == 0 and stream.getvalue() != ""
    return {"scope": "Actual Run and standalone Similar pipeline extracted without Pylint integration; original test fixtures; ignore flags false, so excluded astroid hooks are not used",
            "exit_code": code, "stdout": stream.getvalue(), "expected_stdout_from_test_patch": ""}


def probe_6386(root):
    ns = {"Path": Path, "os": os, "sys": sys, "configparser": configparser}
    # tomli is needed only if config parsing raises, but keep exception handling valid.
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    ns["tomllib"] = tomllib
    callbacks = ["_init_hook", "_set_rcfile", "_set_output", "_add_plugins", "_set_verbose_mode", "_enable_all_extensions", "_preprocess_options"]
    extract(root, "pylint/config/utils.py", callbacks, ns)
    tree = ast.parse((root / "pylint/config/utils.py").read_text(encoding="utf-8"))
    option_table = next(n for n in tree.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "PREPROCESSABLE_OPTIONS")
    execute([option_table], ns, root / "pylint/config/utils.py")
    extract(root, "pylint/config/config_file_parser.py", ["_ConfigurationFileParser"], ns)
    config = root / "setup.cfg"
    assert config.exists()
    results = []
    for flag in ("-v", "--verbose"):
        run = SimpleNamespace(verbose=False)
        remaining = ns["_preprocess_options"](run, ["empty.py", flag])
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            ns["_ConfigurationFileParser"](run.verbose, None).parse_config_file(config)
        results.append({"flag": flag, "remaining_args": remaining,
                        "run_verbose": run.verbose, "config_parser_stderr": stream.getvalue()})
    assert results[0]["run_verbose"] is False and results[0]["config_parser_stderr"] == ""
    assert results[1]["run_verbose"] is True and "Using config file" in results[1]["config_parser_stderr"]
    return {"scope": "Extracted preprocessor/callbacks and actual configuration parser, manually connected with their source-defined verbose carrier; no full Run", "comparisons": results}


def probe_6903(root):
    ns = {"Path": lambda p: SimpleNamespace(is_file=lambda: p.endswith("cpu.shares")),
          "open": lambda p, **kw: io.StringIO("2"),
          "os": SimpleNamespace(sched_getaffinity=lambda _: {0, 1}),
          "multiprocessing": multiprocessing}
    extract(root, "pylint/lint/run.py", ["_query_cpu", "_cpu_count"], ns)
    share = ns["_query_cpu"]()
    jobs = ns["_cpu_count"]()
    try:
        with multiprocessing.Pool(jobs):
            pass
    except ValueError as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    assert share == jobs == 0 and error["type"] == "ValueError"
    return {"scope": "Extracted CPU helpers with a synthetic cgroup view and two affinity CPUs, followed by real stdlib Pool; full Run not executed",
            "cgroup_quota_file_exists": False, "cpu_shares": 2, "host_cpus": 2,
            "query_cpu_result": share, "cpu_count_result": jobs, "pool_error": error,
            "test_mock_difference": "Test optionally reads quota=-1 depending on file existence; probe directly selects the shares branch, which is the same branch in either case."}


def probe_7080(root):
    ns = {"os": SimpleNamespace(path=posixpath, walk=lambda _: iter([
        (".", ["ignored_subdirectory"], []),
        ("./ignored_subdirectory", [], ["failing.py"]),
    ]))}
    extract(root, "pylint/lint/expand_modules.py", ["_is_in_ignore_list_re", "_is_ignored_file"], ns)
    # _discover_files is extracted as an unbound function, retaining its original body.
    extract(root, "pylint/lint/pylinter.py", ["PyLinter._discover_files"], ns)
    pattern = re.compile(r"^ignored_subdirectory/.*")
    config = SimpleNamespace(ignore=[], ignore_patterns=[], ignore_paths=[pattern])
    raw = "./ignored_subdirectory/failing.py"
    discovered = list(ns["_discover_files"](SimpleNamespace(config=config), ["."]))
    ignored_raw = ns["_is_ignored_file"](raw, [], [], [pattern])
    ignored_normalized = ns["_is_ignored_file"](posixpath.normpath(raw), [], [], [pattern])
    assert raw in discovered and not ignored_raw and ignored_normalized
    return {"scope": "Extracted discovery/matcher with synthetic POSIX traversal; real re/posixpath; no astroid expansion, lint run, or final exit-code reproduction",
            "raw_path": raw, "normalized_path": posixpath.normpath(raw),
            "discovered_paths": discovered, "ignored_raw": ignored_raw,
            "ignored_after_external_normalization": ignored_normalized,
            "fixture_source": (root / "tests/regrtest_data/directory/ignored_subdirectory/failing.py").read_text()}


def probe_7277(root):
    cwd = "/tmp/pytest-of-root/pytest-0/test_do_not_import_files_from_0"
    fake_sys = SimpleNamespace(path=[])
    fake_os = SimpleNamespace(environ={}, getcwd=lambda: cwd)
    ns = {"sys": fake_sys, "os": fake_os}
    extract(root, "pylint/__init__.py", ["modify_sys_path"], ns)
    results = []
    defaults = ["/usr/local/lib/python39.zip", "/usr/local/lib/python3.9"]
    for first in ("", ".", cwd, "/do_not_remove"):
        initial = [first, *defaults]
        fake_sys.path = list(initial)
        ns["modify_sys_path"]()
        expected = initial if first == "/do_not_remove" else initial[1:]
        results.append({"initial": initial, "actual": fake_sys.path,
                        "expected_from_test": expected, "matches": fake_sys.path == expected})
    assert [r["matches"] for r in results] == [True, True, True, False]
    return {"scope": "Actual extracted function with isolated sys.path/os state; does not mutate this process's real sys.path", "cases": results}


def probe_8898(root):
    ns = {"argparse": argparse, "re": re}
    extract(root, "pylint/utils/utils.py", ["_splitstrip", "_check_csv"], ns)
    ns["pylint_utils"] = SimpleNamespace(_check_csv=ns["_check_csv"])
    extract(root, "pylint/config/argument.py", ["_csv_transformer", "_regex_transformer", "_regexp_csv_transfomer"], ns)
    results = []
    for value, expected in [("foo", ["foo"]), ("foo,bar", ["foo", "bar"]),
                            ("foo, bar", ["foo", "bar"]),
                            ("foo, bar{1,3}", ["foo", "bar{1,3}"])]:
        parser = argparse.ArgumentParser()
        parser.add_argument("--bad-names-rgxs", type=ns["_regexp_csv_transfomer"])
        actual = [p.pattern for p in parser.parse_args([f"--bad-names-rgx={value}"]).bad_names_rgxs]
        results.append({"input": value, "split_tokens": list(ns["_csv_transformer"](value)),
                        "actual_patterns": actual, "expected_patterns": expected, "matches": actual == expected})
    invalid = "(foo{1,}, foo{1,3}})"
    try:
        ns["_regexp_csv_transfomer"](invalid)
    except argparse.ArgumentTypeError as exc:
        error = str(exc)
    assert results[-1]["actual_patterns"] == ["foo", "bar{1", "3}"]
    return {"scope": "Extracted buggy converters plus stdlib argparse with the actual option name and its test-used abbreviation; no full Run", "cases": results,
            "error_case": {"input": invalid, "actual_error": error,
                           "expected_fragment_from_test_patch": "Error in provided regular expression: (foo{1,} beginning at index 0: missing ), unterminated subpattern"}}


PROBES = {"4970": probe_4970, "6386": probe_6386, "6903": probe_6903,
          "7080": probe_7080, "7277": probe_7277, "8898": probe_8898}

PROBE_FILES = {
    "4970": ["pylint/checkers/similar.py", "tests/input/similar1", "tests/input/similar2"],
    "6386": ["pylint/config/utils.py", "pylint/config/config_file_parser.py", "setup.cfg"],
    "6903": ["pylint/lint/run.py"],
    "7080": ["pylint/lint/expand_modules.py", "pylint/lint/pylinter.py", "tests/regrtest_data/directory/ignored_subdirectory/failing.py"],
    "7277": ["pylint/__init__.py"],
    "8898": ["pylint/config/argument.py", "pylint/utils/utils.py"],
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with CSV.open(encoding="utf-8", newline="") as handle:
        # Project the row immediately. Never inspect/print patch, issue, hints, or labels.
        cases = [{k: r[k] for k in ("instance_id", "repo", "base_commit", "test_patch", "source_dataset")}
                 for r in csv.DictReader(handle) if r["repo"] == "pylint-dev/pylint"]
    for case in cases:
        instance = case["instance_id"]
        root = ROOT / "repos" / instance
        prefix = ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root)]
        head = subprocess.check_output([*prefix, "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output([*prefix, "status", "--porcelain"], text=True).strip()
        if head != case["base_commit"] or dirty:
            raise ValueError(f"Expected clean pinned buggy source: {instance}")
        issue = instance.rsplit("-", 1)[1]
        payload = {"instance_id": instance, "base_commit": head,
                   "test_patch_sha256": hashlib.sha256(case["test_patch"].encode()).hexdigest(),
                   "probe_input_file_sha256": {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in PROBE_FILES[issue]},
                   "production_patch_read": False, "full_test_executed": False,
                   "runtime": {"python": sys.version, "platform": platform.platform(),
                               "dependencies_available": {name: importlib.util.find_spec(name) is not None for name in ("pytest", "astroid", "platformdirs", "isort", "mccabe")}},
                   "probe": PROBES[issue](root)}
        (OUT / f"{instance}.probe.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"{instance}: bounded probe completed", flush=True)


if __name__ == "__main__":
    main()
