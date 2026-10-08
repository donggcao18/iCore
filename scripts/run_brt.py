"""Generate final BRT candidates from retrieval, then evaluate buggy/fixed code."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from scripts.run_retrieval import (
    ROOT, DEFAULT_OUTPUT, BENCHMARKS, BASE_URL, artifact_paths, benchmark_argument,
    check_json, model_argument, positive_int, preflight, prepare_selection,
    repo_argument, run_module, select_rows,
)


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def summarize(result_file, rows, samples):
    results = json.loads(result_file.read_text(encoding='utf-8'))
    successes = reproduced = 0
    for row in rows:
        bug_id = row['instance_id']
        instance_successes = 0
        for sample in range(1, samples + 1):
            name = f'{bug_id}_n{sample}.txt'
            if name not in results.get(bug_id, {}):
                raise RuntimeError(f'Missing evaluation result for {name} in {result_file}')
            result = results[bug_id][name]
            success = isinstance(result, dict) and result.get('success') is True
            instance_successes += success
            print(f'{name}: ' + ('FAIL-TO-PASS' if success else 'NOT REPRODUCED / ERROR'))
        successes += instance_successes
        reproduced += instance_successes > 0
    summary = {
        'instances': len(rows), 'samples_per_instance': samples,
        'candidates': len(rows) * samples, 'fail_to_pass_candidates': successes,
        'reproduced_instances': reproduced,
        'instance_reproduction_rate': reproduced / len(rows),
    }
    result_file.with_name('summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(f'{successes}/{summary["candidates"]} fail-to-pass candidates; '
          f'{reproduced}/{len(rows)} instances reproduced. Results: {result_file}')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', '--dataset', type=benchmark_argument, default='swt-verified')
    parser.add_argument('--model', type=model_argument, required=True)
    parser.add_argument('--repo', type=repo_argument, action='append', required=True)
    parser.add_argument('--dataset-csv', type=Path)
    parser.add_argument('--output-root', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--iterations', type=positive_int, default=3,
                        help='Completed retrieval refinements; use related_tests_(iterations+1).json.')
    parser.add_argument('--samples', type=positive_int, default=1)
    parser.add_argument('--temperature', type=float, default=0.7)
    parser.add_argument('--stage', choices=('all', 'generate', 'evaluate'), default='all')
    parser.add_argument('--provider')
    parser.add_argument('--max-tokens', type=positive_int)
    parser.add_argument('--timeout', type=positive_int, default=180, help='LLM request timeout in seconds.')
    parser.add_argument('--test-timeout', type=positive_int, default=60, help='Timeout for each buggy/fixed test run.')
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args(argv)
    if not 0 <= args.temperature <= 2:
        parser.error('--temperature must be between 0 and 2')
    benchmark = BENCHMARKS[args.benchmark]
    dataset_csv = args.dataset_csv or ROOT / 'data' / benchmark.folder / 'test.csv'
    selections = {repo: select_rows(dataset_csv, repo, args.benchmark) for repo in dict.fromkeys(args.repo)}
    template = ROOT / 'data/prompt_templates/prompt_with_code_and_tests.json'
    instructions = template.with_name('prompt_with_code_and_test.txt')
    plans = []
    # Check every selected repository before spending credits on any generation.
    for repo, rows in selections.items():
        paths = artifact_paths(args.output_root, args.benchmark, args.model, repo)
        tests = paths.tests / f'related_tests_{args.iterations + 1}.json'
        check_json(paths.code, rows, 'code')
        check_json(tests, rows, 'tests')
        if any(not row.get('patch', '').startswith('diff --git ') for row in rows):
            raise RuntimeError(f'Missing reference production fix for {repo}; evaluation requires patch.')
        root = paths.category('brt') / f'i{args.iterations}_s{args.samples}'
        manifest = {
            'evaluation_version': 2, 'benchmark': benchmark.folder, 'repo': repo,
            'model': args.model, 'base_url': BASE_URL.get(args.model),
            'provider': args.provider, 'max_tokens': args.max_tokens,
            'samples': args.samples, 'iterations': args.iterations, 'temperature': args.temperature,
            'rows_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
            'code_sha256': fingerprint(paths.code), 'tests_sha256': fingerprint(tests),
            'template_sha256': fingerprint(template), 'instructions_sha256': fingerprint(instructions),
        }
        manifest_path = root / 'run_config.json'
        if manifest_path.exists() and json.loads(manifest_path.read_text(encoding='utf-8')) != manifest:
            raise RuntimeError(f'BRT configuration/context changed for {repo}; use a separate --output-root.')
        plans.append((paths, rows, tests, root, manifest))
        print(f'{repo}: {len(rows)} instances, {args.samples} candidate(s) each; context: {tests}', flush=True)
    preflight([row for rows in selections.values() for row in rows], args.model)
    if args.preflight_only:
        print('BRT preflight passed; no generation or evaluation was run.')
        return
    for paths, rows, tests, root, manifest in plans:
        root.mkdir(parents=True, exist_ok=True)
        (root / 'run_config.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        selected_csv, env = prepare_selection(root / 'selections', rows)
        for name, value in (('ICORE_LLM_PROVIDER', args.provider), ('ICORE_LLM_MAX_TOKENS', args.max_tokens),
                            ('ICORE_LLM_TIMEOUT', args.timeout), ('ICORE_TEST_TIMEOUT', args.test_timeout)):
            env.pop(name, None)
            if value is not None:
                env[name] = str(value)
        generated = root / 'generated_tests'
        results = root / 'execution_results.json'
        if args.stage in ('all', 'generate'):
            run_module('scripts.generator.llm_query',
                       '--dataset_csv', selected_csv, '--model', args.model,
                       '--exp_name', 'brt', '--query_time', args.samples, '--temperature', args.temperature,
                       '--context_code_path', paths.code, '--context_test_path', tests,
                       '--template_file', template, '--out_dir', generated,
                       '--save_prompt', '--prompt_dir', root / 'prompts', env=env)
        for row in rows:
            for sample in range(1, args.samples + 1):
                candidate = generated / f'{row["instance_id"]}_n{sample}.txt'
                if not candidate.is_file() or not candidate.stat().st_size:
                    raise RuntimeError(f'Missing or empty candidate: {candidate}')
        if args.stage in ('all', 'evaluate'):
            run_module('scripts.libro.postprocess_swe',
                       '--dataset_csv', selected_csv, '--gen_test_dir', generated,
                       '--model', args.model, '--exp_name', 'brt', '--samples', args.samples,
                       '--injection_path', tests, '--result_file', results, env=env)
            summarize(results, rows, args.samples)
        print(f'{paths.repo}: BRT {args.stage} complete. Outputs: {root}', flush=True)


def cli():
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == '__main__':
    cli()
