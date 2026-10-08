#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Edit these selections for the oracle-test-only experiment.
MODEL="${MODEL:-deepseek/deepseek-r1-0528}"
REPO="${REPO:-pylint-dev/pylint}"
ORACLE="${ORACLE:-./retrieval_results/test/oracle/swt-bench-verified/pylint/related_tests_oracle_base_augmented.json}"
DATASET_CSV="${DATASET_CSV:-./data/swt-bench-verified/oracle_input_pylint_pytest.csv}"
EXCLUDE_INSTANCE="${EXCLUDE_INSTANCE-pylint-dev__pylint-7277}"
SAMPLES="${SAMPLES:-1}"
OUTPUT_ROOT="${OUTPUT_ROOT:-./retrieval_results}"
export ICORE_LLM_TIMEOUT="${ICORE_LLM_TIMEOUT:-500}"
export ICORE_TEST_TIMEOUT="${ICORE_TEST_TIMEOUT:-60}"

exec "${PYTHON:-python}" - "$MODEL" "$REPO" "$ORACLE" "$DATASET_CSV" \
    "$SAMPLES" "$OUTPUT_ROOT" "$EXCLUDE_INSTANCE" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys

from scripts.config import BASE_URL
from scripts.run_brt import fingerprint, summarize
from scripts.run_retrieval import ROOT, artifact_paths, check_files, preflight, prepare_selection, run_module, select_rows
from scripts.utils.patch_utils import prepare_reference_patch

model, repo, oracle_arg, csv_arg, samples_arg, output_arg, exclude = sys.argv[1:]
samples = int(samples_arg)
if samples < 1:
    raise SystemExit('SAMPLES must be a positive integer.')
oracle, dataset_csv = Path(oracle_arg).resolve(), Path(csv_arg).resolve()
source_rows = select_rows(dataset_csv, repo, 'swt-verified')
excluded = [row['instance_id'] for row in source_rows if row['instance_id'] == exclude]
rows = [row for row in source_rows if row['instance_id'] not in excluded]
if not rows:
    raise SystemExit('No instances remain for this experiment.')
contexts = json.loads(oracle.read_text(encoding='utf-8'))
if not isinstance(contexts, dict):
    raise SystemExit(f'Oracle tests must map instance IDs to test lists: {oracle}')
for row in rows:
    bug_id = row['instance_id']
    tests = contexts.get(bug_id)
    if not isinstance(tests, list) or any(
        not isinstance(test, dict) or not all(isinstance(test.get(key), str) and test[key].strip()
                                             for key in ('file', 'name', 'code_content'))
        for test in tests
    ):
        raise SystemExit(f'Missing or invalid oracle test context for {bug_id}: {oracle}')
    prepare_reference_patch(row.get('patch'), bug_id)

root = artifact_paths(Path(output_arg), 'swt-verified', model, repo).category('brt') / f'oracle_test_augmented_s{samples}'
template = ROOT / 'data/prompt_templates/prompt_with_test.json'
manifest = {
    'context_mode': 'oracle_test_only', 'model': model, 'base_url': BASE_URL.get(model),
    'repo': repo, 'samples': samples, 'temperature': 0.7,
    'provider': os.getenv('ICORE_LLM_PROVIDER'), 'max_tokens': os.getenv('ICORE_LLM_MAX_TOKENS'),
    'source_rows_sha256': hashlib.sha256(json.dumps(source_rows, sort_keys=True).encode()).hexdigest(),
    'oracle_sha256': fingerprint(oracle), 'template_sha256': fingerprint(template),
    'instructions_sha256': fingerprint(template.with_suffix('.txt')),
    'excluded_instance_ids': excluded,
}
manifest_path = root / 'run_config.json'
if manifest_path.exists():
    previous = json.loads(manifest_path.read_text(encoding='utf-8'))
    previous['excluded_instance_ids'] = excluded
    if previous != manifest:
        raise SystemExit('Oracle experiment inputs/settings changed; use a separate OUTPUT_ROOT.')
preflight(rows, model)
root.mkdir(parents=True, exist_ok=True)
manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
selected_csv, env = prepare_selection(root / 'selections', rows)
generated, results = root / 'generated_tests', root / 'execution_results.json'
print(f'Oracle-test-only BRT experiment: {len(rows)} instances, {samples} candidate(s) each; excluded: {excluded}', flush=True)
# No --context_code_path: only the issue and this oracle test file enter prompts.
run_module('scripts.generator.llm_query',
           '--dataset_csv', selected_csv, '--model', model, '--exp_name', 'oracle_test_augmented',
           '--query_time', samples, '--temperature', '0.7', '--context_test_path', oracle,
           '--template_file', template, '--out_dir', generated,
           '--save_prompt', '--prompt_dir', root / 'prompts', env=env)
for sample in range(1, samples + 1):
    check_files(generated, rows, f'_n{sample}.txt')
run_module('scripts.libro.postprocess_swe',
           '--dataset_csv', selected_csv, '--gen_test_dir', generated, '--model', model,
           '--exp_name', 'oracle_test_augmented', '--samples', samples,
           '--injection_path', oracle, '--result_file', results, env=env)
summarize(results, rows, samples, no_code=[row['instance_id'] for row in rows], excluded=excluded)
print(f'Oracle experiment outputs: {root}', flush=True)
PY
