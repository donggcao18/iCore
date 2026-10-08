#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Edit the model and repository arguments here to select your experiment.
exec "${PYTHON:-python}" -m scripts.run_retrieval \
    --stage all --benchmark swt-verified \
    --model mistralai/mistral-small-3.2-24b-instruct \
    --repo pylint-dev/pylint --repo pytest-dev/pytest \
    --max-workers "${MAX_WORKERS:-1}" --iterations "${ITERATIONS:-3}" \
    --timeout 180 "$@"
