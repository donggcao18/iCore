#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# Run from the icore Conda environment, with QWEN_* credentials exported.
MODEL='qwen/qwen3.8-27b:free'
KEYWORDS='./retrieval_results/code/flask_keywords.json'
GRAPHS='./retrieval_results/graphs'
CODE='./retrieval_results/code/flask_retrieval_results.json'

# Preserve existing selections; refuse to process anything except this trial.
if [[ ! -f tdd.txt ]]; then
    printf '%s\n' 'pallets__flask-5014' > tdd.txt
fi
touch swt.txt
python - <<'PY'
from pathlib import Path
ids = Path('tdd.txt').read_text().split()
if ids != ['pallets__flask-5014']:
    raise SystemExit('For this launcher, tdd.txt must contain only pallets__flask-5014.')
PY
mkdir -p "$(dirname "$KEYWORDS")" "$GRAPHS"

# Check the API-dependent stage first, before building the repository graph.
python -m scripts.code_retrieval.extract_keywords \
    --keywords_path "$KEYWORDS" --model "$MODEL" --tdd
python - "$KEYWORDS" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    keywords = json.load(f).get('pallets__flask-5014')
if not isinstance(keywords, list) or not keywords:
    raise SystemExit('Flask keyword extraction failed or returned no keywords. Check the logs and retry.')
PY

python -m scripts.code_retrieval.repo_graph.graph \
    --graph_path "$GRAPHS" --max_workers 1 --tdd

python -m scripts.code_retrieval.retrieval \
    --keywords_path "$KEYWORDS" --graph_dir "$GRAPHS" \
    --save_path "$CODE" --tdd

printf 'Production context saved to %s\n' "$CODE"
