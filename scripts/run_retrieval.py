"""Run production/test retrieval with benchmark/model-scoped output folders.

All subprocesses run from the checkout root. Model outputs are isolated by
repository, and each existing stage retains its normal resume behavior.
"""

import argparse
import hashlib
import csv
from dataclasses import dataclass
import importlib.util
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import sys
from urllib.parse import quote

from scripts.config import API_KEY, BASE_URL, REPO_ROOT_DIR
from scripts.export_swt_verified import DATASET

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'retrieval_results'


@dataclass(frozen=True)
class Benchmark:
    folder: str
    source_dataset: str


BENCHMARKS = {
    'swt-verified': Benchmark('swt-bench-verified', DATASET),
    'lite': Benchmark('swe-bench-lite', 'SWE-bench/SWE-bench_Lite'),
}


def benchmark_argument(value):
    for name, benchmark in BENCHMARKS.items():
        if value in (name, benchmark.folder, benchmark.source_dataset):
            return name
    raise argparse.ArgumentTypeError('choose swt-verified or lite')


@dataclass(frozen=True)
class RetrievalPaths:
    """Retain the original artifact categories under a benchmark/model root."""
    model_root: Path
    repo: str

    def category(self, name):
        return self.model_root / name / Path(*self.repo.split('/'))

    @property
    def selection(self):
        return self.category('selections')

    @property
    def manifest(self):
        return self.selection / 'run_config.json'

    @property
    def keywords(self):
        return self.category('code') / 'keywords.json'

    @property
    def code(self):
        return self.category('code') / 'retrieval_results.json'

    @property
    def graphs(self):
        return self.category('graphs')

    @property
    def tests(self):
        return self.category('test')

    @property
    def trees(self):
        return self.category('swe_test_cgs')

    @property
    def drafts(self):
        return self.category('drafts')


def artifact_paths(output_root, benchmark, model, repo):
    model_root = output_root.resolve() / BENCHMARKS[benchmark].folder / quote(model, safe='')
    return RetrievalPaths(model_root, repo)


def select_rows(path, repo, benchmark='swt-verified'):
    # Do not inherit another launcher's instance selection.
    csv.field_size_limit(10_000_000)
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        rows = [row for row in csv.DictReader(handle) if row['repo'] == repo]
    if not rows or len({row['instance_id'] for row in rows}) != len(rows):
        raise ValueError(f'Expected unique {BENCHMARKS[benchmark].folder} instances for {repo} in {path}')
    if benchmark == 'swt-verified' and any(
            row.get('source_dataset') != DATASET
            or not row.get('test_patch', '').startswith('diff --git ') for row in rows):
        raise ValueError('CSV must be normalized by python -m scripts.export_swt_verified')
    if benchmark == 'lite' and any(row.get('source_dataset') not in (
            None, '', BENCHMARKS[benchmark].source_dataset) for row in rows):
        raise ValueError('CSV source_dataset does not match SWE-bench Lite')
    return rows


def run_module(module, *args, env=None):
    command = [sys.executable, '-m', module, *map(str, args)]
    print('+ ' + shlex.join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def preflight(rows, model):
    errors = []
    if model not in API_KEY or model not in BASE_URL:
        errors.append(f'Model {model!r} needs API_KEY and BASE_URL entries in scripts/config.py.')
    elif not API_KEY.get(model):
        errors.append(f'API key for {model!r} is not set. Export the environment variable used in scripts/config.py.')
    required = ('openai', 'datasets', 'git', 'jedi', 'pyan', 'rank_bm25',
                'zss', 'nltk', 'pandas', 'numpy', 'tqdm', 'astor', 'Levenshtein', 'docutils')
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        errors.append('Missing pipeline dependencies: ' + ', '.join(missing)
                      + '. Install requirements.txt in your icore environment.')
    for repo in sorted({row['repo'] for row in rows}):
        clone = Path(REPO_ROOT_DIR).expanduser().resolve() / repo.split('/')[-1]
        if not (clone / '.git').exists():
            errors.append(f'Missing benchmark Git checkout: {clone}')
            continue
        result = subprocess.run(['git', '-C', str(clone), 'status', '--porcelain'],
                                capture_output=True, text=True, check=True)
        if result.stdout.strip():
            errors.append(f'Benchmark checkout has local changes: {clone}. '
                          'Use a clean disposable checkout; retrieval resets base commits.')
        for row in rows:
            if row['repo'] != repo:
                continue
            result = subprocess.run(
                ['git', '-C', str(clone), 'cat-file', '-e', row['base_commit'] + '^{commit}'],
                capture_output=True,
            )
            if result.returncode:
                errors.append(f"Missing base commit for {row['instance_id']} in {clone}")
    # Jedi needs the benchmark interpreter even for static retrieval.
    from scripts.utils.swe_util import get_conda_python, get_env_name
    for name in sorted({get_env_name(row) for row in rows}):
        try:
            get_conda_python(name)
        except (OSError, RuntimeError) as exc:
            errors.append(f'Missing benchmark environment {name}: {exc}')
    if errors:
        raise RuntimeError('Preflight failed:\n- ' + '\n- '.join(errors))


def prepare_selection(root, rows):
    root.mkdir(parents=True, exist_ok=True)
    ids = root / 'selected_ids.txt'
    ids.write_text('\n'.join(row['instance_id'] for row in rows) + '\n', encoding='utf-8')
    selected_csv = root / 'selected.csv'
    with selected_csv.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    env = os.environ.copy()
    env['SWT_IDS_FILE'] = str(ids)
    # Legacy production stages read both lists. Never overwrite existing IDs.
    for name in ('swt.txt', 'tdd.txt'):
        (ROOT / name).touch(exist_ok=True)
    return selected_csv, env


def check_json(path, rows, kind):
    values = json.loads(Path(path).read_text(encoding='utf-8'))
    missing = []
    for row in rows:
        key = row['instance_id']
        value = values.get(key)
        if kind == 'keywords':
            valid = isinstance(value, list) and bool(value) and all(isinstance(x, str) for x in value)
        elif kind == 'code':
            valid = isinstance(value, dict) and any(
                isinstance(node, dict) and node.get('code_content') for node in value.values())
        else:
            # Empty selections are valid model results, but missing IDs are not.
            valid = key in values and isinstance(value, list)
        if not valid:
            missing.append(key)
    if missing:
        raise RuntimeError(f'Missing or invalid {kind} in {path}: ' + ', '.join(missing))


def check_files(root, rows, suffix):
    missing = []
    for row in rows:
        path = Path(root) / (row['instance_id'] + suffix)
        if not path.is_file() or not path.stat().st_size:
            missing.append(str(path))
    if missing:
        raise RuntimeError('Missing or empty stage outputs:\n' + '\n'.join(missing))


def code_retrieval(paths, rows, selected_csv, env, workers, model):
    keywords, graphs, code = paths.keywords, paths.graphs, paths.code
    keywords.parent.mkdir(parents=True, exist_ok=True)
    graphs.mkdir(parents=True, exist_ok=True)
    common = ('--dataset_csv', selected_csv, '--swt')
    run_module('scripts.code_retrieval.extract_keywords', *common,
               '--keywords_path', keywords, '--model', model, env=env)
    check_json(keywords, rows, 'keywords')
    run_module('scripts.code_retrieval.repo_graph.graph', *common,
               '--graph_path', graphs, '--max_workers', workers, env=env)
    check_files(graphs, rows, '_graph.pkl')
    run_module('scripts.code_retrieval.retrieval', *common, '--keywords_path', keywords,
               '--graph_dir', graphs, '--save_path', code, env=env)
    check_json(code, rows, 'code')


def test_retrieval(paths, rows, selected_csv, env, workers, iterations, model):
    keywords, code = paths.keywords, paths.code
    check_json(keywords, rows, 'keywords')
    check_json(code, rows, 'code')
    trees, tests, drafts = paths.trees, paths.tests, paths.drafts
    for directory in (trees, tests, drafts):
        directory.mkdir(parents=True, exist_ok=True)
    common = ('--dataset_csv', selected_csv, '--swt')
    run_module('scripts.test_retrieval.similarities.get_all_cg_parallel', *common,
               '--output_dir', trees, '--proj', '', '--max_workers', workers, env=env)
    check_files(trees, rows, '/call_trees.db')
    check_files(trees, rows, '/df.json')
    run_module('scripts.test_retrieval.initial_retrieval', *common,
               '--related_tests_path', tests / 'related_tests_1.json',
               '--message_path', tests / 'messages/initial', '--model', model, env=env)
    check_json(tests / 'related_tests_1.json', rows, 'tests')
    # Keep the generator's prompt cache separate for every output location/model.
    experiment = hashlib.sha256(f'{paths.model_root}\0{paths.repo}\0{model}'.encode()).hexdigest()[:16]
    for iteration in range(1, iterations + 1):
        current = tests / f'related_tests_{iteration}.json'
        next_tests = tests / f'related_tests_{iteration + 1}.json'
        draft_dir = drafts / f'iteration_{iteration}'
        similarity = tests / f'test_similarity/{iteration}'
        run_module('scripts.generator.llm_query', *common,
                   '--exp_name', f'retrieval_{experiment}_{iteration}',
                   '--query_time', 1, '--context_code_path', code, '--context_test_path', current,
                   '--out_dir', draft_dir, '--template_file',
                   ROOT / 'data/prompt_templates/prompt_with_code_and_tests.json',
                   '--model', model, '--temperature', 0.0, env=env)
        check_files(draft_dir, rows, '_n1.txt')
        run_module('scripts.test_retrieval.retrieve_test', *common,
                   '--gen_test_dir', draft_dir, '--output_dir', similarity,
                   '--injection_path', current, '--tree_path', trees,
                   '--keywords_path', keywords, env=env)
        check_files(similarity, rows, '/semantic.csv')
        run_module('scripts.test_retrieval.rerank', *common,
                   '--output_related_tests_path', next_tests,
                   '--message_dir', tests / f'messages/rerank_{iteration}',
                   '--test_similarity_dir', similarity, '--last_related_tests_path', current,
                   '--model', model, env=env)
        check_json(next_tests, rows, 'tests')


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def repo_argument(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value) or any(
            part in ('.', '..') for part in value.split('/')):
        raise argparse.ArgumentTypeError('must be an exact owner/name')
    return value


def model_argument(value):
    if not value.strip() or value in ('.', '..'):
        raise argparse.ArgumentTypeError('must be a model name')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('code', 'test', 'all'), default='all')
    parser.add_argument('--benchmark', '--dataset', type=benchmark_argument, default='swt-verified',
                        help='swt-verified (default) or lite; folder names and full dataset IDs also work.')
    parser.add_argument('--model', required=True, type=model_argument, help='Model name configured in scripts/config.py.')
    parser.add_argument('--repo', required=True, type=repo_argument, action='append', help='Exact repository owner/name; repeat for multiple repositories.')
    parser.add_argument('--dataset-csv', type=Path, help='Override the selected benchmark\'s local CSV.')
    parser.add_argument('--output-root', type=Path, default=DEFAULT_OUTPUT,
                        help='Base retrieval folder; benchmark/model/artifact folders are added automatically.')
    parser.add_argument('--max-workers', type=positive_int, default=1)
    parser.add_argument('--iterations', type=positive_int, default=3)
    parser.add_argument('--provider', help='Optional OpenRouter provider slug; omit to use normal routing.')
    parser.add_argument('--max-tokens', type=positive_int, help='Optional completion-token limit for all LLM stages.')
    parser.add_argument('--timeout', type=positive_int, help='Optional timeout in seconds for all LLM stages.')
    parser.add_argument('--preflight-only', action='store_true', help='Validate inputs/environment without API calls.')
    args = parser.parse_args(argv)
    benchmark = BENCHMARKS[args.benchmark]
    args.dataset_csv = args.dataset_csv or ROOT / 'data' / benchmark.folder / 'test.csv'
    if not args.dataset_csv.is_file():
        if args.benchmark != 'swt-verified':
            parser.error(f'Missing {args.dataset_csv}; provide a SWE-bench Lite test CSV with --dataset-csv.')
        if args.preflight_only:
            parser.error(f'Missing {args.dataset_csv}; run python -m scripts.export_swt_verified')
        run_module('scripts.export_swt_verified', '--output', args.dataset_csv.resolve())
    selections = {repo: select_rows(args.dataset_csv, repo, args.benchmark) for repo in dict.fromkeys(args.repo)}
    all_rows = [row for rows in selections.values() for row in rows]
    print(f'Model: {args.model}; endpoint: {BASE_URL.get(args.model)}; provider: {args.provider or "default routing"}', flush=True)
    for repo, rows in selections.items():
        print(f'{repo}: {len(rows)} {benchmark.folder} instances', flush=True)
    preflight(all_rows, args.model)
    if args.preflight_only:
        print('Preflight passed; no API calls were made.')
        return
    for repo, rows in selections.items():
        paths = artifact_paths(args.output_root, args.benchmark, args.model, repo)
        manifest_path = paths.manifest
        manifest = {
            'benchmark': benchmark.folder, 'model': args.model, 'provider': args.provider,
            'source_dataset': benchmark.source_dataset,
            'base_url': BASE_URL[args.model], 'repo': repo, 'instance_ids': [r['instance_id'] for r in rows],
            'iterations': args.iterations, 'max_workers': args.max_workers,
            'max_tokens': args.max_tokens, 'timeout': args.timeout, 'reasoning': 'model default',
        }
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding='utf-8'))
            for key in ('benchmark', 'model', 'provider', 'source_dataset', 'base_url', 'repo', 'instance_ids', 'max_tokens', 'timeout'):
                if previous.get(key) != manifest[key]:
                    raise RuntimeError(f'Run configuration changed ({key}); use a separate --output-root.')
        selected_csv, env = prepare_selection(paths.selection, rows)
        for name, value in (('ICORE_LLM_PROVIDER', args.provider),
                            ('ICORE_LLM_MAX_TOKENS', args.max_tokens),
                            ('ICORE_LLM_TIMEOUT', args.timeout)):
            # CLI options are authoritative; do not inherit another run's settings.
            env.pop(name, None)
            if value is not None:
                env[name] = str(value)
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        if args.stage in ('code', 'all'):
            code_retrieval(paths, rows, selected_csv, env, args.max_workers, args.model)
        if args.stage in ('test', 'all'):
            test_retrieval(paths, rows, selected_csv, env, args.max_workers, args.iterations, args.model)
        print(f'{repo}: {args.stage} retrieval complete. Outputs: {paths.model_root}', flush=True)


def cli():
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == '__main__':
    cli()
