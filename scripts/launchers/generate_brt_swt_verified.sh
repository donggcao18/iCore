#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Match the existing retrieval run; two refinements use related_tests_3.json.
MODEL="${MODEL:-deepseek/deepseek-r1-0528}"
REPO="${REPO:-pytest-dev/pytest}"
DATASET_CSV="${DATASET_CSV:-./data/swt-bench-verified/oracle_input_pylint_pytest.csv}"
OUTPUT_ROOT="${OUTPUT_ROOT:-./retrieval_results}"

exec "${PYTHON:-python}" -m scripts.run_brt \
    --stage "${STAGE:-generate}" --benchmark swt-verified \
    --model "$MODEL" --repo "$REPO" --dataset-csv "$DATASET_CSV" \
    --output-root "$OUTPUT_ROOT" --iterations "${ITERATIONS:-3}" \
    --samples "${SAMPLES:-1}" --temperature 0.7 \
    --timeout "${ICORE_LLM_TIMEOUT:-1500}" \
    --test-timeout "${ICORE_TEST_TIMEOUT:-60}" "$@"
