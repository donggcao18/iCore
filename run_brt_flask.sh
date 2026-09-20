#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

MODEL="${MODEL:-nvidia/nemotron-3-super-120b-a12b:free}"
CODE="${CODE:-./retrieval_results/code/nemo_retrieval_results.json}"
TESTS="${TESTS:-./retrieval_results/test/nemo_retrieval_results}"
# Match the number of completed test-retrieval refinements; no retrieval is rerun.
ITERATIONS="${ITERATIONS:-3}"
SAMPLES="${SAMPLES:-1}"
if [[ ! "$ITERATIONS" =~ ^[1-9][0-9]*$ ]]; then
    printf 'ITERATIONS must be a positive integer.\n' >&2
    exit 1
fi
FINAL_TESTS="${1:-$TESTS/related_tests_$((ITERATIONS + 1)).json}"
EXP="${EXP:-nemo_brt_i${ITERATIONS}_s${SAMPLES}}"
GEN_DIR="${GEN_DIR:-./data/${EXP}/generated_tests}"
RESULTS="${RESULTS:-./results/${EXP}/execution_results.json}"
if [[ ! "$SAMPLES" =~ ^[1-9][0-9]*$ ]]; then
    printf 'SAMPLES must be a positive integer.\n' >&2
    exit 1
fi
touch swt.txt
python - "$CODE" "$FINAL_TESTS" <<'PY'
import json, sys
from pathlib import Path
ids = [line for line in Path('tdd.txt').read_text().splitlines() if line]
if not ids or any(line != line.strip() or len(line.split()) != 1 for line in ids):
    raise SystemExit('tdd.txt must contain one instance ID per line, without surrounding spaces.')
if len(ids) != len(set(ids)):
    raise SystemExit('Remove duplicate IDs from tdd.txt.')
for path in sys.argv[1:]:
    with open(path) as f:
        values = json.load(f)
    missing = [instance for instance in ids if not values.get(instance)]
    if missing:
        raise SystemExit(f'Missing context in {path}: {", ".join(missing)}. Finish retrieval first.')
print(f'Generating BRTs for {len(ids)} selected Verified instances.')
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
    --model "$MODEL" --temperature 0.7 --save_prompt --tdd

python - "$GEN_DIR" "$SAMPLES" <<'PY'
import sys
from pathlib import Path
for instance in Path('tdd.txt').read_text().split():
    for sample in range(1, int(sys.argv[2]) + 1):
        path = Path(sys.argv[1]) / f'{instance}_n{sample}.txt'
        if not path.is_file() or not path.stat().st_size:
            raise SystemExit(f'Missing or empty candidate: {path}')
PY

# The evaluator resets/cleans each selected repository, injects each candidate,
# runs it on the buggy revision, then applies the reference fix and runs again.
python -m scripts.libro.postprocess_swe \
    --gen_test_dir "$GEN_DIR" --model "$MODEL" --exp_name "$EXP" \
    --injection_path "$FINAL_TESTS" --result_file "$RESULTS" --tdd

python - "$RESULTS" "$SAMPLES" <<'PY'
import json, sys
from pathlib import Path
with open(sys.argv[1]) as f:
    all_results = json.load(f)
total = successes = reproduced = 0
missing = []
for instance in Path('tdd.txt').read_text().split():
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
