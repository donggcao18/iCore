# Experimental BRT harness

This opt-in harness uses retrieved tests as execution scaffolds and behavior and
assertion examples. The original `scripts.run_brt` pipeline is unchanged. The
first adapter supports pytest, including unittest classes run through pytest.
Django and SymPy's native runners are explicitly unsupported.

## Run

Use Python 3.10+ for the controller. The benchmark interpreter can be an older Python with the
project's historical dependencies and pytest installed. No LLM/benchmark packages
are required for `--stage prepare`.

```bash
python -m scripts.run_brt_harness \
  --dataset-csv data/swt-bench-verified/oracle_input_pylint_pytest.csv \
  --repo pylint-dev/pylint --repo-dir repos/pylint \
  --tests retrieval_results/oracle/test/swt-bench-verified/pylint/related_tests_oracle_base_augmented.json \
  --context-provenance oracle-base \
  --output-dir retrieval_results/brt_harness/pylint_trial \
  --model deepseek/deepseek-r1-0528 \
  --instance-id pylint-dev__pylint-6386 \
  --stage prepare
```

Inspect `prepared.json`, then rerun the same command with `--stage all`.
For ordinary iCoRe experiments use `related_tests_<iteration+1>.json` and
`--context-provenance retrieved` (the default). `--code` accepts the original
production-retrieval JSON, including explicit null misses. Oracle annotations
are stripped from production context. All test snippets are rehydrated from
`base_commit`; supplied patched test bodies are never shown to the model.
Missing base references are recorded; an empty usable reference set falls back
to lexical selection of existing base tests, visibly marked in the artifacts.
Do not treat this fallback as successful retrieval.

`--python /path/to/historical/env/bin/python` overrides Conda resolution. Otherwise
the existing `setup_<owner>_<repo>__<version>` environments are used. The source
clone only needs Git history: it is never checked out, reset, cleaned, or edited.
Each execution uses a separate temporary export of the recorded base commit.
Exports read exact Git blobs, including files marked `export-ignore`, without
checkout filters or `export-subst` rewriting. Submodules need a separate adapter.
`PYTHONPATH` includes that export's `src`, `lib`, and root directories.

Compiled projects may require `--install-project`. This opt-in runs
`python -m pip install --no-deps --no-build-isolation -e .` in **each** export,
using and updating the selected benchmark environment. Dependencies/build tools
must already exist. Run such experiments sequentially per environment. Exported
sources contain tracked files only; external datasets and build prerequisites
must be available separately. Native Windows projects requiring Bash or native
toolchains still need their appropriate environment.

The Bash launcher `scripts/launchers/run_brt_harness.sh` defaults to DeepSeek R1,
Pylint, both base-oracle retrieval files, four attempts, two hosts, a 120-second
test timeout, and two evaluation runs per version. From the repository root:

```bash
bash "$PWD/scripts/launchers/run_brt_harness.sh"
```

It writes to `retrieval_results/brt_harness/pylint_code_test_trial`. Credentials
and historical environments must already be configured. Every setting can be
overridden through environment variables or trailing CLI flags. For example:

```bash
bash "$PWD/scripts/launchers/run_brt_harness.sh" --stage prepare
bash "$PWD/scripts/launchers/run_brt_harness.sh" --instance-id pylint-dev__pylint-6386
```

Environment variables include `CODE` (an explicit empty value omits code context),
`TESTS`, `REPO`, `REPO_DIR`, `DATASET_CSV`, `MODEL`, `OUTPUT_DIR`, `ATTEMPTS`,
`MAX_HOSTS`, `TEST_TIMEOUT`, `REPETITIONS`, `CONTEXT_PROVENANCE`, `STAGE`, and
`BENCHMARK_PYTHON`. Use `CONTEXT_PROVENANCE=retrieved` with ordinary retrieval.

## Generation and repair

1. Select up to two distinct existing module/class hosts from retrieved tests.
2. Run their original reference tests on the base revision. Prefer passing
   scaffolds; retain assertion-failing references because they may expose the bug.
3. Expand bounded context using fixture, lifecycle, helper, and symbol edges in
   `DependencyIndex`. Imported definitions and aliases remain grounded in base
   source. This dependency expansion does not change strict oracle relevance rules.
4. Request a structured issue contract and one sibling test. Preserve the exact
   class host, fixtures, decorators, helpers, and constants. Reject name collisions
   and extra generated tests; never flatten classes or invent imports.
5. Parse/compile, then execute in a fresh buggy snapshot. The structured probe
   distinguishes collection, setup, call, teardown, skip, and runner failures.
6. Repair syntax/setup, retrieve exact missing names/fixtures (up to three extra
   symbols), or switch hosts after passing/unproductive attempts. The expected
   behavior contract is locked across repairs. Identical patches reuse execution.
7. Freeze a provisional failing candidate. No production fix or fixed-version
   result is available to the generation controller.

The default budget is four model calls, at most two host calibrations, and at
most four candidate executions. API transport retries remain governed by the
existing LLM client. A plausible call failure ends generation early. Routing
uses exception-name heuristics; it does not prove semantic agreement with the
report. Unknown/dynamic dependencies are not assumed resolved. Context omissions
are recorded under the default 24,000-character/40-record cap.

The current policy repairs once locally, then explores another available host;
it can change the trigger after a pass. Normal-input controls are encouraged
inside the generated test when justified by the report. They are not mandatory.
Generated helpers are module-level, with `brt_` names, placed before the selected
class or immediately before a generated module-level test. Single-line/nested
class hosts and hosts without a terminal newline are unsupported and cause a
repair attempt rather than silent rewriting.
Non-UTF8 host encodings also require a separate byte-preserving patch adapter.

## Frozen evaluation

`--stage generate` stops after generation and selection. `--stage evaluate`
requires an existing saved generation. `--stage all` performs both stages.
Evaluation applies the **same saved test patch and selector** to independently
exported buggy and fixed sources. It neither regenerates nor reinfers imports.
The developer's `test_patch` is never applied. The default two runs per version
must agree; change with `--repetitions`. Every parameterized case must execute,
all fixed cases must pass, and at least one buggy case must fail in the call
phase. Skips, xfails, collection/setup errors, differing collected IDs, unstable
outcomes, and failed patch applications do not count as reproduction.

An ordinary test-body failure can still be unrelated to the issue. Treat the
automatic fail-to-pass flag as an execution metric and review semantic relevance.
Gold production fixes are read only by evaluation. Oracle-base **selection** uses
hindsight even though its source is from base; report this separately from normal
retrieval. This harness does not implement a patched-test upper-bound condition.

## Artifacts and resume

Each instance contains `run_config.json`, `prepared.json`, original host runs,
numbered attempt prompts/responses/context/patches/full logs, `generation.json`,
`selected.patch`, and `evaluation.json` with repeated-run reports. The top-level
`summary.json` reports selected-instance reproduction over **all requested
instances**, including those for which no candidate was selected. It is a selected
candidate metric, not best-of-all-attempts success.

Manifests fingerprint settings, source/context inputs, shared dependency code,
and harness implementation. Use a new output directory after changing them.
Saved responses and executions are reused after interruption, so successful
requests are not repeated. An empty API response stops without caching a candidate.
Evaluation separately fingerprints the fix and frozen selection. `--stage prepare`
performs no model calls, environment installation, or test execution.

For ablations, compare faithful insertion, expanded context, repair, and targeted
retrieval using matched model/token/execution budgets. Record host calibration
and repair executions as costs; `generation.json` includes their count. Larger
benchmarks and repeated seeds are needed to establish improvement.

## Validation

```bash
python -m unittest tests.test_brt_harness tests.test_brt_workflow tests.test_strict_oracle -v
```

The new suite uses miniature Git repositories and real pytest subprocesses to
check retrieval of missing definitions, fixture reuse, parameterization, skips,
contract preservation, resume behavior, source-clone isolation, and frozen-patch
fail-to-pass evaluation. Subprocess integration tests require pytest in the test
interpreter; otherwise unittest marks them skipped.
