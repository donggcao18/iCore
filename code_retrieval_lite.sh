#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# Select every Lite instance for one repository; default to Pylint first.
REPO="${REPO:-pylint-dev/pylint}"
MODEL="${MODEL:-nvidia/nemotron-3-super-120b-a12b:free}"
MAX_WORKERS="${MAX_WORKERS:-1}"
KEYWORDS='./retrieval_results/code/nemo_keywords_lite.json'
GRAPHS='./retrieval_results/graphs'
CODE='./retrieval_results/code/nemo_retrieval_results_lite.json'
SELECTED_IDS="./retrieval_results/code/lite_selected_${REPO//\//__}.txt"

if [[ ! "$REPO" =~ ^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$ ]]; then
    printf 'REPO must be an exact owner/name, for example pylint-dev/pylint.\n' >&2
    exit 1
fi
if [[ ! "$MAX_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
    printf 'MAX_WORKERS must be a positive integer.\n' >&2
    exit 1
fi
# Some Python stages open both lists even when --swt is selected.
touch swt.txt
touch tdd.txt
mkdir -p "$(dirname "$KEYWORDS")" "$GRAPHS"

# Fail before any expensive/API work if IDs are absent from Lite, or a base
# checkout/required commit is missing. Graph generation copies these clones;
# it does not clone them itself.
python - "$REPO" "$SELECTED_IDS" <<'PY'
from pathlib import Path
import shlex
import subprocess
import sys

from datasets import load_dataset
from scripts.config import REPO_ROOT_DIR

repo = sys.argv[1]
selection_path = Path(sys.argv[2])
dataset = load_dataset('SWE-bench/SWE-bench_Lite')['test']
selected = {row['instance_id']: row for row in dataset if row['repo'] == repo}
if not selected:
    raise SystemExit(f'No instances for {repo} in the Lite test split.')

missing = {}
invalid_checkouts = []
missing_commits = []
for row in selected.values():
    repo = row['repo']
    clone = Path(REPO_ROOT_DIR).expanduser() / repo.split('/')[-1]
    if not (clone / '.git').exists():
        if clone.exists():
            invalid_checkouts.append(str(clone))
        else:
            missing[repo] = clone
        continue
    result = subprocess.run(
        ['git', '-C', str(clone), 'cat-file', '-e', row['base_commit'] + '^{commit}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        missing_commits.append(f"{row['instance_id']} ({row['base_commit']}) in {clone}")

if missing:
    print('Clone the required base repositories before running code retrieval:')
    for repo, clone in sorted(missing.items()):
        print('git clone ' + shlex.quote(f'https://github.com/{repo}.git') + ' ' + shlex.quote(str(clone)))
if invalid_checkouts:
    print('These paths exist but are not Git checkouts; inspect them before cloning:')
    for clone in sorted(set(invalid_checkouts)):
        print('  ' + clone)
if missing_commits:
    print('These base commits are not present in the existing clones (fetch full history):')
    for item in missing_commits:
        print('  ' + item)
if missing or invalid_checkouts or missing_commits:
    raise SystemExit(1)

selection_path.write_text('\n'.join(selected) + '\n')
print(f'Selected all {len(selected)} {repo} Lite instances; saved {selection_path}.')
PY

# Pass the selected IDs to each stage without replacing the full swt.txt.
export SWT_IDS_FILE="$SELECTED_IDS"

python -m scripts.code_retrieval.extract_keywords \
    --keywords_path "$KEYWORDS" --model "$MODEL" --swt
python - "$KEYWORDS" "$SELECTED_IDS" <<'PY'
import json
import sys
from pathlib import Path

values = json.loads(Path(sys.argv[1]).read_text())
ids = Path(sys.argv[2]).read_text().split()
failed = [instance for instance in ids if not isinstance(values.get(instance), list) or not values[instance]]
if failed:
    raise SystemExit('Missing or empty keywords for: ' + ', '.join(failed))
PY

python -m scripts.code_retrieval.repo_graph.graph \
    --graph_path "$GRAPHS" --max_workers "$MAX_WORKERS" --swt
python - "$GRAPHS" "$SELECTED_IDS" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
missing = [instance for instance in Path(sys.argv[2]).read_text().split()
           if not (root / f'{instance}_graph.pkl').is_file()]
if missing:
    raise SystemExit('Missing repository graphs for: ' + ', '.join(missing))
PY

python -m scripts.code_retrieval.retrieval \
    --keywords_path "$KEYWORDS" --graph_dir "$GRAPHS" \
    --save_path "$CODE" --swt
python - "$CODE" "$SELECTED_IDS" <<'PY'
import json
import sys
from pathlib import Path

values = json.loads(Path(sys.argv[1]).read_text())
missing = [instance for instance in Path(sys.argv[2]).read_text().split() if instance not in values]
if missing:
    raise SystemExit('Missing production context for: ' + ', '.join(missing))
PY

printf 'Production context saved to %s\n' "$CODE"
