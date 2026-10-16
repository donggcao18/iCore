#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Defaults for the code + test base-oracle experiment. Override with environment
# variables or trailing CLI flags. Credentials and historical environments must
# already be configured; this launcher never stores API keys.
MODEL="${MODEL:-deepseek/deepseek-r1-0528}"
REPO="${REPO:-pylint-dev/pylint}"
REPO_DIR="${REPO_DIR:-${REPO_ROOT_DIR:-./repos}/${REPO##*/}}"
DATASET_CSV="${DATASET_CSV:-./data/swt-bench-verified/oracle_input_pylint_pytest.csv}"
CODE="${CODE-./retrieval_results/oracle/code/swt-bench-verified/${REPO##*/}/code_retrieval_oracle_base.json}"
TESTS="${TESTS:-./retrieval_results/oracle/test/swt-bench-verified/${REPO##*/}/related_tests_oracle_base_augmented.json}"
OUTPUT_DIR="${OUTPUT_DIR:-./retrieval_results/brt_harness/${REPO##*/}_code_test_trial}"
ATTEMPTS="${ATTEMPTS:-4}"
MAX_HOSTS="${MAX_HOSTS:-2}"
TEST_TIMEOUT="${TEST_TIMEOUT:-120}"
REPETITIONS="${REPETITIONS:-2}"
CONTEXT_PROVENANCE="${CONTEXT_PROVENANCE:-oracle-base}"
STAGE="${STAGE:-all}"

options=()
if [[ -n "${CODE:-}" ]]; then options+=(--code "$CODE"); fi
if [[ -n "${REPO_DIR:-}" ]]; then options+=(--repo-dir "$REPO_DIR"); fi
if [[ -n "${BENCHMARK_PYTHON:-}" ]]; then options+=(--python "$BENCHMARK_PYTHON"); fi
exec "${PYTHON:-python}" -m scripts.run_brt_harness \
  --dataset-csv "$DATASET_CSV" \
  --repo "$REPO" --tests "$TESTS" --output-dir "$OUTPUT_DIR" \
  --model "$MODEL" --attempts "$ATTEMPTS" \
  --max-hosts "$MAX_HOSTS" --test-timeout "$TEST_TIMEOUT" \
  --repetitions "$REPETITIONS" --context-provenance "$CONTEXT_PROVENANCE" \
  --stage "$STAGE" \
  "${options[@]}" "$@"
