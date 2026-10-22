#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Historical broad oracle retrieval: follow dependency paths up to eight edges.
DATASET="${DATASET:-lite}"
REPO="${REPO:-sympy/sympy}"
MAX_DEPTH="${MAX_DEPTH:-8}"
TOP_K="${TOP_K:-10}"

exec "${PYTHON:-python}" -m scripts.test_retrieval.augment_oracle \
    --dataset "$DATASET" \
    --repo "$REPO" \
    --selection-policy legacy-paths \
    --max-depth "$MAX_DEPTH" \
    --top-k "$TOP_K"
