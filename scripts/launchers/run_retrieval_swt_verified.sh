#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Edit the model and repository arguments here to select your experiment.
exec "${PYTHON:-python}" -m scripts.run_retrieval \
    --stage all --benchmark swt-verified \
    --model deepseek/deepseek-v4-flash-0731 \
    --repo pylint-dev/pylint --repo pytest-dev/pytest \
    --max-workers "${MAX_WORKERS:-1}" --iterations "${ITERATIONS:-3}" "$@"
