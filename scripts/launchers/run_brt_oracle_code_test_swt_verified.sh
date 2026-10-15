#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Base code oracle plus the archived broad base-test oracle, then buggy/fixed evaluation.
MODEL="${MODEL:-deepseek/deepseek-r1-0528}"
REPO="${REPO:-pytest-dev/pytest}"
CODE="${CODE:-./retrieval_results/oracle/code/swt-bench-verified/${REPO##*/}/code_retrieval_oracle_base.json}"
ORACLE="${ORACLE:-./retrieval_results/oracle/test/broad_selection_backup_2026-10-12/swt-bench-verified/${REPO##*/}/related_tests_oracle_base_augmented.json}"
INJECTION="${INJECTION:-$ORACLE}"
DATASET_CSV="${DATASET_CSV:-./data/swt-bench-verified/oracle_input_pylint_pytest.csv}"
EXCLUDE_INSTANCE="${EXCLUDE_INSTANCE-}"
SAMPLES="${SAMPLES:-1}"
OUTPUT_ROOT="${OUTPUT_ROOT:-./retrieval_results}"
STAGE="${STAGE:-all}"
export ICORE_LLM_TIMEOUT="${ICORE_LLM_TIMEOUT:-5000}"
export ICORE_TEST_TIMEOUT="${ICORE_TEST_TIMEOUT:-60}"

exec "${PYTHON:-python}" - "$MODEL" "$REPO" "$CODE" "$ORACLE" "$DATASET_CSV" \
    "$SAMPLES" "$OUTPUT_ROOT" "$EXCLUDE_INSTANCE" "$STAGE" "$INJECTION" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys

from scripts.config import BASE_URL
from scripts.retrieval_formats import split_code_documents, test_retrieval
from scripts.run_brt import fingerprint, summarize
from scripts.run_retrieval import ROOT, artifact_paths, check_files, preflight, prepare_selection, run_module, select_rows
from scripts.utils.patch_utils import prepare_reference_patch

model, repo, code_arg, oracle_arg, csv_arg, samples_arg, output_arg, exclude, stage, injection_arg = sys.argv[1:]
try:
    samples = int(samples_arg)
except ValueError:
    raise SystemExit('SAMPLES must be a positive integer.')
if samples < 1:
    raise SystemExit('SAMPLES must be a positive integer.')
if stage not in ('all', 'generate', 'evaluate'):
    raise SystemExit('STAGE must be all, generate, or evaluate.')
code, oracle, dataset_csv = (Path(arg).resolve() for arg in (code_arg, oracle_arg, csv_arg))
source_rows = select_rows(dataset_csv, repo, 'swt-verified')
if exclude and exclude not in {row['instance_id'] for row in source_rows}:
    raise SystemExit(f'EXCLUDE_INSTANCE is not selected for {repo}: {exclude}')
excluded = [row['instance_id'] for row in source_rows if row['instance_id'] == exclude]
rows = [row for row in source_rows if row['instance_id'] not in excluded]
if not rows:
    raise SystemExit('No instances remain for this experiment.')

code_blob = code.read_bytes()
code_context = json.loads(code_blob)
if not isinstance(code_context, dict):
    raise SystemExit(f'Oracle code must map instance IDs to code objects: {code}')
for row in rows:
    bug_id = row['instance_id']
    try:
        documents, _ = split_code_documents(code_context.get(bug_id))
        if not any(doc is not None and doc['code_content'].strip() for doc in documents.values()):
            raise ValueError('no usable production-code snippets')
        if any(doc is not None and not doc['code_content'].strip() for doc in documents.values()):
            raise ValueError('empty production-code snippet')
    except ValueError as exc:
        raise SystemExit(f'Missing or invalid oracle code context for {bug_id}: {code}: {exc}')


def read_tests(path):
    blob = path.read_bytes()
    contexts = json.loads(blob)
    if not isinstance(contexts, dict):
        raise SystemExit(f'Oracle tests must map instance IDs to test lists: {path}')
    for row in rows:
        bug_id = row['instance_id']
        try:
            test_retrieval({bug_id: contexts.get(bug_id)})
        except ValueError as exc:
            raise SystemExit(f'Missing or invalid oracle test context for {bug_id}: {path}: {exc}')
    return blob


oracle_blob = read_tests(oracle)
injection_source = None if injection_arg == 'libro' else Path(injection_arg).resolve()
injection_blob = (oracle_blob if injection_source == oracle else read_tests(injection_source)) if injection_source else None
if stage != 'generate':
    for row in rows:
        prepare_reference_patch(row.get('patch'), row['instance_id'])

experiment = 'oracle_code_and_test_base'
root = artifact_paths(Path(output_arg), 'swt-verified', model, repo).category('brt') / f'{experiment}_s{samples}'
template = ROOT / 'data/prompt_templates/prompt_with_code_and_tests.json'
manifest = {
    'context_mode': 'oracle_code_and_test', 'oracle_revision': 'base',
    'model': model, 'base_url': BASE_URL.get(model), 'repo': repo,
    'samples': samples, 'temperature': 0.7,
    'provider': os.getenv('ICORE_LLM_PROVIDER'), 'max_tokens': os.getenv('ICORE_LLM_MAX_TOKENS'),
    'source_rows_sha256': hashlib.sha256(json.dumps(source_rows, sort_keys=True).encode()).hexdigest(),
    'oracle_code_source': str(code), 'oracle_test_source': str(oracle),
    'oracle_code_sha256': hashlib.sha256(code_blob).hexdigest(),
    'oracle_test_sha256': hashlib.sha256(oracle_blob).hexdigest(),
    'evaluation_injection': 'base_oracle' if injection_source else 'libro',
    'injection_sha256': hashlib.sha256(injection_blob).hexdigest() if injection_blob is not None else None,
    'template_sha256': fingerprint(template),
    'instructions_sha256': fingerprint(template.with_name('prompt_with_code_and_test.txt')),
    'excluded_instance_ids': excluded,
}
manifest_path = root / 'run_config.json'
if manifest_path.exists():
    if json.loads(manifest_path.read_text(encoding='utf-8')) != manifest:
        raise SystemExit('Combined-oracle inputs/settings changed; use a separate OUTPUT_ROOT.')
elif stage == 'evaluate' or (root.exists() and any(root.iterdir())):
    raise SystemExit('No saved combined-oracle configuration; generate in an empty experiment directory first.')
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
            raise SystemExit(f'Saved context snapshot differs: {path}; use a separate OUTPUT_ROOT.')
    else:
        path.write_bytes(blob)
    return path


saved_code = snapshot('base_code.json', code_blob)
saved_tests = snapshot('base_tests.json', oracle_blob)
injection = (saved_tests if injection_source == oracle else snapshot('injection_base_tests.json', injection_blob)) if injection_source else 'libro'
manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
selected_csv, env = prepare_selection(root / 'selections', rows)
print(f'Combined base-oracle BRT: {len(rows)} instances, {samples} candidate(s) each; '
      f'excluded: {excluded}; stage: {stage}', flush=True)
if stage in ('all', 'generate'):
    run_module('scripts.generator.llm_query',
               '--dataset_csv', selected_csv, '--model', model, '--exp_name', experiment,
               '--query_time', samples, '--temperature', '0.7',
               '--context_code_path', saved_code, '--context_test_path', saved_tests,
               '--template_file', template, '--out_dir', generated,
               '--save_prompt', '--prompt_dir', root / 'prompts', env=env)
for sample in range(1, samples + 1):
    check_files(generated, rows, f'_n{sample}.txt')
if stage in ('all', 'evaluate'):
    run_module('scripts.libro.postprocess_swe',
               '--dataset_csv', selected_csv, '--gen_test_dir', generated, '--model', model,
               '--exp_name', experiment, '--samples', samples,
               '--injection_path', injection, '--result_file', results, env=env)
    summarize(results, rows, samples, excluded=excluded)
print(f'Combined-oracle outputs: {root}', flush=True)
PY
