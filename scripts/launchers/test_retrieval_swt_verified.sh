#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Keep model and repository arguments the same as in the code-retrieval script.
exec "${PYTHON:-python}" -m scripts.run_retrieval \
    --stage test --benchmark swt-verified \
    --model deepseek/deepseek-v4-flash-0731 \
    --repo pylint-dev/pylint --repo pytest-dev/pytest \
    --max-workers "${MAX_WORKERS:-1}" --iterations "${ITERATIONS:-3}" "$@"
