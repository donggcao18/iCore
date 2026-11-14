# Execution traces for Pylint and pytest on SWE-bench Verified

The first experiment compares the oracle's proposed path with the calls actually observed for one exact `FAIL_TO_PASS` selector. The trace records each invocation separately. A later pass can follow values and state from a changed function to the observed failure.

## Inputs

Use the official SWE-bench Verified Parquet at `.cache/verified-test-audit/test.parquet`. It contains 10 Pylint and 19 pytest instances. The prepared SWT-bench CSV contains 21 overlapping cases and preserves SWT test patches. Use it only for an explicitly labeled SWT comparison.

Start with `pylint-dev__pylint-4970` and `pytest-dev__pytest-7324`. The required base commits are present in the local clones. The pytest case exercises parameterized selectors. Before running, check that each selected interpreter supports the historical checkout and imports the project from the exported tree. Pilot execution remains pending.

## Proposed files

| File | Responsibility |
| --- | --- |
| `run.py` | Read the pinned rows, select exact test cases, resolve local Git objects, and orchestrate independent buggy-plus-test and fixed-plus-test snapshots. Save reports and run provenance. |
| `targets.py` | Map old-side changed production spans to enclosing AST symbols on the buggy snapshot. Map insertion-only changes using the corresponding fixed-side symbol and check that it exists on the buggy side. Preserve multiple candidates and report unmappable targets. |
| `trace_plugin.py` | Use pytest phase hooks and `sys.setprofile` to capture project invocations, their caller and call site, phase, and symbol identity. Emit a separate trace per exact selector. |
| `paths.py` | Reconstruct paths from invocation parent IDs, project to production symbols, and compare optional oracle routes with the runtime evidence. |

Keep the code and run artifacts under this experiment directory. Keep temporary exports and large traces in an ignored output directory. Reuse local Git object stores as read-only inputs.

## Run the prototype

From the iCoRe root, use an interpreter with PyArrow for the launcher and supply the historical test interpreter separately:

```powershell
& F:/anaconda3/envs/icore/python.exe research/diagnostic_path/verified_execution/run.py --instance pylint-dev__pylint-4970 --python C:/path/to/pylint-env/python.exe --output research/diagnostic_path/verified_execution/runs/pylint-4970
```

Use a new output directory for each run. By default the runner attempts all listed selectors. Repeat `--selector` to choose exact selectors. Use `--pytest-arg=VALUE` to pass runner flags. The exit status reports whether the fail/pass contrast reproduced. Read `paths.json` separately for observed target hits and anchored routes.

The first implementation captures Python calls in the pytest thread and process. It marks jumps through unrecorded frames and records whether the call limit truncated the trace. Oracle comparison and probes for values and state remain follow-up work. The Pylint pilot reached test startup and encountered an incompatible Astroid installation. The pytest pilot needs missing blobs fetched into its partial clone.

## Reuse existing modules

- Import `snapshot`, `apply_patch`, and `classify` from [iCoRe's BRT execution harness](../../../scripts/brt_harness/execution.py). Reuse its report plugin and check whether `PytestRunner` can load the experiment plugin through an explicit plugin argument. If it cannot, use a small experiment-local launcher with the same report format and timeout handling. Apply patches in disposable exports and verify that the affected files changed as expected.
- Use the [SWE-smith tracer](../swe-smith/trace_call_path.py) as a reference for stack capture and deterministic ordering. Verified's production diff is a fix, so the tracer's injecting-diff postimage mapping needs a separate implementation for Verified. The pilot also needs setup and teardown events and all observed calls.
- Use [DependencyIndex](../../../scripts/test_retrieval/static_dependencies.py) if the pilot needs a static candidate graph or a source lookup for unresolved symbols. Build runtime edges from observed caller and callee events.

## Run contract

1. Create two exports from `base_commit`. Apply the same official `test_patch` to both, and apply the production `patch` only to the fixed export. Record the patch hashes and interpreter version.
2. Run each selected test in a fresh process. Require one collected node ID matching the requested selector, a buggy failure, and a fixed pass. Compare uninstrumented and instrumented outcomes on pilot cases. Record collection errors, setup failures, timeouts, and paths that cross an unobserved native, thread, or subprocess boundary.
3. Record each call with an invocation ID, parent invocation ID, source file and symbol, call site, and phase. Preserve repeated calls. Reconstruct a route using the parent chain from one invocation. For a fixture target, identify the fixture as its observed entry under that selector. Mark paths unavailable when the test or fixture anchor cannot be observed.
4. Export `run.json`, `calls.jsonl`, and `paths.json` per selector. Include all distinct observed routes, target hits, the failing observation, and the shortest observed route as a baseline. If a target executes but its result's connection to the failure is unknown, label the route as observed reachability.
5. On one ambiguous case, compare the buggy and fixed invocations and probe the value or state consumed by the failing assertion. Use the result to decide whether a candidate route supports a diagnostic explanation. Export the production segment separately and check that its starting point can be located from the issue and original buggy source.

The two-case pilot should establish whether the historical runners are usable, whether the same selector fails and passes as expected, and whether the observed routes can be reproduced. Scale to the remaining 27 instances after that check. Report patch application, test execution, and route coverage separately so missing paths remain visible.
