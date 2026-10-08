# README
This is the replication package of paper "iCoRe: An Iterative Correlation-Aware Retriever for Bug Reproduction Test Generation".

It includes the complete pipeline for setting up the environment, retrieving relevant production and test code, and leveraging LLMs to generate bug reproduction tests.

For a source-linked explanation of both retrieval pipelines, their scoring formulas, outputs, and resume behavior, see [the retrieval guide](RETRIEVAL_GUIDE.md).

# How to Run

All shell launchers now live in [`scripts/launchers`](scripts/launchers/README.md).
The generic retrieval launchers accept **any configured model**, explicit
repository selections, and either SWT Verified or SWE-bench Lite. For example,
with `QWEN_API_KEY` set to your OpenRouter key:

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

Outputs default to `retrieval_results/swt-bench-verified/<escaped-model-id>/`,
with the existing `code/`, `test/`, `graphs/`, and `swe_test_cgs/` categories
and repository folders underneath. Use `--benchmark lite` to select Lite and
write to `retrieval_results/swe-bench-lite/<escaped-model-id>/`.
The [launcher guide](scripts/launchers/README.md) covers preflight checks,
repository selection, iterations, output paths, and running both stages together.

For SWT Verified with `mistralai/mistral-small-3.2-24b-instruct` on both Pylint and pytest,
the dedicated launchers provide those defaults:

```bash
bash scripts/launchers/setup_swt_verified.sh
bash scripts/launchers/code_retrieval_swt_verified.sh
bash scripts/launchers/test_retrieval_swt_verified.sh
```

Change experiments by editing the `--model` and `--repo` lines inside those
shell scripts. The Python runner remains generic.
The setup launcher creates the required benchmark Conda environments from the
same CSV; it preserves existing repository changes. Retrieval requires clean
benchmark checkouts.

1. Install Dependencies
Clone the repository and install the required Python packages:
```sh
pip install -r requirements.txt
```

Set API credentials in your shell before running retrieval or generation:

```bash
export OPENAI_API_KEY='your-api-key'
# Optional when using a custom OpenAI-compatible endpoint:
# export OPENAI_BASE_URL='https://your-endpoint/v1'
```

Both GPT-4o model names use `OPENAI_API_KEY` and `OPENAI_BASE_URL`.
For Qwen, set `QWEN_API_KEY` and `QWEN_BASE_URL`; for DeepSeek, set
`DEEPSEEK_API_KEY` and `DEEPSEEK_BASE_URL` to your hosting provider's values.
Variables are read at process startup. `.env` files are not loaded automatically.
Environment setup alone does not require an API key.

Additional models: `--model qwen/qwen3.8-27b:free` and `--model z-ai/glm-5.2:free`.
Qwen uses `QWEN_API_KEY` and `QWEN_BASE_URL`; GLM uses `GLM_API_KEY`
and `GLM_BASE_URL`. For OpenRouter, set both base URLs to
`https://openrouter.ai/api/v1` and use your OpenRouter API key for both models.
The exact `--model` value is sent unchanged for these two models in generation,
initial retrieval, and reranking; no separate model-ID setting is needed.

2. Setup the SWE-bench Environment
Initialize the environment necessary for SWE-bench tasks:
```
python -m scripts.env_setup.env_setup
```

3. Retrieve relevant context
First, retrieve the relevant production code:

```sh
bash scripts/launchers/code_retrieval.sh
```

For `SWE-bench/SWE-bench_Lite`, run
`REPO=pylint-dev/pylint bash scripts/launchers/code_retrieval_lite.sh`. The launcher selects
all Lite instances for that repository and saves their IDs to
`retrieval_results/code/lite_selected_pylint-dev__pylint.txt`. It does not
require you to copy IDs into `swt.txt`. Set `REPO=owner/name` for the next
repository.
It uses `--swt` throughout and writes `nemo_keywords_lite.json` and
`nemo_retrieval_results_lite.json` under `retrieval_results/code/`. It checks
that the selected base checkout and commits exist under `REPO_ROOT_DIR` and
uses `env_setup.clone_repo()` to clone it when missing. The current environment setup
entry point is fixed to Flask in Verified; prepare the matching Lite project
environments separately before running later test retrieval or BRT evaluation.

For the official SWT-bench Verified Pylint subset, use
[`SWT_VERIFIED_PYLINT_GUIDE.md`](SWT_VERIFIED_PYLINT_GUIDE.md). Its local CSV
normalizes the source dataset's test/fix patch columns before oracle extraction.
The same oracle extractor handles either dataset and any selected repository:

```sh
python -m scripts.test_retrieval.extract_oracle --dataset lite --repo pytest-dev/pytest
python -m scripts.export_swt_verified
python -m scripts.test_retrieval.extract_oracle --dataset swt-verified --repo pylint-dev/pylint
```

Change `--repo` for another project. The exporter creates one normalized
`data/swt-bench-verified/test.csv` containing every repository; the extractor
filters that CSV by `--repo`. Lite uses `data/swe-bench-lite/test.csv`.
Oracle output is grouped as `retrieval_results/test/oracle/<dataset>/<repo>/`
(for example, `lite/pylint/` or `swt-bench-verified/pylint/`).

Production-code oracle retrieval observes the normalized developer code `patch`
and selects the affected functions, methods, classes, and variables:

```sh
python -m scripts.code_retrieval.extract_oracle --dataset lite --repo pylint-dev/pylint
python -m scripts.code_retrieval.extract_oracle --dataset lite --repo pytest-dev/pytest
```

It writes `code_retrieval_oracle_base.json`, `code_retrieval_oracle_patched.json`,
and `oracle_code_manifest.json` under `retrieval_results/code/oracle/<dataset>/<repo>/`.
Use the base JSON as `--context_code_path` to supply buggy-revision source.
The patched JSON contains the developer fix and is a separate hindsight variant.
Selected methods also include enclosing class context from the same revision:
full source for small classes, or an outline with setup and directly referenced
helpers for large classes. Context is shared across methods of the same class.
See [ORACLE_CODE_GUIDE.md](ORACLE_CODE_GUIDE.md) for selection rules and usage.

To store both code and augmented existing-test oracle context, run:

```sh
python -m scripts.test_retrieval.augment_oracle --dataset lite --repo pylint-dev/pylint
```

This also writes `related_tests_oracle_code_base.json`,
`related_tests_oracle_base_augmented.json`, and `oracle_augmented_manifest.json`
under the test oracle directory. It analyzes calls/references, helpers, fixtures,
and inherited setup methods. Defaults are ten tests and eight dependency edges;
use `--top-k 0` for all candidates or `--no-fallback` to disable module proximity.
`--code-output-dir` overrides code output separately from `--output-dir` for tests.

After preparing those environments, continue with
`REPO=pylint-dev/pylint DATASET=lite ITERATIONS=3 bash scripts/launchers/test_retrieval_flask.sh`, then
`REPO=pylint-dev/pylint DATASET=lite ITERATIONS=3 bash scripts/launchers/run_brt_flask.sh`. Both launchers use Verified
by default and keep Lite retrieval, drafts, and BRT results in separate paths.

Next, retrieve the relevant test code:

```sh
bash scripts/launchers/test_retrieval.sh
```

4. Generate Bug Reproduction Tests

```bash
python -m scripts.generator.llm_query \
    --exp_name sketch \
    --query_time 10 \
    --context_code_path ./retrieval_results/code/retrieval_results.json \
    --context_test_path ./retrieval_results/test/related_tests_4.json \
    --out_dir ./data/icore/gen_tests_gpt-4o/ \
    --template_file ./data/prompt_templates/prompt_with_code_and_tests.json \
    --model gpt-4o \
    --temperature 0.7 \
    --swt
```

# Repository Organization
The repository is organized as follows:
```
.
├── data/                  # Contains prompt templates for the LLM
├── results/               # Generated bug reproduction tests based on the retrieved context
├── retrieval_results/     # Retrieval results from iCoRe
├── scripts/               # Core code for iCoRe
│   ├── code_retrieval/    # Retrieval for relevant production code.
│   ├── test_retrieval/    # Retrieval for relevant test code.
│   ├── generator/         # The basic BRT generator.
│   ├── libro/             # A Python adaptation of the LIBRO framework.
│   ├── launchers/         # Shell launchers for retrieval and BRT runs.
│   └── env_setup/         # Environment configuration scripts for SWT-bench/TDD-bench.
└── requirements.txt       # Python dependencies
```
