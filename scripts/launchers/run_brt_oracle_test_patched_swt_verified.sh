#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Patched tests are prompt context; evaluation still uses buggy/fixed production code.
MODEL="${MODEL:-deepseek/deepseek-r1-0528}"
REPO="${REPO:-pytest-dev/pytest}"
ORACLE="${ORACLE:-./retrieval_results/oracle/test/swt-bench-verified/${REPO##*/}/related_tests_oracle_patched.json}"
# A newly added reference test/class may not exist in the base checkout.
INJECTION="${INJECTION:-./retrieval_results/oracle/test/swt-bench-verified/${REPO##*/}/related_tests_oracle_base_augmented.json}"
DATASET_CSV="${DATASET_CSV:-./data/swt-bench-verified/oracle_input_pylint_pytest.csv}"
EXCLUDE_INSTANCE="${EXCLUDE_INSTANCE-}"
SAMPLES="${SAMPLES:-1}"
OUTPUT_ROOT="${OUTPUT_ROOT:-./retrieval_results}"
STAGE="${STAGE:-all}"
export ICORE_LLM_TIMEOUT="${ICORE_LLM_TIMEOUT:-2500}"
export ICORE_TEST_TIMEOUT="${ICORE_TEST_TIMEOUT:-60}"

exec "${PYTHON:-python}" - "$MODEL" "$REPO" "$ORACLE" "$DATASET_CSV" \
    "$SAMPLES" "$OUTPUT_ROOT" "$EXCLUDE_INSTANCE" "$STAGE" "$INJECTION" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys

from scripts.config import BASE_URL
from scripts.run_brt import fingerprint, summarize
from scripts.run_retrieval import ROOT, artifact_paths, check_files, preflight, prepare_selection, run_module, select_rows
from scripts.utils.patch_utils import prepare_reference_patch

model, repo, oracle_arg, csv_arg, samples_arg, output_arg, exclude, stage, injection_arg = sys.argv[1:]
try:
    samples = int(samples_arg)
except ValueError:
    raise SystemExit('SAMPLES must be a positive integer.')
if samples < 1:
    raise SystemExit('SAMPLES must be a positive integer.')
if stage not in ('all', 'generate', 'evaluate'):
    raise SystemExit('STAGE must be all, generate, or evaluate.')

oracle, dataset_csv = Path(oracle_arg).resolve(), Path(csv_arg).resolve()
source_rows = select_rows(dataset_csv, repo, 'swt-verified')
if exclude and exclude not in {row['instance_id'] for row in source_rows}:
    raise SystemExit(f'EXCLUDE_INSTANCE is not selected for {repo}: {exclude}')
excluded = [row['instance_id'] for row in source_rows if row['instance_id'] == exclude]
rows = [row for row in source_rows if row['instance_id'] not in excluded]
if not rows:
    raise SystemExit('No instances remain for this experiment.')


def read_tests(path, label):
    blob = path.read_bytes()
    contexts = json.loads(blob)
    if not isinstance(contexts, dict):
        raise SystemExit(f'{label} must map instance IDs to test lists: {path}')
    for row in rows:
        tests = contexts.get(row['instance_id'])
        if not isinstance(tests, list) or any(
            not isinstance(test, dict) or not all(
                isinstance(test.get(key), str) and test[key].strip()
                for key in ('file', 'name', 'code_content')
            ) for test in tests
        ):
            raise SystemExit(f'Missing or invalid {label} for {row["instance_id"]}: {path}')
    return blob


oracle_blob = read_tests(oracle, 'patched oracle test context')
injection_source = None if injection_arg == 'libro' else Path(injection_arg).resolve()
if injection_source == oracle:
    raise SystemExit('Use a base-test INJECTION file or libro; patched tests are generation context only.')
injection_blob = read_tests(injection_source, 'base injection context') if injection_source else None
if stage != 'generate':
    for row in rows:
        prepare_reference_patch(row.get('patch'), row['instance_id'])

root = artifact_paths(Path(output_arg), 'swt-verified', model, repo).category('brt') / f'oracle_test_patched_s{samples}'
template = ROOT / 'data/prompt_templates/prompt_with_test.json'
manifest = {
    'context_mode': 'oracle_test_only', 'oracle_revision': 'patched',
    'model': model, 'base_url': BASE_URL.get(model), 'repo': repo,
    'samples': samples, 'temperature': 0.7,
    'provider': os.getenv('ICORE_LLM_PROVIDER'), 'max_tokens': os.getenv('ICORE_LLM_MAX_TOKENS'),
    'source_rows_sha256': hashlib.sha256(json.dumps(source_rows, sort_keys=True).encode()).hexdigest(),
    'oracle_sha256': hashlib.sha256(oracle_blob).hexdigest(),
    'evaluation_injection': 'base_oracle' if injection_source else 'libro',
    'injection_sha256': hashlib.sha256(injection_blob).hexdigest() if injection_blob is not None else None,
    'template_sha256': fingerprint(template),
    'instructions_sha256': fingerprint(template.with_suffix('.txt')),
    'excluded_instance_ids': excluded,
}
manifest_path = root / 'run_config.json'
if manifest_path.exists():
    if json.loads(manifest_path.read_text(encoding='utf-8')) != manifest:
        raise SystemExit('Patched-oracle experiment inputs/settings changed; use a separate OUTPUT_ROOT.')
elif stage == 'evaluate' or (root.exists() and any(root.iterdir())):
    raise SystemExit('No saved patched-oracle configuration; generate in an empty experiment directory first.')

generated, results = root / 'generated_tests', root / 'execution_results.json'
if stage == 'evaluate':
    for sample in range(1, samples + 1):
        check_files(generated, rows, f'_n{sample}.txt')
preflight(rows, model)
root.mkdir(parents=True, exist_ok=True)
inputs = root / 'inputs'
inputs.mkdir(exist_ok=True)


def snapshot(name, blob):
    path = inputs / name
    if path.exists():
        if path.read_bytes() != blob:
            raise SystemExit(f'Saved context snapshot differs from the input: {path}; use a separate OUTPUT_ROOT.')
    else:
        path.write_bytes(blob)
    return path


saved_oracle = snapshot('patched_tests.json', oracle_blob)
injection = snapshot('injection_base_tests.json', injection_blob) if injection_blob is not None else 'libro'
manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
selected_csv, env = prepare_selection(root / 'selections', rows)
print(f'Patched-oracle-test BRT: {len(rows)} instances, {samples} candidate(s) each; '
      f'excluded: {excluded}; stage: {stage}; injection: {manifest["evaluation_injection"]}', flush=True)
if stage in ('all', 'generate'):
    run_module('scripts.generator.llm_query',
               '--dataset_csv', selected_csv, '--model', model, '--exp_name', 'oracle_test_patched',
               '--query_time', samples, '--temperature', '0.7', '--context_test_path', saved_oracle,
               '--template_file', template, '--out_dir', generated,
               '--save_prompt', '--prompt_dir', root / 'prompts', env=env)
for sample in range(1, samples + 1):
    check_files(generated, rows, f'_n{sample}.txt')
if stage in ('all', 'evaluate'):
    # No reference test patch is applied: execute the generated candidate on both production versions.
    run_module('scripts.libro.postprocess_swe',
               '--dataset_csv', selected_csv, '--gen_test_dir', generated, '--model', model,
               '--exp_name', 'oracle_test_patched', '--samples', samples,
               '--injection_path', injection, '--result_file', results, env=env)
    summarize(results, rows, samples, no_code=[row['instance_id'] for row in rows], excluded=excluded)
print(f'Patched-oracle outputs: {root}', flush=True)
PY
