#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Edit these selections, or override them through environment variables.
MODEL="${MODEL:-deepseek/deepseek-r1-0528}"
REPO="${REPO:-pytest-dev/pytest}"
DATASET_CSV="${DATASET_CSV:-./data/swt-bench-verified/oracle_input_pylint_pytest.csv}"
OUTPUT_ROOT="${OUTPUT_ROOT:-./retrieval_results}"
ITERATIONS="${ITERATIONS:-3}"
SAMPLES="${SAMPLES:-1}"
TIMEOUT="${ICORE_LLM_TIMEOUT:-1500}"

# This CSV supplies instance metadata and complete fixes for evaluation.
# Model prompts use retrieved code/tests; oracle-context JSON is not loaded.
common=(--benchmark swt-verified --model "$MODEL" --repo "$REPO"
        --dataset-csv "$DATASET_CSV" --output-root "$OUTPUT_ROOT"
        --iterations "$ITERATIONS" --timeout "$TIMEOUT")

"${PYTHON:-python}" -m scripts.run_retrieval --stage all "${common[@]}" \
    --max-workers "${MAX_WORKERS:-1}"

exec "${PYTHON:-python}" -m scripts.run_brt --stage all "${common[@]}" \
    --samples "$SAMPLES" --temperature 0.7 \
    --test-timeout "${ICORE_TEST_TIMEOUT:-60}"
