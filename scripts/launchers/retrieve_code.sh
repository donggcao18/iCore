#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

exec "${PYTHON:-python}" -m scripts.run_retrieval \
    --stage code --max-workers "${MAX_WORKERS:-1}" "$@"
