#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

MODEL="${MODEL:-nvidia/nemotron-3-super-120b-a12b:free}"
DATASET="${DATASET:-verified}"
case "$DATASET" in
    verified)
        IDS='tdd.txt'
        DATASET_FLAG='--tdd'
        CODE="${CODE:-./retrieval_results/code/nemo_retrieval_results.json}"
        TESTS="${TESTS:-./retrieval_results/test/nemo_retrieval_results}"
        EXP_PREFIX='nemo'
        touch swt.txt
        ;;
    lite)
        REPO="${REPO:-pylint-dev/pylint}"
        if [[ ! "$REPO" =~ ^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$ ]]; then
            printf 'REPO must be an exact owner/name, for example pylint-dev/pylint.\n' >&2
            exit 1
        fi
        IDS="./retrieval_results/code/lite_selected_${REPO//\//__}.txt"
        if [[ ! -s "$IDS" ]]; then
            printf 'Missing %s. Run REPO=%s bash code_retrieval_lite.sh first.\n' "$IDS" "$REPO" >&2
            exit 1
        fi
        export SWT_IDS_FILE="$IDS"
        DATASET_FLAG='--swt'
        CODE="${CODE:-./retrieval_results/code/nemo_retrieval_results_lite.json}"
        TESTS="${TESTS:-./retrieval_results/test/nemo_retrieval_results_lite}"
        EXP_PREFIX='nemo_lite'
        touch tdd.txt
        ;;
    *)
        printf 'DATASET must be verified or lite.\n' >&2
        exit 1
        ;;
esac
# Match the number of completed test-retrieval refinements; no retrieval is rerun.
ITERATIONS="${ITERATIONS:-3}"
SAMPLES="${SAMPLES:-1}"
if [[ ! "$ITERATIONS" =~ ^[1-9][0-9]*$ ]]; then
    printf 'ITERATIONS must be a positive integer.\n' >&2
    exit 1
fi
FINAL_TESTS="${1:-$TESTS/related_tests_$((ITERATIONS + 1)).json}"
EXP="${EXP:-${EXP_PREFIX}_brt_i${ITERATIONS}_s${SAMPLES}}"
GEN_DIR="${GEN_DIR:-./data/${EXP}/generated_tests}"
RESULTS="${RESULTS:-./results/${EXP}/execution_results.json}"
if [[ ! "$SAMPLES" =~ ^[1-9][0-9]*$ ]]; then
    printf 'SAMPLES must be a positive integer.\n' >&2
    exit 1
fi
python - "$CODE" "$FINAL_TESTS" "$IDS" "$DATASET" <<'PY'
import json, sys
from pathlib import Path
selection = sys.argv[3]
ids = [line for line in Path(selection).read_text().splitlines() if line] if Path(selection).is_file() else []
if not ids or any(line != line.strip() or len(line.split()) != 1 for line in ids):
    raise SystemExit(f'{selection} must contain one instance ID per line, without surrounding spaces.')
if len(ids) != len(set(ids)):
    raise SystemExit(f'Remove duplicate IDs from {selection}.')
for path in sys.argv[1:3]:
    with open(path) as f:
        values = json.load(f)
    missing = [instance for instance in ids if not values.get(instance)]
    if missing:
        raise SystemExit(f'Missing context in {path}: {", ".join(missing)}. Finish retrieval first.')
print(f'Generating BRTs for {len(ids)} selected {sys.argv[4]} instances.')
PY
printf 'Using final test context: %s\n' "$FINAL_TESTS"
mkdir -p "$GEN_DIR" "$(dirname "$RESULTS")" tmp_data
# Required by the current evaluator, although its skip list is unused.
touch tmp_data/final_gpt_@1_acc.txt

python -m scripts.generator.llm_query \
    --exp_name "$EXP" --query_time "$SAMPLES" \
    --context_code_path "$CODE" --context_test_path "$FINAL_TESTS" \
    --out_dir "$GEN_DIR" \
    --template_file ./data/prompt_templates/prompt_with_code_and_tests.json \
    --model "$MODEL" --temperature 0.7 --save_prompt "$DATASET_FLAG"

python - "$GEN_DIR" "$SAMPLES" "$IDS" <<'PY'
import sys
from pathlib import Path
for instance in Path(sys.argv[3]).read_text().split():
    for sample in range(1, int(sys.argv[2]) + 1):
        path = Path(sys.argv[1]) / f'{instance}_n{sample}.txt'
        if not path.is_file() or not path.stat().st_size:
            raise SystemExit(f'Missing or empty candidate: {path}')
PY

# The evaluator resets/cleans each selected repository, injects each candidate,
# runs it on the buggy revision, then applies the reference fix and runs again.
python -m scripts.libro.postprocess_swe \
    --gen_test_dir "$GEN_DIR" --model "$MODEL" --exp_name "$EXP" \
    --injection_path "$FINAL_TESTS" --result_file "$RESULTS" "$DATASET_FLAG"

python - "$RESULTS" "$SAMPLES" "$IDS" <<'PY'
import json, sys
from pathlib import Path
with open(sys.argv[1]) as f:
    all_results = json.load(f)
total = successes = reproduced = 0
missing = []
for instance in Path(sys.argv[3]).read_text().split():
    results = all_results.get(instance, {})
    instance_successes = 0
    for sample in range(1, int(sys.argv[2]) + 1):
        name = f'{instance}_n{sample}.txt'
        if name not in results:
            missing.append(name)
            continue
        result = results[name]
        success = isinstance(result, dict) and result.get('success') is True
        instance_successes += success
        total += 1
        print(f'{name}: ' + ('FAIL-TO-PASS' if success else 'NOT REPRODUCED / ERROR'))
    successes += instance_successes
    reproduced += instance_successes > 0
print(f'Evaluator reports {successes}/{total} fail-to-pass candidates across {reproduced} reproduced instances.')
print(f'Detailed results: {sys.argv[1]}')
if missing:
    raise SystemExit('Missing evaluation results: ' + ', '.join(missing))
PY
