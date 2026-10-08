#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# Only base production-code oracle snippets enter this experiment's prompts.
MODEL="${MODEL:-z-ai/glm-5.3-flash}"
REPO="${REPO:-pylint-dev/pylint}"
CODE="${CODE:-/research/cbim/vast/qt60/any-ssr/utils/iCore/retrieval_results/code/oracle/swt-bench-verified/pylint/code_retrieval_oracle_base.json}"
DATASET_CSV="${DATASET_CSV:-./data/swt-bench-verified/oracle_input_pylint_pytest.csv}"
EXCLUDE_INSTANCE="${EXCLUDE_INSTANCE-pylint-dev__pylint-7277}"
SAMPLES="${SAMPLES:-1}"
OUTPUT_ROOT="${OUTPUT_ROOT:-./retrieval_results}"
STAGE="${STAGE:-all}"
export ICORE_LLM_TIMEOUT="${ICORE_LLM_TIMEOUT:-180}"
export ICORE_TEST_TIMEOUT="${ICORE_TEST_TIMEOUT:-60}"

exec "${PYTHON:-python}" - "$MODEL" "$REPO" "$CODE" "$DATASET_CSV" \
    "$SAMPLES" "$OUTPUT_ROOT" "$EXCLUDE_INSTANCE" "$STAGE" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys

from scripts.config import BASE_URL
from scripts.retrieval_formats import split_code_documents
from scripts.run_brt import fingerprint, summarize
from scripts.run_retrieval import ROOT, artifact_paths, check_files, preflight, prepare_selection, run_module, select_rows
from scripts.utils.patch_utils import prepare_reference_patch

model, repo, code_arg, csv_arg, samples_arg, output_arg, exclude, stage = sys.argv[1:]
try:
    samples = int(samples_arg)
except ValueError:
    raise SystemExit('SAMPLES must be a positive integer.')
if samples < 1:
    raise SystemExit('SAMPLES must be a positive integer.')
if stage not in ('all', 'generate', 'evaluate'):
    raise SystemExit('STAGE must be all, generate, or evaluate.')
code, dataset_csv = Path(code_arg).resolve(), Path(csv_arg).resolve()
source_rows = select_rows(dataset_csv, repo, 'swt-verified')
excluded = [row['instance_id'] for row in source_rows if row['instance_id'] == exclude]
rows = [row for row in source_rows if row['instance_id'] not in excluded]
if not rows:
    raise SystemExit('No instances remain for this experiment.')
contexts = json.loads(code.read_text(encoding='utf-8'))
if not isinstance(contexts, dict):
    raise SystemExit(f'Oracle code must map instance IDs to code objects: {code}')
for row in rows:
    bug_id = row['instance_id']
    try:
        documents, _ = split_code_documents(contexts.get(bug_id))
        if not any(doc is not None and doc['code_content'].strip() for doc in documents.values()):
            raise ValueError('no usable production-code snippets')
        if any(doc is not None and not doc['code_content'].strip() for doc in documents.values()):
            raise ValueError('empty production-code snippet')
    except ValueError as exc:
        raise SystemExit(f'Missing or invalid oracle code context for {bug_id}: {code}: {exc}')
    if stage != 'generate':
        prepare_reference_patch(row.get('patch'), bug_id)

root = artifact_paths(Path(output_arg), 'swt-verified', model, repo).category('brt') / f'oracle_code_base_s{samples}'
template = ROOT / 'data/prompt_templates/prompt_with_code.json'
manifest = {
    'context_mode': 'oracle_code_only', 'model': model, 'base_url': BASE_URL.get(model),
    'repo': repo, 'samples': samples, 'temperature': 0.7,
    'provider': os.getenv('ICORE_LLM_PROVIDER'), 'max_tokens': os.getenv('ICORE_LLM_MAX_TOKENS'),
    'source_rows_sha256': hashlib.sha256(json.dumps(source_rows, sort_keys=True).encode()).hexdigest(),
    'oracle_code_sha256': fingerprint(code), 'template_sha256': fingerprint(template),
    'instructions_sha256': fingerprint(template.with_suffix('.txt')),
    'excluded_instance_ids': excluded, 'evaluation_injection': 'libro',
}
manifest_path = root / 'run_config.json'
if manifest_path.exists():
    if json.loads(manifest_path.read_text(encoding='utf-8')) != manifest:
        raise SystemExit('Oracle experiment inputs/settings changed; use a separate OUTPUT_ROOT.')
elif stage == 'evaluate':
    raise SystemExit('No saved code-only experiment configuration; run generation first.')
if stage == 'evaluate':
    for sample in range(1, samples + 1):
        check_files(root / 'generated_tests', rows, f'_n{sample}.txt')
preflight(rows, model)
root.mkdir(parents=True, exist_ok=True)
manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
selected_csv, env = prepare_selection(root / 'selections', rows)
generated, results = root / 'generated_tests', root / 'execution_results.json'
print(f'Oracle-code-only BRT experiment: {len(rows)} instances, {samples} candidate(s) each; '
      f'excluded: {excluded}; stage: {stage}', flush=True)
if stage in ('all', 'generate'):
    # No --context_test_path: generation uses only the issue and base code.
    run_module('scripts.generator.llm_query',
               '--dataset_csv', selected_csv, '--model', model, '--exp_name', 'oracle_code_base',
               '--query_time', samples, '--temperature', '0.7', '--context_code_path', code,
               '--template_file', template, '--out_dir', generated,
               '--save_prompt', '--prompt_dir', root / 'prompts', env=env)
for sample in range(1, samples + 1):
    check_files(generated, rows, f'_n{sample}.txt')
if stage in ('all', 'evaluate'):
    # Existing token-similarity injection requires no test-retrieval JSON.
    run_module('scripts.libro.postprocess_swe',
               '--dataset_csv', selected_csv, '--gen_test_dir', generated, '--model', model,
               '--exp_name', 'oracle_code_base', '--samples', samples,
               '--injection_path', 'libro', '--result_file', results, env=env)
    summarize(results, rows, samples, excluded=excluded)
print(f'Oracle experiment outputs: {root}', flush=True)
PY
