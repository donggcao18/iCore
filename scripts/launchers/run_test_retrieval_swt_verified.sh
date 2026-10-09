#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Match the selections and output root of run_retrieval_brt_swt_verified.sh.
MODEL="${MODEL:-deepseek/deepseek-r1-0528}"
REPO="${REPO:-pytest-dev/pytest}"
DATASET_CSV="${DATASET_CSV:-./data/swt-bench-verified/oracle_input_pylint_pytest.csv}"
OUTPUT_ROOT="${OUTPUT_ROOT:-./retrieval_results}"

exec "${PYTHON:-python}" -m scripts.run_retrieval --stage test \
    --benchmark swt-verified --model "$MODEL" --repo "$REPO" \
    --dataset-csv "$DATASET_CSV" --output-root "$OUTPUT_ROOT" \
    --iterations "${ITERATIONS:-3}" --max-workers "${MAX_WORKERS:-1}" \
    --timeout "${ICORE_LLM_TIMEOUT:-1500}" "$@"
