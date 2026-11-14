"""Run the Verified execution-trace experiment from the iCoRe checkout."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory

from paths import observed_paths
from targets import map_targets

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from scripts.brt_harness import execution

BOOTSTRAP = """
import os
import sys
import pytest
import icore_brt_probe
project = __import__(os.environ['VERIFIED_PROJECT'])
modules = [project]
if project.__name__ == 'pytest':
    import _pytest
    modules.append(_pytest)
root = os.path.normcase(os.path.realpath(os.getcwd())) + os.sep
for module in modules:
    origin = os.path.normcase(os.path.realpath(module.__file__))
    if not origin.startswith(root):
        raise RuntimeError('Imported outside snapshot: ' + origin)
print('Interpreter:', sys.version, flush=True)
plugins = [icore_brt_probe]
if os.environ.get('VERIFIED_TRACE'):
    from trace_plugin import Recorder
    plugins.append(Recorder(os.getcwd(), os.environ['VERIFIED_TRACE'],
                            int(os.environ['VERIFIED_LIMIT'])))
raise SystemExit(pytest.main(sys.argv[1:], plugins=plugins))
"""


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def apply_snapshot_patch(tree, patch):
    # Give the disposable export its own Git root so patch paths resolve there.
    if not (tree / ".git").exists():
        execution.git(tree, "init", "--quiet")
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "apply", "--recount",
         "--whitespace=nowarn", "--verbose", "-"],
        cwd=tree, input=patch.encode("utf-8"), capture_output=True, timeout=120,
    )
    diagnostic = result.stderr.decode("utf-8", errors="replace")
    if result.returncode or any(line.startswith("Skipped patch ") for line in diagnostic.splitlines()):
        raise RuntimeError(diagnostic.strip())


def run_test(args, tree, selector, project, destination, trace=None):
    destination.mkdir()
    report_file = destination / "report.json"
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("VERIFIED_TRACE", None)
    env.update(
        PYTHONPATH=os.pathsep.join(map(str, (tree / "src", tree / "lib", tree,
                                            HERE, Path(execution.__file__).parent))),
        PYTHONDONTWRITEBYTECODE="1",
        ICORE_BRT_REPORT=str(report_file),
        VERIFIED_PROJECT=project,
        VERIFIED_LIMIT=str(args.max_calls),
    )
    if trace is not None:
        env["VERIFIED_TRACE"] = str(trace)
    command = [str(args.python), "-c", BOOTSTRAP, "--tb=short", *args.pytest_arg, selector]
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    with (destination / "stdout.txt").open("wb") as stdout, (
        destination / "stderr.txt"
    ).open("wb") as stderr:
        with subprocess.Popen(command, cwd=tree, env=env, stdout=stdout, stderr=stderr,
                              **group) as process:
            try:
                code = process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True)
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.kill()
                return dict(status="timeout", collected=[])
    if not report_file.exists():
        return dict(status="runner_error", collected=[], returncode=code)
    report = json.loads(report_file.read_text(encoding="utf-8"))
    result = execution.classify(report, code, selector)
    if result["collected"] != [selector]:
        result["status"] = "collection_error"
    return result


def find_repo(repos, row):
    for repo in (repos / row["instance_id"], repos / row["repo"].split("/")[-1]):
        if repo.is_dir():
            try:
                execution.git(repo, "cat-file", "-e", row["base_commit"] + "^{commit}")
                return repo
            except RuntimeError:
                continue
    raise ValueError("No local clone contains the required base commit")


def run_case(args, row, output):
    repo = find_repo(args.repos, row)
    selectors = row["FAIL_TO_PASS"]
    if isinstance(selectors, str):
        selectors = json.loads(selectors)
    if args.selector and any(selector not in selectors for selector in args.selector):
        raise ValueError("Each selector must be an exact FAIL_TO_PASS entry")
    project = row["repo"].split("/")[-1]
    accepted = True
    with TemporaryDirectory(dir=output) as temporary:
        with execution.snapshot(repo, row["base_commit"], parent=temporary) as buggy, (
            execution.snapshot(repo, row["base_commit"], parent=temporary)
        ) as fixed:
            for tree in (buggy, fixed):
                apply_snapshot_patch(tree, row["test_patch"])
            apply_snapshot_patch(fixed, row["patch"])
            targets, unresolved = map_targets(buggy, fixed, row["patch"])
            for index, selector in enumerate(args.selector or dict.fromkeys(selectors), 1):
                destination = output / f"test-{index:03}"
                destination.mkdir()
                trace = destination / "calls.jsonl"
                results = {
                    "buggy": run_test(args, buggy, selector, project, destination / "buggy"),
                    "fixed": run_test(args, fixed, selector, project, destination / "fixed"),
                    "traced": run_test(args, buggy, selector, project, destination / "traced", trace),
                }
                if not trace.exists():
                    trace.touch()
                paths = observed_paths(trace, targets)
                valid = [result["status"] for result in results.values()] == [
                    "failed", "passed", "failed"
                ]
                meta = trace.with_suffix(".jsonl.meta")
                write_json(destination / "run.json", dict(
                    instance_id=row["instance_id"], repo=row["repo"], selector=selector,
                    base_commit=row["base_commit"], python=str(args.python),
                    pytest_args=args.pytest_arg, timeout=args.timeout,
                    sha256={key: hashlib.sha256(row[key].encode()).hexdigest()
                            for key in ("patch", "test_patch")},
                    outcomes=results, contrast_reproduced=valid, targets=targets,
                    unresolved_targets=unresolved,
                    trace=json.loads(meta.read_text()) if meta.exists() else None,
                ))
                write_json(destination / "paths.json", dict(
                    **paths, evidence="observed reachability", contrast_reproduced=valid
                ))
                accepted &= valid
                print(f"{selector}: " + ", ".join(
                    f"{key}={result['status']}" for key, result in results.items()
                ) + f", target hits={paths['target_hits']}", flush=True)
    return accepted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / ".cache/verified-test-audit/test.parquet")
    parser.add_argument("--repos", type=Path, default=ROOT / "repos")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--selector", action="append")
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pytest-arg", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--max-calls", type=int, default=100000)
    args = parser.parse_args()
    args.python, args.output = args.python.resolve(), args.output.resolve()
    if not args.python.is_file() or args.timeout <= 0 or args.max_calls <= 0:
        parser.error("Provide an existing benchmark interpreter and positive limits")

    import pyarrow.parquet as parquet

    rows = parquet.read_table(args.dataset, filters=[("instance_id", "=", args.instance)]).to_pylist()
    if len(rows) != 1 or rows[0]["repo"] not in {"pylint-dev/pylint", "pytest-dev/pytest"}:
        parser.error("Expected one Pylint or pytest row in SWE-bench Verified")
    args.output.mkdir(parents=True)
    try:
        return 0 if run_case(args, rows[0], args.output) else 1
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        write_json(args.output / "error.json", {"error": str(exc)})
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
