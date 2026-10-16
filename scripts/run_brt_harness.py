"""Experimental BRT harness: retrieved scaffolds, buggy feedback, frozen evaluation."""

import argparse
import csv
import json
import os
from pathlib import Path
import subprocess

from scripts.brt_harness.context import TestContext
from scripts.brt_harness.execution import PytestRunner, evaluate_frozen, git
from scripts.brt_harness.workflow import digest, generate_instance, save_json
from scripts.config import BASE_URL, REPO_ROOT_DIR
from scripts.test_retrieval.augment_oracle import base_sources
from scripts.retrieval_formats import split_code_documents


ROOT = Path(__file__).resolve().parents[1]


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-csv', type=Path, required=True)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--repo-dir', type=Path)
    parser.add_argument('--tests', type=Path, required=True, help='Retrieved tests JSON keyed by instance ID.')
    parser.add_argument('--code', type=Path, help='Optional base production snippets JSON keyed by instance ID.')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--model', default='gpt-4o')
    parser.add_argument('--python', type=Path, help='Historical environment Python; otherwise resolve Conda.')
    parser.add_argument('--instance-id', action='append', default=[])
    parser.add_argument('--stage', choices=('prepare', 'generate', 'evaluate', 'all'), default='all')
    parser.add_argument('--attempts', type=positive, default=4)
    parser.add_argument('--max-hosts', type=positive, default=2)
    parser.add_argument('--max-context-chars', type=positive, default=24000)
    parser.add_argument('--test-timeout', type=positive, default=60)
    parser.add_argument('--repetitions', type=positive, default=2, help='Independent runs per version in final evaluation.')
    parser.add_argument('--install-project', action='store_true',
                        help='Rebuild each snapshot with pip --no-deps --no-build-isolation -e . in the selected environment.')
    parser.add_argument('--temperature', type=float, default=0.7)
    parser.add_argument('--context-provenance', choices=('retrieved', 'oracle-base'), default='retrieved')
    args = parser.parse_args(argv)
    if not 0 <= args.temperature <= 2:
        parser.error('--temperature must be between 0 and 2')
    if args.repo in {'django/django', 'sympy/sympy'}:
        parser.error('This experimental adapter supports pytest runners; Django/SymPy require native adapters.')
    csv.field_size_limit(16 * 1024 * 1024)
    with args.dataset_csv.open(encoding='utf-8-sig', newline='') as handle:
        rows = [row for row in csv.DictReader(handle) if row['repo'] == args.repo]
    if not rows or len({row['instance_id'] for row in rows}) != len(rows):
        parser.error('Expected unique instances for the selected repository')
    requested = set(args.instance_id)
    missing = requested - {row['instance_id'] for row in rows}
    if missing:
        parser.error(f'Unknown instance IDs: {sorted(missing)}')
    rows = [row for row in rows if not requested or row['instance_id'] in requested]
    references = json.loads(args.tests.read_text(encoding='utf-8'))
    code = json.loads(args.code.read_text(encoding='utf-8')) if args.code else {}
    if not isinstance(references, dict) or not isinstance(code, dict):
        parser.error('--tests and --code must contain instance mappings')
    repo = (args.repo_dir or Path(REPO_ROOT_DIR).expanduser() / args.repo.split('/')[-1]).resolve()
    if not (repo / '.git').exists():
        parser.error(f'Missing Git source clone: {repo}')
    args.output_dir = args.output_dir.resolve()
    settings = dict(model=args.model, base_url=BASE_URL.get(args.model), attempts=args.attempts,
                    max_hosts=args.max_hosts, max_context_chars=args.max_context_chars,
                    temperature=args.temperature, test_timeout=args.test_timeout,
                    repetitions=args.repetitions,
                    install_project=args.install_project,
                    python=str(args.python.resolve()) if args.python else None,
                    provenance=args.context_provenance,
                    provider=os.getenv('ICORE_LLM_PROVIDER'), max_tokens=os.getenv('ICORE_LLM_MAX_TOKENS'),
                    request_timeout=os.getenv('ICORE_LLM_TIMEOUT'), source_clone=str(repo))
    harness_files = sorted((ROOT / 'scripts/brt_harness').glob('*.py')) + [Path(__file__),
        ROOT / 'scripts/test_retrieval/static_dependencies.py',
        ROOT / 'scripts/test_retrieval/augment_oracle.py',
        ROOT / 'scripts/test_retrieval/extract_oracle.py', ROOT / 'scripts/utils/llm_api.py']
    settings['implementation'] = digest({str(p.relative_to(ROOT)): p.read_text(encoding='utf-8') for p in harness_files})
    # Preflight all inputs/manifests before creating artifacts or spending calls.
    plans = []
    for row in rows:
        instance = row['instance_id']
        if not instance or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in instance):
            parser.error(f'Unsafe instance ID: {instance!r}')
        values = references.get(instance)
        if not isinstance(values, list) or any(not isinstance(ref, dict) or any(
                not isinstance(ref.get(key), str) or not ref[key].strip()
                for key in ('file', 'name', 'code_content')) for ref in values):
            parser.error(f'Missing or invalid test references: {instance}')
        if args.code and instance not in code:
            parser.error(f'Missing production context key: {instance}')
        if code.get(instance) is not None:
            code[instance], _ = split_code_documents(code[instance])
        git(repo, 'cat-file', '-e', row['base_commit'] + '^{commit}')
        folder = args.output_dir / instance
        # Gold fields belong solely to evaluation. They never enter the generation
        # manifest, controller, context lookup, or model prompt.
        public_row = {key: row[key] for key in ('instance_id', 'repo', 'base_commit', 'problem_statement')}
        public_row['version'] = row.get('version')
        manifest = dict(settings=settings, input=digest(dict(row=public_row,
                        references=references[instance], code=code.get(instance))), version=1)
        manifest_path = folder / 'run_config.json'
        if manifest_path.exists() and json.loads(manifest_path.read_text(encoding='utf-8')) != manifest:
            raise RuntimeError(f'{instance}: inputs/harness changed; choose a new --output-dir')
        if not manifest_path.exists() and folder.exists() and any(folder.iterdir()):
            raise RuntimeError(f'{instance}: existing artifacts have no manifest; choose a new --output-dir')
        generation_path = folder / 'generation.json'
        if args.stage == 'evaluate' and not generation_path.exists():
            raise RuntimeError(f'{instance}: generate and freeze a candidate before evaluating')
        plans.append((row, folder, public_row, manifest))
    summaries = []
    for row, folder, public_row, manifest in plans:
        instance = row['instance_id']
        manifest_path = folder / 'run_config.json'
        generation_path = folder / 'generation.json'
        save_json(manifest_path, manifest)
        if args.stage != 'evaluate' and not generation_path.exists():
            sources, errors, collection = base_sources(repo, row['base_commit'])
            context = TestContext(sources, references[instance], max_chars=args.max_context_chars,
                                  test_patterns=collection.get('test_patterns'),
                                  ignore_patterns=collection.get('ignore_patterns', ()),
                                  provenance=args.context_provenance)
            save_json(folder / 'prepared.json', dict(hosts=[host.__dict__ for host in context.hosts(
                row['problem_statement'], args.max_hosts)], diagnostics=errors,
                context=context.package(context.hosts(row['problem_statement'], args.max_hosts)[0])))
            print(f'{instance}: prepared base test context', flush=True)
            if args.stage == 'prepare':
                continue
            if args.python:
                interpreter = args.python.resolve()
            else:
                from scripts.utils.swe_util import get_env_path
                interpreter = get_env_path(row)
            from scripts.utils.llm_api import query_llm
            runner = PytestRunner(interpreter, timeout=args.test_timeout, install_project=args.install_project)
            runner.preflight()
            generate_instance(public_row, repo, context, code.get(instance), runner,
                              lambda messages, temperature: query_llm(messages, args.model, temperature),
                              folder, attempts=args.attempts, temperature=args.temperature,
                              max_hosts=args.max_hosts)
        if args.stage == 'prepare':
            continue
        generation = json.loads(generation_path.read_text(encoding='utf-8'))
        selected = generation['selected']
        record = dict(instance_id=instance, selected=selected is not None,
                      attempts_used=generation['attempts_used'])
        if args.stage in ('all', 'evaluate'):
            evaluation_input = digest(dict(base_commit=row['base_commit'], patch=row.get('patch'), selected=selected))
            evaluation_path = folder / 'evaluation.json'
            if evaluation_path.exists():
                evaluation = json.loads(evaluation_path.read_text(encoding='utf-8'))
                if evaluation['input_sha256'] != evaluation_input:
                    raise RuntimeError(f'{instance}: evaluation inputs changed; choose a new output directory')
            elif selected:
                if not row.get('patch', '').strip():
                    raise ValueError(f'{instance}: missing production fix for final evaluation')
                if args.python:
                    interpreter = args.python.resolve()
                else:
                    from scripts.utils.swe_util import get_env_path
                    interpreter = get_env_path(row)
                runner = PytestRunner(interpreter, timeout=args.test_timeout, install_project=args.install_project)
                runner.preflight()
                evaluation = evaluate_frozen(repo, row, selected, runner, folder / 'evaluation',
                                             repetitions=args.repetitions)
                evaluation['input_sha256'] = evaluation_input
                save_json(evaluation_path, evaluation)
            else:
                evaluation = dict(success=False, reason='no_candidate_selected', input_sha256=evaluation_input)
                save_json(evaluation_path, evaluation)
            record.update(success=evaluation['success'])
        summaries.append(record)
        print(f'{instance}: {record}', flush=True)
    if summaries:
        summary = dict(instances=len(summaries), records=summaries,
                       selected_instances=sum(record['selected'] for record in summaries))
        if args.stage in ('all', 'evaluate'):
            summary['reproduced_instances'] = sum(record['success'] for record in summaries)
            summary['instance_reproduction_rate'] = summary['reproduced_instances'] / len(summaries)
        save_json(args.output_dir / 'summary.json', summary)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from exc
