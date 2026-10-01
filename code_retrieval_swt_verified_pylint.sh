#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# Retrieve production code for the Pylint subset of official SWT-bench Verified.
MODEL="${MODEL:-nvidia/nemotron-3-super-120b-a12b:free}"
MAX_WORKERS="${MAX_WORKERS:-1}"
DATASET_CSV="${DATASET_CSV:-./data/swt-bench-verified/test.csv}"
KEYWORDS='./retrieval_results/code/swt_verified_pylint_keywords.json'
GRAPHS='./retrieval_results/graphs/swt_verified_pylint'
CODE='./retrieval_results/code/swt_verified_pylint_code.json'
SELECTED_IDS='./retrieval_results/code/swt_verified_pylint_ids.txt'

if [[ ! "$MAX_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
    printf 'MAX_WORKERS must be a positive integer.\n' >&2
    exit 1
fi
if [[ ! -f "$DATASET_CSV" ]]; then
    python -m scripts.export_swt_verified --output "$DATASET_CSV"
fi
touch swt.txt tdd.txt
mkdir -p "$(dirname "$KEYWORDS")" "$GRAPHS"

# The local CSV contains all repositories. Select Pylint here; the source
# dataset's patch columns are normalized by scripts.export_swt_verified.
python - "$DATASET_CSV" "$SELECTED_IDS" <<'PY'
import csv
import subprocess
import sys
from pathlib import Path

from scripts.config import REPO_ROOT_DIR
from scripts.env_setup.env_setup import clone_repo
from scripts.export_swt_verified import DATASET
from scripts.utils.swe_util import get_conda_python, get_env_name

csv_path, selection_path = map(Path, sys.argv[1:3])
csv.field_size_limit(10_000_000)
with csv_path.open(encoding='utf-8-sig', newline='') as handle:
    rows = [row for row in csv.DictReader(handle)
            if row['repo'] == 'pylint-dev/pylint']
if not rows or len({row['instance_id'] for row in rows}) != len(rows):
    raise SystemExit('Expected unique SWT Verified Pylint instances in the shared CSV.')
if any(row.get('source_dataset') != DATASET
       or not row['test_patch'].startswith('diff --git ') for row in rows):
    raise SystemExit('CSV must be the normalized export from ' + DATASET)

missing_envs = []
for name in sorted({get_env_name(row) for row in rows}):
    try:
        get_conda_python(name)
    except (OSError, RuntimeError) as exc:
        missing_envs.append(f'{name}: {exc}')
if missing_envs:
    raise SystemExit('Set up these Conda environments before code retrieval:\n'
                     + '\n'.join(missing_envs))

clone = Path(REPO_ROOT_DIR).expanduser() / 'pylint'
if not clone.exists():
    clone_repo('pylint-dev/pylint', REPO_ROOT_DIR, '')
if not (clone / '.git').exists():
    raise SystemExit(f'{clone} is not a Git checkout.')
missing = [row['instance_id'] for row in rows if subprocess.run(
    ['git', '-C', str(clone), 'cat-file', '-e', row['base_commit'] + '^{commit}'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
).returncode]
if missing:
    raise SystemExit('Missing base commits in ' + str(clone) + ': ' + ', '.join(missing))
selection_path.write_text('\n'.join(row['instance_id'] for row in rows) + '\n')
print(f'Selected {len(rows)} SWT Verified Pylint instances: {selection_path}')
PY

export SWT_IDS_FILE="$SELECTED_IDS"
python -m scripts.code_retrieval.extract_keywords \
    --dataset_csv "$DATASET_CSV" --keywords_path "$KEYWORDS" --model "$MODEL" --swt
python - "$KEYWORDS" "$SELECTED_IDS" <<'PY'
import json
import sys
from pathlib import Path
values = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
ids = Path(sys.argv[2]).read_text(encoding='utf-8').split()
missing = [instance for instance in ids
           if not isinstance(values.get(instance), list) or not values[instance]]
if missing:
    raise SystemExit('Missing or empty keywords for: ' + ', '.join(missing))
PY

python -m scripts.code_retrieval.repo_graph.graph \
    --dataset_csv "$DATASET_CSV" --graph_path "$GRAPHS" \
    --max_workers "$MAX_WORKERS" --swt
python - "$GRAPHS" "$SELECTED_IDS" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
missing = [instance for instance in Path(sys.argv[2]).read_text(encoding='utf-8').split()
           if not (root / f'{instance}_graph.pkl').is_file()]
if missing:
    raise SystemExit('Missing repository graphs for: ' + ', '.join(missing))
PY

python -m scripts.code_retrieval.retrieval \
    --dataset_csv "$DATASET_CSV" --keywords_path "$KEYWORDS" \
    --graph_dir "$GRAPHS" --save_path "$CODE" --swt
python - "$CODE" "$SELECTED_IDS" <<'PY'
import json
import sys
from pathlib import Path
values = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
ids = Path(sys.argv[2]).read_text(encoding='utf-8').split()
missing = [instance for instance in ids
           if not isinstance(values.get(instance), dict)
           or not any(isinstance(node, dict) and node.get('code_content')
                      for node in values[instance].values())]
if missing:
    raise SystemExit('Missing production context for: ' + ', '.join(missing))
PY

printf 'Production context saved to %s\n' "$CODE"
