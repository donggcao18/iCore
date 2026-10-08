#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

if [[ ! -f data/swt-bench-verified/test.csv ]]; then
    "${PYTHON:-python}" -m scripts.export_swt_verified
fi

# Keep these repository arguments aligned with the retrieval scripts.
exec "${PYTHON:-python}" -m scripts.env_setup.env_setup \
    --dataset-csv data/swt-bench-verified/test.csv \
    --repo pylint-dev/pylint --repo pytest-dev/pytest "$@"
