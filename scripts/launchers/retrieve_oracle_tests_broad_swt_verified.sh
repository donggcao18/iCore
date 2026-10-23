#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Retrieval only: run in the pipeline environment after benchmark setup.
PYTHON="${PYTHON:-python}"
REPO="${REPO:-scikit-learn/scikit-learn}"
DATASET_CSV="${DATASET_CSV:-$PWD/data/swt-bench-verified/test.csv}"
REPO_ROOT_DIR="${REPO_ROOT_DIR:-$PWD/repos}"
REPO_DIR="${REPO_DIR:-$REPO_ROOT_DIR/${REPO##*/}}"
MAX_DEPTH="${MAX_DEPTH:-8}"
TOP_K="${TOP_K:-10}"
ORACLE_DIR="${ORACLE_DIR:-$PWD/retrieval_results/oracle/test/swt-bench-verified/${REPO##*/}/legacy_d${MAX_DEPTH}}"
CODE_ORACLE_DIR="${CODE_ORACLE_DIR:-$PWD/retrieval_results/oracle/code/swt-bench-verified/${REPO##*/}/legacy_d${MAX_DEPTH}}"

if [[ ! -f "$DATASET_CSV" ]]; then
    echo "Local dataset not found: $DATASET_CSV. Set DATASET_CSV to an existing normalized SWT Verified CSV." >&2
    exit 2
fi
if [[ ! -e "$REPO_DIR/.git" ]]; then
    echo "Repository checkout not found: $REPO_DIR. Set REPO_ROOT_DIR or REPO_DIR to your prepared checkout." >&2
    exit 2
fi

echo "Broad oracle test retrieval: repo=$REPO max_depth=$MAX_DEPTH top_k=$TOP_K"
echo "Dataset: $DATASET_CSV"
echo "Checkout: $REPO_DIR"
echo "BRT context: $ORACLE_DIR/related_tests_oracle_base_augmented.json"
echo "Selection evidence: $ORACLE_DIR/oracle_augmented_manifest.json"

# Historical policy includes module-proximity fallback and merges base
# counterparts of reference tests. The extractor also saves code oracle data.
exec "$PYTHON" -u -m scripts.test_retrieval.augment_oracle \
    --dataset swt-verified \
    --repo "$REPO" \
    --csv "$DATASET_CSV" \
    --repo-dir "$REPO_DIR" \
    --selection-policy legacy-paths \
    --max-depth "$MAX_DEPTH" \
    --top-k "$TOP_K" \
    --output-dir "$ORACLE_DIR" \
    --code-output-dir "$CODE_ORACLE_DIR" \
    "$@"
