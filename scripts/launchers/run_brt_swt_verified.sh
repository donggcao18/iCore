#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Match the model, repositories, and completed refinement rounds in retrieval.
exec "${PYTHON:-python}" -m scripts.run_brt \
    --benchmark swt-verified \
    --model mistralai/mistral-small-3.2-24b-instruct \
    --repo pylint-dev/pylint --repo pytest-dev/pytest \
    --iterations "${ITERATIONS:-2}" --samples "${SAMPLES:-1}" \
    --temperature 0.7 --timeout 180 --test-timeout 60 "$@"
