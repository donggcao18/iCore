#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

MODEL='nvidia/nemotron-3-super-120b-a12b:free'
CODE='./retrieval_results/code/flask_retrieval_results.json'
# One retrieval refinement produces related_tests_2.json; three produce _4.json.
FINAL_TESTS="${1:-./retrieval_results/test/flask_retrieval_results/related_tests_2.json}"
SAMPLES="${SAMPLES:-1}"
EXP="${EXP:-flask_brt_nemotron}"
GEN_DIR="./data/${EXP}/generated_tests"
RESULTS="./results/${EXP}/execution_results.json"
if [[ ! "$SAMPLES" =~ ^[1-9][0-9]*$ ]]; then
    printf 'SAMPLES must be a positive integer.\n' >&2
    exit 1
fi
touch swt.txt
python - "$CODE" "$FINAL_TESTS" <<'PY'
import json, sys
from pathlib import Path
from scripts.utils.swe_util import get_conda_python
instance = 'pallets__flask-5014'
if Path('tdd.txt').read_text().split() != [instance]:
    raise SystemExit('tdd.txt must contain only pallets__flask-5014 for this launcher.')
for path in sys.argv[1:]:
    with open(path) as f:
        value = json.load(f).get(instance)
    if not value:
        raise SystemExit(f'Missing Flask context in {path}. Finish retrieval first.')
print('Target Python:', get_conda_python('setup_pallets_flask__2.3'))
PY
mkdir -p "$GEN_DIR" "$(dirname "$RESULTS")" tmp_data
# Required by the current evaluator, although its skip list is unused.
touch tmp_data/final_gpt_@1_acc.txt

python -m scripts.generator.llm_query \
    --exp_name "$EXP" --query_time "$SAMPLES" \
    --context_code_path "$CODE" --context_test_path "$FINAL_TESTS" \
    --out_dir "$GEN_DIR" \
    --template_file ./data/prompt_templates/prompt_with_code_and_tests.json \
    --model "$MODEL" --temperature 0.7 --save_prompt --tdd

for ((i = 1; i <= SAMPLES; i++)); do
    test -s "$GEN_DIR/pallets__flask-5014_n${i}.txt"
done

# The evaluator resets/cleans REPO_ROOT_DIR/flask, injects each candidate,
# runs it on the buggy revision, then applies the reference fix and runs again.
python -m scripts.libro.postprocess_swe \
    --gen_test_dir "$GEN_DIR" --model "$MODEL" --exp_name "$EXP" \
    --injection_path "$FINAL_TESTS" --result_file "$RESULTS" --tdd

python - "$RESULTS" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    results = json.load(f).get('pallets__flask-5014', {})
if not results:
    raise SystemExit('No evaluation results were produced. Inspect the evaluation logs.')
successes = 0
for name, result in results.items():
    success = isinstance(result, dict) and result.get('success') is True
    successes += success
    print(f'{name}: ' + ('FAIL-TO-PASS' if success else 'NOT REPRODUCED / ERROR'))
print(f'Evaluator reports {successes}/{len(results)} fail-to-pass candidates.')
print(f'Detailed results: {sys.argv[1]}')
PY
