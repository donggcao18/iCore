#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

export DATASET_CSV="${DATASET_CSV:-./data/swt-bench-verified/test.csv}"
export CODE="${CODE:-./retrieval_results/code/swt_verified_pylint_code.json}"
export ORACLE="${ORACLE:-./retrieval_results/test/oracle/swt-bench-verified/pylint/related_tests_oracle_base.json}"
export EXP="${EXP:-swt_verified_pylint_oracle_exact_base}"

if [[ ! -f "$ORACLE" ]]; then
    python -m scripts.test_retrieval.extract_oracle --dataset swt-verified \
        --repo pylint-dev/pylint \
        --csv "$DATASET_CSV" --output-dir "$(dirname "$ORACLE")"
fi

exec bash ./generate_brt_oracle_pylint.sh
