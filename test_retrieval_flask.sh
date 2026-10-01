#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

MODEL="${MODEL:-nvidia/nemotron-3-super-120b-a12b:free}"
DATASET="${DATASET:-verified}"
case "$DATASET" in
    verified)
        IDS='tdd.txt'
        DATASET_FLAG='--tdd'
        KEYWORDS='./retrieval_results/code/nemo_keywords.json'
        CODE='./retrieval_results/code/nemo_retrieval_results.json'
        TESTS='./retrieval_results/test/nemo_retrieval_results'
        DRAFTS='./data/nemo'
        EXP_PREFIX='flask_qwen'
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
        KEYWORDS='./retrieval_results/code/nemo_keywords_lite.json'
        CODE='./retrieval_results/code/nemo_retrieval_results_lite.json'
        TESTS='./retrieval_results/test/nemo_retrieval_results_lite'
        DRAFTS='./data/nemo_lite'
        EXP_PREFIX='lite_qwen'
        touch tdd.txt
        ;;
    *)
        printf 'DATASET must be verified or lite.\n' >&2
        exit 1
        ;;
esac
TREES='./retrieval_results/swe_test_cgs'
# One refinement for the first trial; use ITERATIONS=3 for the original count.
ITERATIONS="${ITERATIONS:-2}"

if [[ ! "$ITERATIONS" =~ ^[1-9][0-9]*$ ]]; then
    printf 'ITERATIONS must be a positive integer.\n' >&2
    exit 1
fi
python - "$CODE" "$KEYWORDS" "$IDS" "$DATASET" <<'PY'
import json, sys
from pathlib import Path
selection = sys.argv[3]
ids = Path(selection).read_text().splitlines() if Path(selection).is_file() else []
ids = [line for line in ids if line]
if not ids:
    raise SystemExit(f'{selection} must contain at least one instance ID, one per line.')
if any(line != line.strip() or len(line.split()) != 1 for line in ids):
    raise SystemExit(f'Use one instance ID per line in {selection}, without surrounding spaces.')
if len(ids) != len(set(ids)):
    raise SystemExit(f'Remove duplicate instance IDs from {selection}.')
for path in sys.argv[1:3]:
    with open(path) as f:
        values = json.load(f)
    missing = [instance for instance in ids if not values.get(instance)]
    if missing:
        raise SystemExit(f'Missing context in {path}: {", ".join(missing)}. Retrieve production code and keywords for these IDs first.')
print(f'Selected {len(ids)} {sys.argv[4]} instances from {selection}.')
PY
mkdir -p "$TREES" "$TESTS" "$DRAFTS"

# Validate outputs for every selected instance, including IDs skipped by a stage.
check_instance_files() {
    local root="$1" suffix="$2" instance
    while IFS= read -r instance || [[ -n "$instance" ]]; do
        instance="${instance%$'\r'}"
        [[ -z "$instance" ]] && continue
        if [[ ! -s "$root/$instance$suffix" ]]; then
            printf 'Missing or empty output: %s/%s%s\n' "$root" "$instance" "$suffix" >&2
            return 1
        fi
    done < "$IDS"
}

python -m scripts.test_retrieval.similarities.get_all_cg_parallel \
    --output_dir "$TREES" --proj "" --max_workers 1 "$DATASET_FLAG"
check_instance_files "$TREES" '/call_trees.db'
check_instance_files "$TREES" '/df.json'

python -m scripts.test_retrieval.initial_retrieval \
    --related_tests_path "$TESTS/related_tests_1.json" \
    --message_path "$TESTS/messages/initial" --model "$MODEL" "$DATASET_FLAG"

for ((i = 1; i <= ITERATIONS; i++)); do
    next=$((i + 1))
    python -m scripts.generator.llm_query \
        --exp_name "${EXP_PREFIX}_${i}" --query_time 1 \
        --context_code_path "$CODE" \
        --context_test_path "$TESTS/related_tests_${i}.json" \
        --out_dir "$DRAFTS/iteration_${i}" \
        --template_file ./data/prompt_templates/prompt_with_code_and_tests.json \
        --model "$MODEL" --temperature 0.0 "$DATASET_FLAG"
    check_instance_files "$DRAFTS/iteration_${i}" '_n1.txt'

    python -m scripts.test_retrieval.retrieve_test \
        --gen_test_dir "$DRAFTS/iteration_${i}" \
        --output_dir "$TESTS/similarity/${i}" \
        --injection_path "$TESTS/related_tests_${i}.json" \
        --tree_path "$TREES" --keywords_path "$KEYWORDS" "$DATASET_FLAG"

    python -m scripts.test_retrieval.rerank \
        --output_related_tests_path "$TESTS/related_tests_${next}.json" \
        --message_dir "$TESTS/messages/rerank_${i}" \
        --test_similarity_dir "$TESTS/similarity/${i}" \
        --last_related_tests_path "$TESTS/related_tests_${i}.json" \
        --model "$MODEL" "$DATASET_FLAG"
done

printf 'Refined test context saved to %s/related_tests_%s.json\n' "$TESTS" "$((ITERATIONS + 1))"
