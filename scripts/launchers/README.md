# Pipeline launchers

Run these scripts with Bash from your `icore` environment. They locate the
checkout root automatically, so relative output paths retain their meaning.
The Python modules remain under `scripts/code_retrieval`, `scripts/test_retrieval`,
and `scripts/generator`; this folder contains the shell entry points.

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
experiment; resume checks reject changed model/provider/request settings.

For example, `--output-root experiments/run2` produces
`experiments/run2/<benchmark>/<escaped-model-id>/...`. Pass the base directory,
without adding the benchmark or model yourself. Existing legacy outputs stay
where they are; these launchers do not move or rewrite them.

The `code_retrieval_swt_verified.sh`, `test_retrieval_swt_verified.sh`, and
`run_retrieval_swt_verified.sh` entry points pass SWT Verified,
`deepseek/deepseek-v4-flash-0731`, and both `pylint-dev/pylint` and
`pytest-dev/pytest` directly to the Python runner. Prepare the selected Conda
environments once, then run code retrieval followed by test retrieval:

```bash
bash scripts/launchers/setup_swt_verified.sh
bash scripts/launchers/code_retrieval_swt_verified.sh
bash scripts/launchers/test_retrieval_swt_verified.sh
```

To change experiments, edit the `--model`, `--repo`, and `--benchmark` lines
in these shell scripts. Keep the selections the same for code and test retrieval.
The Python runner contains no fixed model or repository selection. Optional
runtime flags such as `--preflight-only`, `--iterations`, and `--output-root`
can still be appended to the running command; use the same output root for both
stages.

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
bug. Use the separate BRT generation/evaluation workflow for that experiment.

## Existing launchers

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
