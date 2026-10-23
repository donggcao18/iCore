#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Run in the Linux/Conda pipeline environment, not a benchmark Python 3.6 env.
PYTHON="${PYTHON:-python}"
export REPO="scikit-learn/scikit-learn"
export MODEL="${MODEL:-openai/gpt-6-luna}"
export REPO_ROOT_DIR="${REPO_ROOT_DIR:-$PWD/repos}"
export DATASET_CSV="${DATASET_CSV:-$PWD/data/swt-bench-verified/test.csv}"
export SAMPLES="${SAMPLES:-1}"
export OUTPUT_ROOT="${OUTPUT_ROOT:-$PWD/retrieval_results}"
export EXCLUDE_INSTANCE="${EXCLUDE_INSTANCE-}"
ORACLE_MODE="${ORACLE_MODE:-base}"
RUN_STAGE="${RUN_STAGE:-all}"
ORACLE_DIR="${ORACLE_DIR:-$PWD/retrieval_results/oracle/test/swt-bench-verified/scikit-learn}"

case "$ORACLE_MODE" in
    patched|base) ;;
    *) echo 'ORACLE_MODE must be patched or base.' >&2; exit 2 ;;
esac
case "$RUN_STAGE" in
    all|setup|run) ;;
    *) echo 'RUN_STAGE must be all, setup, or run.' >&2; exit 2 ;;
esac
# Read existing local data first. Dataset download/patch restoration is a
# separate, explicit preparation step; this launcher never rewrites the CSV.
if [[ ! -f "$DATASET_CSV" ]]; then
    echo "Local dataset not found: $DATASET_CSV. Set DATASET_CSV to an existing CSV in data/." >&2
    exit 2
fi
"$PYTHON" - "$DATASET_CSV" "$REPO" <<'PY'
from collections import Counter
from pathlib import Path
import sys
from scripts.run_retrieval import select_rows

rows = select_rows(Path(sys.argv[1]), sys.argv[2], 'swt-verified')
print(f'Loaded {len(rows)} {sys.argv[2]} instances from {sys.argv[1]}', flush=True)
print(f'Benchmark versions: {dict(Counter(row["version"] for row in rows))}', flush=True)
PY

if [[ "$RUN_STAGE" != run ]]; then
    echo 'Installing/reusing benchmark environments from the local dataset...'
    "$PYTHON" -m scripts.env_setup.env_setup \
        --dataset-csv "$DATASET_CSV" --repo "$REPO" \
        --log-dir "$OUTPUT_ROOT/env_setup"
    echo 'Extracting oracle tests from the reference test patches...'
    "$PYTHON" -m scripts.test_retrieval.extract_oracle \
        --dataset swt-verified --repo "$REPO" --csv "$DATASET_CSV" \
        --repo-dir "$REPO_ROOT_DIR/scikit-learn" --output-dir "$ORACLE_DIR"
fi
if [[ "$RUN_STAGE" == setup ]]; then
    echo "Setup and oracle extraction complete: $DATASET_CSV"
    exit 0
fi

# Base counterparts of the changed reference tests exist at the historical
# commit. Empty entries use the existing LIBRO fallback. No augmented graph
# extraction is required. Use the same insertion reference for both modes.
export INJECTION="${INJECTION:-$ORACLE_DIR/related_tests_oracle_base.json}"
echo "Generating BRTs and evaluating buggy/fixed code (oracle mode: $ORACLE_MODE)..."
if [[ "$ORACLE_MODE" == patched ]]; then
    export ORACLE="${ORACLE:-$ORACLE_DIR/related_tests_oracle_patched.json}"
    exec env PYTHON="$PYTHON" bash scripts/launchers/run_brt_oracle_test_patched_swt_verified.sh
else
    export ORACLE="${ORACLE:-$ORACLE_DIR/related_tests_oracle_base.json}"
    # This older launcher uses ORACLE for both prompt context and insertion.
    if [[ "$INJECTION" != "$ORACLE" ]]; then
        echo 'Base mode requires INJECTION to equal ORACLE; use separate runs for another insertion strategy.' >&2
        exit 2
    fi
    exec env PYTHON="$PYTHON" bash scripts/launchers/run_brt_oracle_test_swt_verified.sh
fi
