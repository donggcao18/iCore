#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

MODEL="${MODEL:-openai/gpt-6-luna}"
REPO="${REPO:-scikit-learn/scikit-learn}"
SOURCE_RUN="${SOURCE_RUN:-$PWD/retrieval_results/broad_d8/swt-bench-verified/openai%2Fgpt-6-luna/brt/scikit-learn/scikit-learn/oracle_test_augmented_s1}"
SOURCE_RESULTS="${SOURCE_RESULTS:-$SOURCE_RUN/execution_results.json}"
ORACLE="${ORACLE:-$PWD/retrieval_results/oracle/test/swt-bench-verified/${REPO##*/}/legacy_d8/related_tests_oracle_base_augmented.json}"
OUTPUT_DIR="${OUTPUT_DIR:-$SOURCE_RUN/second_generation}"
STAGE="${STAGE:-all}"
export REPO_ROOT_DIR="${REPO_ROOT_DIR:-$PWD/repos}"
export ICORE_LLM_TIMEOUT="${ICORE_LLM_TIMEOUT:-2500}"
export ICORE_TEST_TIMEOUT="${ICORE_TEST_TIMEOUT:-60}"

args=(--source-results "$SOURCE_RESULTS" --oracle "$ORACLE" --output-dir "$OUTPUT_DIR"
      --repo "$REPO" --model "$MODEL" --stage "$STAGE")
if [[ -n "${DATASET_CSV:-}" ]]; then args+=(--dataset-csv "$DATASET_CSV"); fi
if [[ -n "${SOURCE_TESTS:-}" ]]; then args+=(--source-tests "$SOURCE_TESTS"); fi
exec "${PYTHON:-python}" -u -m scripts.refine_brt "${args[@]}" "$@"
