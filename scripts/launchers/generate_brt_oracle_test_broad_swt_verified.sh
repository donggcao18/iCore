#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Generation only: use the contexts produced by the broad retrieval launcher.
MODEL="${MODEL:-openai/gpt-6-luna}"
REPO="${REPO:-scikit-learn/scikit-learn}"
MAX_DEPTH="${MAX_DEPTH:-8}"
ORACLE_DIR="${ORACLE_DIR:-$PWD/retrieval_results/oracle/test/swt-bench-verified/${REPO##*/}/legacy_d${MAX_DEPTH}}"
ORACLE="${ORACLE:-$ORACLE_DIR/related_tests_oracle_base_augmented.json}"
DATASET_CSV="${DATASET_CSV:-$PWD/data/swt-bench-verified/test.csv}"
SAMPLES="${SAMPLES:-1}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$PWD/retrieval_results}"
INSTANCE_ID="${INSTANCE_ID:-}"
export ICORE_LLM_TIMEOUT="${ICORE_LLM_TIMEOUT:-2500}"
export PYTHONUNBUFFERED=1

exec "${PYTHON:-python}" -u - "$MODEL" "$REPO" "$ORACLE" "$DATASET_CSV" \
    "$SAMPLES" "$OUTPUT_ROOT" "$MAX_DEPTH" "$INSTANCE_ID" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys

from scripts.config import API_KEY, BASE_URL
from scripts.run_brt import fingerprint
from scripts.run_retrieval import ROOT, artifact_paths, check_files, prepare_selection, run_module, select_rows

model, repo, oracle_arg, csv_arg, samples_arg, output_arg, depth_arg, instance = sys.argv[1:]
try:
    samples, depth = int(samples_arg), int(depth_arg)
except ValueError:
    raise SystemExit('SAMPLES and MAX_DEPTH must be positive integers.')
if samples < 1 or depth < 1:
    raise SystemExit('SAMPLES and MAX_DEPTH must be positive integers.')

oracle, dataset_csv = Path(oracle_arg).resolve(), Path(csv_arg).resolve()
for path in (dataset_csv, oracle):
    if not path.is_file():
        raise SystemExit(f'Missing input: {path}')
rows = select_rows(dataset_csv, repo, 'swt-verified')
if instance:
    rows = [row for row in rows if row['instance_id'] == instance]
    if not rows:
        raise SystemExit(f'INSTANCE_ID is not selected for {repo}: {instance}')
oracle_blob = oracle.read_bytes()
contexts = json.loads(oracle_blob)
if not isinstance(contexts, dict):
    raise SystemExit(f'Oracle tests must map instance IDs to test lists: {oracle}')
for row in rows:
    tests = contexts.get(row['instance_id'])
    if not isinstance(tests, list) or any(
        not isinstance(test, dict) or not all(
            isinstance(test.get(key), str) and test[key].strip()
            for key in ('file', 'name', 'code_content')
        ) for test in tests
    ):
        raise SystemExit(f'Missing or invalid broad oracle context for {row["instance_id"]}: {oracle}')
if model not in API_KEY or model not in BASE_URL:
    raise SystemExit(f'Model {model!r} needs API_KEY and BASE_URL entries in scripts/config.py.')
if not API_KEY.get(model):
    raise SystemExit(f'API key for {model!r} is not set; export its configured environment variable (QWEN_API_KEY for openai/gpt-6-luna).')

exp = f'oracle_test_broad_d{depth}'
root = artifact_paths(Path(output_arg), 'swt-verified', model, repo).category('brt') / f'{exp}_s{samples}'
template = ROOT / 'data/prompt_templates/prompt_with_test.json'
manifest = {
    'context_mode': 'oracle_test_only', 'oracle_revision': 'base',
    'experiment': exp, 'model': model, 'base_url': BASE_URL.get(model),
    'repo': repo, 'samples': samples, 'temperature': 0.7,
    'provider': os.getenv('ICORE_LLM_PROVIDER'), 'max_tokens': os.getenv('ICORE_LLM_MAX_TOKENS'),
    'source_rows_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
    'oracle_sha256': hashlib.sha256(oracle_blob).hexdigest(),
    'template_sha256': fingerprint(template),
    'instructions_sha256': fingerprint(template.with_suffix('.txt')),
}
manifest_path = root / 'run_config.json'
if manifest_path.exists():
    if json.loads(manifest_path.read_text(encoding='utf-8')) != manifest:
        raise SystemExit('Broad-oracle generation inputs/settings changed; use a separate OUTPUT_ROOT.')
elif root.exists() and any(root.iterdir()):
    raise SystemExit('No saved generation configuration; use an empty experiment directory.')

inputs = root / 'inputs'
inputs.mkdir(parents=True, exist_ok=True)
saved_oracle = inputs / 'broad_base_tests.json'
if saved_oracle.exists() and saved_oracle.read_bytes() != oracle_blob:
    raise SystemExit('Saved broad-oracle context differs; use a separate OUTPUT_ROOT.')
saved_oracle.write_bytes(oracle_blob)
manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
selected_csv, env = prepare_selection(root / 'selections', rows)
generated = root / 'generated_tests'
empty = sum(not contexts[row['instance_id']] for row in rows)
print(f'Broad oracle BRT generation: {len(rows)} instances, {samples} candidate(s) each; model={model}', flush=True)
print(f'Oracle input: {oracle}; empty test contexts: {empty}', flush=True)
print(f'Output directory: {root}', flush=True)
# Prompt context is the issue plus retrieved tests. No harness injection occurs.
run_module('scripts.generator.llm_query',
           '--dataset_csv', selected_csv, '--repo', repo, '--model', model,
           '--exp_name', exp, '--query_time', samples, '--temperature', '0.7',
           '--context_test_path', saved_oracle, '--template_file', template,
           '--out_dir', generated, '--save_prompt', '--prompt_dir', root / 'prompts', env=env)
for sample in range(1, samples + 1):
    check_files(generated, rows, f'_n{sample}.txt')
print(f'Generated {len(rows) * samples} BRT candidates in {generated}', flush=True)
PY
