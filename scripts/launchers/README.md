# Pipeline launchers

Run these scripts with Bash from your `icore` environment. They locate the
checkout root automatically, so relative output paths retain their meaning.
The Python modules remain under `scripts/code_retrieval`, `scripts/test_retrieval`,
and `scripts/generator`; this folder contains the shell entry points.

To run code retrieval, iterative test retrieval, final BRT generation, and
buggy/fixed evaluation for SWT Verified pytest with DeepSeek R1-0528:

```bash
bash scripts/launchers/run_retrieval_brt_swt_verified.sh
```

This launcher selects all 15 `pytest-dev/pytest` instances, uses three test
refinement rounds, generates one final candidate per instance, and allows 1500
seconds per API attempt. Both runners share the same model, repository, input
CSV, iteration count, and output root. Retrieval failures stop the launcher
before final generation. `DATASET_CSV` defaults to the prepared
`data/swt-bench-verified/oracle_input_pylint_pytest.csv` for complete evaluation
fixes; no oracle-context JSON enters this experiment's prompts.

Edit the selections at the top of the launcher or set `MODEL`, `REPO`,
`DATASET_CSV`, `OUTPUT_ROOT`, `ITERATIONS`, `SAMPLES`, `MAX_WORKERS`,
`ICORE_LLM_TIMEOUT`, or `ICORE_TEST_TIMEOUT`. For ten final candidates:

```bash
SAMPLES=10 bash scripts/launchers/run_retrieval_brt_swt_verified.sh
```

With defaults, outputs are under
`retrieval_results/swt-bench-verified/deepseek%2Fdeepseek-r1-0528/`:
`code/pytest-dev/pytest/`, `test/pytest-dev/pytest/`, and
`brt/pytest-dev/pytest/i3_s1/`. Final execution details and aggregate results are
`execution_results.json` and `summary.json` inside that BRT directory.

To resume test retrieval alone with the same DeepSeek R1/pytest selections:

```bash
bash scripts/launchers/run_test_retrieval_swt_verified.sh
# Override the current 1500-second API timeout if desired:
ICORE_LLM_TIMEOUT=500 bash scripts/launchers/run_test_retrieval_swt_verified.sh
```

This uses existing keywords and code retrieval in the same output root, then
performs initial test selection and three refinement rounds. It does not rerun
code retrieval or final BRT generation/evaluation. Completed test selections
and drafts retain their existing resume behavior.

Streamed tool names now preserve nonempty fragments across empty continuation
chunks. Initial retrieval and reranking validate tool calls before saving or
executing them, with up to two retries for malformed responses. On resume,
malformed saved tool calls are backed up as
`<instance_id>.invalid_tool_calls[.N].json`, and the conversation resumes from
before the invalid assistant turn. Keep the saved messages; manual deletion or
`--restart` is unnecessary for this repair. Null production-code matches remain
included; the initial test-selection stage uses the issue and repository tools,
and draft generation omits unavailable production-code snippets.

For plain unsigned reasoning, streamed text fragments from the same block are
joined into one `reasoning_details` entry before saving. The full `reasoning`
string is also retained. Signed, encrypted, and provider-specific reasoning
payloads retain their original structure for API replay. Existing saved messages
are left unchanged; the compact format applies to new responses.

## Retrieval with a chosen benchmark, model, and repositories

The three generic launchers require `--model` and at least one `--repo`.
There is no default model, repository list, or provider. Each selected repository
must exist in the selected benchmark CSV and have a prepared benchmark
checkout and Conda environments.

`--benchmark swt-verified` is the default and selects
`data/swt-bench-verified/test.csv`. Use `--benchmark lite` for
`data/swe-bench-lite/test.csv`. `--dataset` is an alias for `--benchmark`;
the benchmark folder names and full source dataset IDs also work. `--dataset-csv`
overrides the selected benchmark's input CSV without changing its output folder.

Models use their existing `API_KEY` and `BASE_URL` entries in `scripts/config.py`.
Add a new model there once; the runner and shared API wrapper need no edits.
For example, `deepseek/deepseek-v4-flash-0731` uses **`QWEN_API_KEY`** and
`QWEN_BASE_URL` (defaulting to `https://openrouter.ai/api/v1`). Credentials are
read at process startup and are never written to run metadata.

```bash
export QWEN_API_KEY='your-openrouter-key'
export QWEN_BASE_URL='https://openrouter.ai/api/v1'
# Optional: override the server path defined in scripts/config.py.
export REPO_ROOT_DIR='/path/to/disposable/benchmark/repos'
```

Pass the model and repository explicitly. Repeat `--repo` for several repositories:

```bash
bash scripts/launchers/retrieve_code.sh \
    --benchmark swt-verified \
    --model deepseek/deepseek-v4-flash-0731 \
    --repo pylint-dev/pylint --repo pytest-dev/pytest

bash scripts/launchers/retrieve_tests.sh \
    --benchmark swt-verified \
    --model deepseek/deepseek-v4-flash-0731 \
    --repo pylint-dev/pylint --repo pytest-dev/pytest
```

Or run both stages for one repository, using any model configured in `config.py`:

```bash
bash scripts/launchers/run_retrieval_all.sh \
    --benchmark lite --model qwen/qwen3.8-27b:free --repo pytest-dev/pytest
```

Append `--preflight-only` to validate credentials, dependencies, clean clones,
base commits, and required Conda interpreters without API calls. Defaults are one
graph worker, three refinement rounds, and one draft per instance per round.
Set `--iterations 2` or `ITERATIONS=2` for two rounds, and use `--max-workers` or
`MAX_WORKERS` to change graph concurrency. `--output-root` changes the output
base directory; benchmark and model folders are appended automatically.

Provider routing is unchanged unless you explicitly pass `--provider`.
For OpenRouter, a chosen endpoint must support named-function tool choice because
initial retrieval forces `list_root`. For example:

```bash
bash scripts/launchers/run_retrieval_all.sh \
    --model deepseek/deepseek-v4-flash-0731 --repo pylint-dev/pylint \
    --provider deepinfra/fp8 --max-tokens 32768 --timeout 300
```

These optional request settings apply to all LLM stages regardless of the selected
model. `--provider` pins the supplied OpenRouter endpoint, requires supported
parameters, and disables provider fallback. Omitted token limits/timeouts retain
the existing stages' defaults. Reasoning uses the model default, and streamed
reasoning fields are preserved for subsequent tool turns.

Every stage reads the same repository-specific CSV from the selected benchmark.
SWT Verified uses the normalized export of
`eth-sri/SWT-bench_Verified_bm25_27k_zsb`; Lite uses
`SWE-bench/SWE-bench_Lite`. A missing SWT CSV can be exported automatically;
a missing Lite CSV must be supplied with `--dataset-csv`. There is no fallback
between benchmarks and no repository allowlist. Only buggy `base_commit` code
and existing tests enter prompts; developer patches are not retrieval context.

Outputs and prompt caches are separated by benchmark, model, and full repository
identity. The default root wraps the original artifact categories in benchmark
and model folders:

```text
retrieval_results/
├── swt-bench-verified/
│   └── <escaped-model-id>/
│       ├── code/<owner>/<repo>/
│       │   ├── keywords.json
│       │   └── retrieval_results.json
│       ├── test/<owner>/<repo>/
│       │   ├── related_tests_1.json ... related_tests_4.json
│       │   ├── messages/initial/, messages/rerank_1/ ...
│       │   └── test_similarity/1/ ...
│       ├── graphs/<owner>/<repo>/
│       ├── swe_test_cgs/<owner>/<repo>/
│       ├── drafts/<owner>/<repo>/iteration_1/ ... iteration_3/
│       └── selections/<owner>/<repo>/
│           └── selected.csv, selected_ids.txt, run_config.json
└── swe-bench-lite/
    └── <escaped-model-id>/
        └── (same artifact categories and repository folders)
```

Model IDs are URL-escaped for filesystem paths (`/` becomes `%2F`, `:` becomes
`%3A`), so a full model ID occupies one folder. For example, the default model
folder for `deepseek/deepseek-v4-flash-0731` is
`retrieval_results/swt-bench-verified/deepseek%2Fdeepseek-v4-flash-0731/`.
Both code and test launchers derive the same paths from their arguments in
`scripts/run_retrieval.py`. A new benchmark can be registered in its `BENCHMARKS`
mapping without changing artifact paths or stage commands.
Existing stages resume cached outputs. Use a fresh `--output-root` for a new
experiment; resume checks reject changed model, provider, or token limit.
Transport timeouts can change when resuming the same experiment.

Initial test retrieval and reranking validate saved final answers before reusing
them. If a reply lacks a valid list of `[file_path, test_name]` pairs, the stage
asks the same model to format its selection using the existing conversation,
without further tool calls. It makes at most two formatting retries per invocation
and keeps the original reply and retries in the messages JSON. If they still
fail, the stage stops and reports the instance and saved response path; it does
not silently record an empty selection. After syncing a fix, rerun the same
test launcher to retry that instance while retaining completed selections.

For example, `--output-root experiments/run2` produces
`experiments/run2/<benchmark>/<escaped-model-id>/...`. Pass the base directory,
without adding the benchmark or model yourself. Existing legacy outputs stay
where they are; these launchers do not move or rewrite them.

The `code_retrieval_swt_verified.sh`, `test_retrieval_swt_verified.sh`, and
`run_retrieval_swt_verified.sh` entry points pass SWT Verified,
`mistralai/mistral-small-3.2-24b-instruct`, and both `pylint-dev/pylint` and
`pytest-dev/pytest` directly to the Python runner. Prepare the selected Conda
environments once, then run code retrieval followed by test retrieval:

```bash
bash scripts/launchers/setup_swt_verified.sh
bash scripts/launchers/code_retrieval_swt_verified.sh
bash scripts/launchers/test_retrieval_swt_verified.sh
```

The test launcher uses these parameters:

| Parameter | Value | Meaning |
| --- | --- | --- |
| `--stage` | `test` | Build test call trees, retrieve initial tests, generate drafts, compare them, and rerank. |
| `--benchmark` | `swt-verified` | Select `data/swt-bench-verified/test.csv`. |
| `--model` | `mistralai/mistral-small-3.2-24b-instruct` | Model used for initial retrieval, draft generation, and reranking. |
| `--repo` | `pylint-dev/pylint`, `pytest-dev/pytest` | Process all selected instances for these two repositories. |
| `--max-workers` | `1`, or `MAX_WORKERS` | Parallel workers for local graph/call-tree work; it does not set LLM request concurrency. |
| `--iterations` | `3`, or `ITERATIONS` | Draft/similarity/reranking rounds; three rounds produce `related_tests_4.json`. |
| `--timeout` | `180` | Deadline in seconds for each LLM request on the Linux main thread. |
| `--output-root` | `retrieval_results` | Base output folder; benchmark/model/category/repository folders are appended. |
| `--provider` | omitted | Use normal provider routing; optionally supply an OpenRouter endpoint slug. |
| `--max-tokens` | omitted | Leave the generation token limit to the existing model/stage defaults. |
| `--preflight-only` | omitted | Append this flag to validate environment/inputs without running retrieval or calling the model. |

The model uses `QWEN_API_KEY` and `QWEN_BASE_URL` (OpenRouter by default).
Run code retrieval with the same model, provider, token limit, and output root
before test retrieval. The test stage requires the matching `keywords.json` and
`retrieval_results.json`; it does not run code retrieval itself.

An extracted keyword can have a `null` match in `retrieval_results.json` when
the graph contains no matching symbol. An instance whose matches are all `null`
is retained as a zero-code-hit retrieval result. The runner reports a warning
and continues test retrieval; draft prompts then use the issue and selected
tests without production-code snippets. Keep these misses when reporting
retrieval coverage and benchmark results. Missing instance records, malformed
snippets, and `null` keyword-extraction entries in `keywords.json` still stop
the pipeline. Existing no-hit records can be reused without another LLM call.

To use two rounds or a longer timeout, append the options:

```bash
bash scripts/launchers/test_retrieval_swt_verified.sh --iterations 2 --timeout 300
```

For these presets the test selections are saved under
`retrieval_results/swt-bench-verified/mistralai%2Fmistral-small-3.2-24b-instruct/test/<owner>/<repo>/`.

To change experiments, edit the `--model`, `--repo`, and `--benchmark` lines
in these shell scripts. Keep the selections the same for code and test retrieval.
The Python runner contains no fixed model or repository selection. Optional
runtime flags such as `--preflight-only`, `--iterations`, and `--output-root`
can still be appended to the running command; use the same output root for both
stages.

Keyword extraction waits for a complete assistant answer. An HTTP 200 log line
does not mean extraction has finished. Requests now print elapsed-time updates
every 30 seconds and report response IDs, finish reasons, and token usage on
completion. On the Linux server's main thread, `--timeout` enforces an elapsed
deadline per API attempt in addition to the SDK's network timeout; on Windows
or worker threads, only the SDK's I/O timeout applies. Deadline failures are not
automatically retried. Keyword results are saved after each instance, and a
failed extraction stops the stage so subsequent instances do not hide the error.

To resume a slow run with a shorter timeout, stop the previous process with
Ctrl+C, sync the updated code to the server, then run:

```bash
bash scripts/launchers/code_retrieval_swt_verified.sh --timeout 180
```

Saved successful keyword results are reused. This option does not change the
model's reasoning settings or generation token limit.

The generic `retrieve_code.sh`, `retrieve_tests.sh`, and `run_retrieval_all.sh`
launchers still require explicit model and repository arguments.

The setup launcher uses the same SWT Verified CSV and repository selections.
It creates each repository/version environment once, reuses existing Python
environments, and clones missing repositories. It does not reset or clean
existing checkouts and does not call the LLM. Conda is located through
`CONDA_EXE` or `conda` on your PATH. Setup logs are saved under
`retrieval_results/env_setup/<environment-name>/setup.log`; a failed installation
stops the command and is not recorded as complete. The legacy no-argument
`python -m scripts.env_setup.env_setup` command still selects Flask/Verified.
For another CSV or repository selection, edit the setup launcher's arguments
or pass them directly to that Python module.

If retrieval reports local changes in a benchmark checkout, inspect and preserve
them before retrying. For example, on the research server:

```bash
git -C /research/cbim/vast/qt60/any-ssr/utils/iCore/repos/pytest status --short
git -C /research/cbim/vast/qt60/any-ssr/utils/iCore/repos/pytest \
    stash push --include-untracked -m 'before SWT Verified retrieval'
bash scripts/launchers/code_retrieval_swt_verified.sh --preflight-only
```

The stash keeps tracked and untracked changes for later recovery. Retrieval
requires a clean checkout because its stages switch buggy commits.

Retrieval and draft generation do **not** evaluate whether a draft reproduces a
bug. Run final BRT generation and evaluation after completing both retrieval stages:

```bash
bash scripts/launchers/run_brt_swt_verified.sh
```

This launcher passes the model (`mistralai/mistral-small-3.2-24b-instruct`) and
both repositories (`pylint-dev/pylint`, `pytest-dev/pytest`) directly to
`scripts.run_brt`. Edit those argument lines to change the experiment, matching
the code/test retrieval launchers. It requires completed `related_tests_4.json`
by default and generates one final candidate per instance at temperature 0.7.
It uses the exact local SWT Verified rows for generation and evaluation.
Instances with no retrieved production code (a `null` instance, an empty
mapping, or all keyword matches `null`) still run final BRT generation and
evaluation. Their prompts contain the issue and retrieved tests, with the
production-code section omitted. A null keyword alongside a usable code match
is ignored while the usable snippets are included. The BRT selection CSV and
IDs retain all selected instances; `run_config.json` and `summary.json` record
`no_code_instance_ids`, and reproduction rates include these instances.
Rerunning a previous run that excluded null-code instances restores them while
reusing completed candidates/results. Missing or malformed retrieval records
still stop the workflow.
To explicitly omit an instance, pass `--exclude-instance <instance_id>`;
repeat the flag for multiple IDs. The current SWT BRT launcher excludes
`pylint-dev__pylint-7277` at the user's request because its checkout installation
fails during evaluation. Remove that argument line when its environment is
repaired. Exclusions affect only BRT generation/evaluation. Completed outputs
are retained, and `excluded_instance_ids` in the manifest and summary records
the omitted IDs. Reproduction rates use the remaining evaluated instances.
The issue and retrieved buggy code/tests enter the prompt; the reference
production fix is used only when evaluating the fixed revision.
Production fixes may use Git-style `diff --git` headers or plain unified
`---`/`+++` headers. BRT preflight validates their syntax with Git before
generation. Patch application ensures a final newline and recounts hunk line
counts from the diff body, as required by the SWT production fixes. A missing
or malformed fix reports the affected instance ID; patches that do not apply
to the buggy checkout still fail during evaluation.

The evaluator injects each candidate into the first retrieved reference's
file/class (or uses lexical placement for an empty reference selection), then
runs its first injected test on buggy and fixed code. Prefer one focused test
per candidate. A success requires a test failure on buggy code and a passing
test on fixed code. Collection/setup errors, no-tests-collected runs, and
all-skipped runs do not count as successful reproductions. Each test execution
has a 60-second timeout. Evaluation resets and cleans the disposable benchmark
clones and applies the production fix between runs; it uses the environments
prepared by `setup_swt_verified.sh`. Run only one workflow per clone at a time.

Outputs follow the same benchmark/model/repository structure:

```text
retrieval_results/swt-bench-verified/<escaped-model-id>/brt/<owner>/<repo>/i3_s1/
├── generated_tests/<instance_id>_n1.txt
├── prompts/<instance_id>.json
├── selections/selected.csv, selected_ids.txt
├── run_config.json
├── execution_results.json
└── summary.json
```

`execution_results.json` retains the buggy/fixed failure details and a `success`
boolean for each candidate. `summary.json` reports candidate successes and the
number/rate of instances with at least one successful candidate. For multiple
samples, this is the observed fraction reproduced with that sample budget.
Candidates and evaluated candidate results are reused on rerun. Configuration
and context hashes protect prompt/result reuse; use a separate `--output-root`
when changing model settings or context, with matching retrieval outputs there.

| Option | Default | Meaning |
| --- | --- | --- |
| `--samples` / `SAMPLES` | `1` | Final candidates generated per instance. |
| `--iterations` / `ITERATIONS` | `3` | Use `related_tests_(iterations+1).json` from retrieval. |
| `--temperature` | `0.7` | Final candidate sampling temperature. |
| `--timeout` | `180` | LLM request timeout in seconds. |
| `--test-timeout` | `60` | Timeout in seconds for each buggy/fixed test execution. |
| `--stage` | `all` | `all`, `generate`, or `evaluate`; evaluation reuses saved candidates. |
| `--output-root` | `retrieval_results` | Same base folder used for code/test retrieval. |
| `--preflight-only` | omitted | Check context, configuration, dependencies, clones, and environments. |
| `--provider`, `--max-tokens` | omitted | Optional generation provider and token limit. |

Examples:

```bash
bash scripts/launchers/run_brt_swt_verified.sh --preflight-only
bash scripts/launchers/run_brt_swt_verified.sh --samples 10 --test-timeout 120
# Evaluate the same ten saved candidates per instance:
bash scripts/launchers/run_brt_swt_verified.sh --samples 10 --stage evaluate --test-timeout 120
```

For another configured benchmark/model/repository, call the runner directly:

```bash
python -m scripts.run_brt --benchmark lite --model <model-id> --repo <owner/repo>
```

## Existing launchers

For SWT Verified BRT generation with only the issue and augmented base oracle
tests as context, run:

```bash
bash scripts/launchers/run_brt_oracle_test_swt_verified.sh
```

This separate launcher reuses the existing generator, test-only prompt template,
and buggy/fixed evaluator. It passes
`retrieval_results/test/oracle/swt-bench-verified/pytest/related_tests_oracle_base_augmented.json`
as both generation context and evaluation injection reference. No code-retrieval
file is loaded. The model sees the bug report and the oracle test snippets;
production fixes are used only by evaluation.

Both oracle launchers default to `deepseek/deepseek-r1-0528`, using `QWEN_API_KEY`
and `QWEN_BASE_URL` (OpenRouter by default) from `scripts/config.py`.
Each API attempt has a 500-second timeout, overridable with `ICORE_LLM_TIMEOUT`.
The elapsed-time messages every 30 seconds report the same pending request;
they do not send another API call. Temporary API errors can trigger retries.
Both oracle launchers now select all 15 `pytest-dev/pytest` instances with no
default exclusions. The test-only launcher generates one candidate per instance
at temperature 0.7. `pytest-dev__pytest-7236` has an empty augmented oracle-test
list and remains included, using the issue alone in the test-only experiment.
It uses
`data/swt-bench-verified/oracle_input_pylint_pytest.csv`, the prepared input for
these oracle artifacts with complete reference fixes. Copy that CSV to the
server together with the oracle file. Edit the selections at the top of the
launcher or set `MODEL`, `REPO`, `ORACLE`, `DATASET_CSV`, `SAMPLES`, and
`OUTPUT_ROOT` in the environment. Set `EXCLUDE_INSTANCE` to omit a specific ID.
Default oracle paths follow the selected repository's name.

Prompts, candidates, the selection CSV, execution results, and summary are saved
under
`retrieval_results/swt-bench-verified/<escaped-model-id>/brt/pytest-dev/pytest/oracle_test_augmented_s1/`.
They are separate from the retrieved-context experiment and reused on rerun.
Changing input/context hashes rejects cache reuse in the same output folder.
For ten candidates per instance:

```bash
SAMPLES=10 bash scripts/launchers/run_brt_oracle_test_swt_verified.sh
```

For the code-only oracle experiment, run:

```bash
bash scripts/launchers/run_brt_oracle_code_swt_verified.sh
```

Its default `CODE` input is
`retrieval_results/code/oracle/swt-bench-verified/pytest/code_retrieval_oracle_base.json`
inside the checkout.
Generation uses the existing code-only prompt template with the issue and these
base production-code snippets, including their class context. No test-retrieval
JSON, patched code oracle, production fix, or reference test patch enters the
prompt. Missing or empty code context stops the run before generation.

Defaults match the test-only launcher: `deepseek/deepseek-r1-0528`, pytest, one candidate,
temperature 0.7, the prepared oracle CSV, and no excluded instances. The default
stage generates and evaluates candidates. Evaluation uses the existing `libro`
token-similarity strategy to choose a test insertion file without requiring a
test-retrieval JSON; this differs from the test-only launcher's oracle-test
insertion reference. Reference fixes are used only during evaluation.

Outputs, including saved prompts and configuration hashes, are isolated under
`retrieval_results/swt-bench-verified/<escaped-model-id>/brt/pytest-dev/pytest/oracle_code_base_s1/`.
Set `MODEL`, `REPO`, `CODE`, `DATASET_CSV`, `SAMPLES`, `OUTPUT_ROOT`, or
`EXCLUDE_INSTANCE` to override the selections. Use `STAGE=generate` for generation
only, or `STAGE=evaluate` to evaluate previously generated candidates:

```bash
STAGE=generate SAMPLES=10 bash scripts/launchers/run_brt_oracle_code_swt_verified.sh
STAGE=evaluate SAMPLES=10 bash scripts/launchers/run_brt_oracle_code_swt_verified.sh
# To return to Pylint while retaining the earlier environment-related exclusion:
REPO=pylint-dev/pylint EXCLUDE_INSTANCE=pylint-dev__pylint-7277 \
  bash scripts/launchers/run_brt_oracle_code_swt_verified.sh
```

| Launcher | Purpose |
| --- | --- |
| `code_retrieval.sh`, `test_retrieval.sh` | Original replication launchers; existing limitations are documented in PROJECT_GUIDE.md. |
| `code_retrieval_flask.sh` | Single Flask Verified code-retrieval trial. |
| `code_retrieval_lite.sh` | Select one repository from SWE-bench Lite and retrieve code. |
| `code_retrieval_swt_verified_pylint.sh` | Existing SWT Verified Pylint code retrieval. |
| `test_retrieval_flask.sh` | Existing iterative test retrieval for Verified/Lite. |
| `run_brt_flask.sh` | Existing BRT generation/evaluation workflow. |
| `generate_brt_oracle_pylint.sh` | BRT generation with oracle context. |
| `generate_brt_swt_verified_pylint.sh` | SWT Verified Pylint oracle BRT generation. |

All nine existing root launchers have moved here. Their working-directory setup,
references to other launchers, and guide links were updated for the new location.
