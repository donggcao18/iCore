#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Stage 3: generate BRT candidates from oracle production code and the
# Pylint tests as they existed at each instance's buggy base commit.
MODEL="${MODEL:-nvidia/nemotron-3-super-120b-a12b:free}"
SAMPLES="${SAMPLES:-1}"
DATASET_CSV="${DATASET_CSV:-./data/swe-bench-lite/test.csv}"
CODE="${CODE:-./retrieval_results/code/oracle/lite/pylint/code_retrieval_oracle_base.json}"
ORACLE="${ORACLE:-./retrieval_results/test/oracle/lite/pylint/related_tests_oracle_base.json}"
EXP="${EXP:-oracle_code_and_test_lite_pylint_base}"
OUT_DIR="${OUT_DIR:-./data/${EXP}/generated_tests}"

if [[ ! "$SAMPLES" =~ ^[1-9][0-9]*$ ]]; then
    printf 'SAMPLES must be a positive integer.\n' >&2
    exit 1
fi
# The oracle was extracted from the selected local CSV. Check inputs before
# spending API calls. An empty list is valid when the target test was added
# after the base commit; a missing instance key is not.
python - "$DATASET_CSV" "$CODE" "$ORACLE" <<'PY'
import csv
import json
import sys
from pathlib import Path

csv_path, code_path, oracle_path = map(Path, sys.argv[1:4])
csv.field_size_limit(10_000_000)
for path in (csv_path, code_path, oracle_path):
    if not path.is_file():
        raise SystemExit(f'Missing input: {path}')
with csv_path.open(encoding='utf-8-sig', newline='') as handle:
    ids = [row['instance_id'] for row in csv.DictReader(handle)
           if row['repo'] == 'pylint-dev/pylint']
if not ids or len(ids) != len(set(ids)):
    raise SystemExit('The local CSV needs unique Pylint instance IDs.')
code = json.loads(code_path.read_text(encoding='utf-8'))
oracle = json.loads(oracle_path.read_text(encoding='utf-8'))
missing_code = [instance for instance in ids if instance not in code]
if missing_code:
    raise SystemExit('Missing code-retrieval entries for: ' + ', '.join(missing_code)
                     + f'. Check that this is the matching retrieval file ({code_path}).')
invalid_code = [instance for instance in ids
                if code[instance] is not None and not isinstance(code[instance], dict)]
if invalid_code:
    raise SystemExit('Invalid code-retrieval entries for: ' + ', '.join(invalid_code))
empty_code = [instance for instance in ids
              if not any(isinstance(node, dict)
                         and isinstance(node.get('code_content'), str)
                         and node['code_content'].strip()
                         for node in (code[instance] or {}).values())]
if empty_code:
    print('No usable retrieved code for: ' + ', '.join(empty_code)
          + '; generating from the issue and any available oracle tests.')
invalid_tests = [instance for instance in ids
                 if instance not in oracle or
                 (oracle[instance] is not None and not isinstance(oracle[instance], list))]
if invalid_tests:
    raise SystemExit('Missing or invalid base-oracle entries for: '
                     + ', '.join(invalid_tests))
skipped = 0
empty = 0
for instance in ids:
    usable = 0
    for test in oracle[instance] or []:
        if (not isinstance(test, dict)
                or not all(isinstance(test.get(key), str) and test[key].strip()
                           for key in ('name', 'file', 'code_content'))):
            skipped += 1
            continue
        usable += 1
    empty += usable == 0
if skipped:
    print(f'Skipping {skipped} incomplete oracle entries; those instances may use code-only context.')
print(f'Generating for {len(ids)} Pylint instances; '
      f'{empty} have no usable test at the base commit.')
PY

mkdir -p "$OUT_DIR"
python -m scripts.generator.llm_query \
    --dataset_csv "$DATASET_CSV" --repo pylint-dev/pylint \
    --exp_name "$EXP" --query_time "$SAMPLES" \
    --context_code_path "$CODE" --context_test_path "$ORACLE" \
    --out_dir "$OUT_DIR" \
    --template_file ./data/prompt_templates/prompt_with_code_and_tests.json \
    --model "$MODEL" --temperature 0.7

python - "$DATASET_CSV" "$OUT_DIR" "$SAMPLES" <<'PY'
import csv
import sys
from pathlib import Path

csv_path, out_dir = map(Path, sys.argv[1:3])
csv.field_size_limit(10_000_000)
samples = int(sys.argv[3])
with csv_path.open(encoding='utf-8-sig', newline='') as handle:
    ids = [row['instance_id'] for row in csv.DictReader(handle)
           if row['repo'] == 'pylint-dev/pylint']
missing = [str(out_dir / f'{instance}_n{sample}.txt')
           for instance in ids for sample in range(1, samples + 1)
           if not (out_dir / f'{instance}_n{sample}.txt').is_file()
           or not (out_dir / f'{instance}_n{sample}.txt').stat().st_size]
if missing:
    raise SystemExit('Missing or empty BRT candidates:\n' + '\n'.join(missing))
print(f'Generated {len(ids) * samples} BRT candidates in {out_dir}')
PY
