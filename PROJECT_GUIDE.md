# iCoRe: Architecture, Pipeline, and BRT Evaluation

This guide explains how the current source retrieves context, generates bug reproduction tests (BRTs), and evaluates them. **The full pipeline has not been run in this workspace.** The local SWE-bench Lite CSV exports were verified separately. The execution recipe below requires the setup and corrections in Section 5.

## 1. What iCoRe does

A BRT asserts the correct behavior described in a bug report. It should fail because of the bug on the original code and pass after the known production fix.

```text
Bug report + repository at buggy commit
    -> retrieve production code and initial reference tests
    -> generate draft test
    -> compare draft with existing tests; rerank references
    -> repeat retrieval refinement
    -> generate final candidates
    -> run each candidate on buggy and fixed code
    -> rank candidates and export a generated-test patch
```

The iterative loop improves **reference-test context** using the draft's text and static call structure. Production-code retrieval happens before the loop; buggy/fixed execution happens afterward.

### Retrieval to generation: follow one issue through the code

All stages join their artifacts by `instance_id`. For an issue such as `astropy__astropy-12907`, the sequence below explains how its report becomes a generated Python test. The commands in Section 6 implement this sequence after the corrections in Section 5.

**1. Find relevant production code.** [`extract_keywords()`](scripts/code_retrieval/extract_keywords.py) sends `problem_statement` to an LLM and saves identifiers such as function/class names. Separately, [`get_graph_info()`](scripts/code_retrieval/repo_graph/graph.py) indexes source definitions at the buggy commit. [`retrieve_keywords()` and `filter_retrieval_results()`](scripts/code_retrieval/retrieval.py) resolve and rank the identifiers against that graph. The output, `retrieval_results.json`, contains the selected **source snippets**, not just filenames.

**2. Find initial existing tests.** [`initial_retrieval.chat_with_llm()`](scripts/test_retrieval/initial_retrieval.py) gives the LLM the bug report and repository exploration tools: list directories, inspect definitions, and read functions. It selects up to five reference tests. Their paths, names, and source are saved in `related_tests_1.json`. These are existing tests to learn imports, fixtures, and usage from; they are not yet the new BRT.

**3. Turn both retrieval outputs into a generation prompt.** In [`make_messages_from_dataset()`](scripts/generator/llm_query.py), the two CLI paths select the context for this issue:

| Argument | Helper in `generator/make_prompt_util.py` | Prompt content |
|---|---|---|
| `--context_code_path` | `get_retrieval_docs()` | Formats selected production snippets with symbol names and file paths |
| `--context_test_path` | `get_related_test()` | Formats selected existing tests with names, paths, and code |

The helpers read the stored `code_content` fields. The [combined template](data/prompt_templates/prompt_with_code_and_tests.json) becomes:

```text
System: instructions from prompt_with_code_and_test.txt

User:
<issue>the issue's problem_statement</issue>
<code>retrieved production-code snippets</code>
<test>retrieved existing-test snippets</test>

Please generate a failing test case that reproduces the issue described above.
The test must fail naturally due to the bug.
```

The system instructions require expected-behavior assertions so the test can pass after the fix. The known production fix and benchmark reference-test patch are not inserted into this prompt.

**4. Generate a draft.** [`query_llm_for_gentest()`](scripts/generator/llm_query.py) builds those messages and calls [`query_llm()` → `query_chat_llm()`](scripts/utils/llm_api.py), which sends them to the model. It extracts the response's Python code from optional reasoning text, `<result>` tags, and code fences. `query_times()` saves it as `<instance_id>_n1.txt`. During refinement, `--query_time 1 --temperature 0.0` requests one draft. At this point the saved file contains a candidate, not a verified reproduction.

**5. Use the draft to improve reference retrieval.** [`retrieve_test.retrieval()`](scripts/test_retrieval/retrieve_test.py) treats the generated draft as the search query: compare its names/code with existing tests, temporarily inject it to build its static call tree, and compare that tree with precomputed existing-test trees. It writes `semantic.csv`, `call.csv`, and `score.csv`. [`rerank.list_candidates()` and `chat_with_llm()`](scripts/test_retrieval/rerank.py) then give the bug report, candidate test code, and previous references to the LLM. Its new selection is saved as `related_tests_2.json`.

The next generator call uses the **same issue and production context, but the updated reference tests**. The earlier draft drives retrieval; it is not automatically included as a previous assistant response in the next generation prompt. This loop uses similarity and reranking, not buggy/fixed test execution feedback.

```text
issue + production code + references_1 -> draft_1 -> retrieve/rerank -> references_2
issue + production code + references_2 -> draft_2 -> retrieve/rerank -> references_3
issue + production code + references_3 -> draft_3 -> retrieve/rerank -> references_4
```

**6. Generate final candidates.** Call the same `generator.llm_query` module with the final references. Section 6 uses `related_tests_4.json`, `--query_time 10`, and `--temperature 0.7`: up to ten separately sampled candidates from the final context, not ten additional retrieval rounds. These files then enter the injection and buggy/fixed evaluation described in Section 3.

Use distinct experiment names and message directories for refinement rounds: cached prompts/conversations can otherwise reuse old context instead of incorporating the new references.

### Benchmark inputs

| Field | Role |
|---|---|
| `instance_id` | Identifies the issue across all artifacts |
| `repo` | Target repository, such as `astropy/astropy` |
| `base_commit` | Exact buggy source revision |
| `problem_statement` | Bug report supplied to the generator |
| `version`, `environment_setup_commit` | Historical dependency/environment metadata |
| `patch` | Known production fix used by the evaluator |
| `test_patch` | Benchmark reference-test changes; not your generated test |
| `FAIL_TO_PASS`, `PASS_TO_PASS` | JSON-encoded reference-test names, not test implementations |

Give the generator the report and relevant **buggy-revision** source/tests. Keep `patch`, `test_patch`, and reference evaluation labels outside its prompt/retrieval context. For a GitHub issue outside SWE-bench, identify the buggy commit and applicable production fix yourself; the evaluator does not derive them from an issue URL.

For an instance-based introduction, see [the local dataset guide](data/swe-bench-lite/README.md). That folder contains only the guide and the 300-row test / 23-row development CSVs.

## 2. Module map

Paths below are relative to `scripts/`. Package `__init__.py` files establish imports and do not implement stages.

| Module | Main responsibility |
|---|---|
| `config.py` | Repository root, experiment root, Conda naming, API keys/endpoints |
| `env_setup/env_setup.py` | Clone benchmark repositories and execute generated Conda setup scripts |
| `env_setup/exec_spec.py` | Convert a benchmark row into an `ExecSpec` and dependency-installation commands |
| `env_setup/constants.py` | Repository/version installation recipes, test runners, and benchmark types |
| `env_setup/utils.py` | Historical dependency-file retrieval, patch metadata, logging, Unix file locking |
| `code_retrieval/repo_graph/search_utils.py` | AST-based discovery of classes, functions, signatures, and globals |
| `code_retrieval/repo_graph/graph.py` | Build/save repository containment graphs and resolve symbol names |
| `code_retrieval/extract_keywords.py` | Ask an LLM for actionable identifiers from the report |
| `code_retrieval/retrieval.py` | Match identifiers to graph nodes and select production-code snippets |
| `code_retrieval/utils.py` | Supporting patch, repository-context, import, and file-discovery utilities |
| `test_retrieval/function_calls.py` | LLM tools for listing directories/definitions and reading functions |
| `test_retrieval/initial_retrieval.py` | Explore existing tests with an LLM and select initial references |
| `test_retrieval/similarities/get_all_cg_parallel.py` | Precompute existing test call trees and function document frequencies |
| `test_retrieval/similarities/textual_similarity.py` | Token normalization and BM25 similarity of test names/code |
| `test_retrieval/similarities/tree_edit_distance.py` | Weighted call-tree construction and tree-edit distance |
| `test_retrieval/similarities/call_tree_similarity.py` | Inject a draft, build its tree, and compare with stored test trees |
| `test_retrieval/retrieve_test.py` | Combine textual/structural candidate retrieval and write rankings |
| `test_retrieval/rerank.py` | Let an LLM select references from retrieved and previous candidates |
| `test_retrieval/utils.py` | `Test` container, score serialization, normalization, reference loading |
| `generator/make_prompt_util.py` | Format retrieved code/tests and optional baseline context |
| `generator/llm_query.py` | Assemble prompts, call the model, extract candidate Python code |
| `libro/postprocess_swe.py` | Inject candidates, execute buggy/fixed runs, record results |
| `libro/parse_bug_report.py` | Extract report traceback and exception features |
| `libro/process_failure_output.py` | Parse/normalize failed-test output |
| `libro/selection_and_ranking.py` | Cluster generated tests and rank using failure/report features |
| `libro/output_patch.py` | Inject the top-ranked candidate and export its test diff |
| `utils/llm_api.py` | Model alias mapping and OpenAI-compatible generation calls |
| `utils/swe_util.py` | Project source/test paths, environment names, install/test commands |
| `utils/git_utils.py` | Reset/clean repositories, apply fixes, extract test-file diffs |
| `utils/common.py` | Resolve imports, select injection files, adapt test style, process results |
| `utils/related_test_util.py` | Discover test functions/methods and extract their source/decorators |
| `utils/normalize_utils.py` | Normalize generated code and failure output for clustering |

Prompts live in `data/prompt_templates/`. The combined JSON template references `prompt_with_code_and_test.txt` (singular `test`). Other templates support code-only, test-only, or baseline context. Saved artifacts are under `retrieval_results/` and `results/`.

### How retrieval scores candidates

**Production code:** an identifier matching `m` nodes contributes `1/m` to each node and its file. A candidate's score combines node, file, and parent-node evidence. One node is kept per keyword; unmatched identifiers become `null`. `.py` filename lookup is currently unimplemented.

**Textual test similarity:** tokenize names/paths and source, split snake_case/CamelCase, lowercase and lemmatize, then combine normalized BM25 name/code scores with equal weights. The code calls this “semantic similarity,” but it does not use embeddings.

**Structural similarity:** compare static test call trees with weighted edit distance. Report keywords receive weight `1.0`; other functions receive rarity-based weights with a default minimum of `0.1`.

```text
call similarity = 1 - edit_distance / (query_tree_weight + candidate_tree_weight)
intended combined score = 0.5 * text_similarity + 0.5 * call_similarity
```

Structural comparison is limited to strong textual candidates, using the first 100 textual scores as a threshold; ties can admit more. If tree construction fails, retrieval falls back to text-only candidates. Reranking merges top textual, structural, combined, and previous-reference candidates before LLM selection. The field mismatch in Section 5 currently prevents the intended blend from working correctly.

## 3. How evaluation modifies and tests the actual repository

The main entry point is [`twover_run_experiment()`](scripts/libro/postprocess_swe.py), with Git operations implemented in [`git_utils.py`](scripts/utils/git_utils.py).

### 3.1 Locate and reset the buggy repository

`REPO_ROOT_DIR` in `config.py` points to disposable benchmark clones. For `astropy/astropy`, the expected path is `<REPO_ROOT_DIR>/astropy/`.

At the start of evaluation:

```python
git_reset_hash(repo_path, bug_report["base_commit"])
git_clean_all(repo_path)
setup_environment(proj, repo_path, env_name)
```

`git_reset_hash()` executes `git reset --hard <base_commit>`. Cleaning removes untracked and ignored files. `setup_environment()` installs/configures the target project inside its named Conda environment; that environment must already have been created.

**Use disposable clones.** Keep generated candidate files and result files outside the target clone, because reset/clean operations can delete them. Do not run simultaneous evaluations against the same shared clone.

### 3.2 Inject and run a candidate on buggy code

For each candidate, `individual_run()` calls:

```python
test_names = inject_test(proj, example_test, env_name, bug_id, pos=injection)
test_name = test_names[0]
status, failed_tests, failure_output = run_test(repo_path(proj), test_name, env_name)
```

Injection resolves imports and adapts the test to the destination's style. A retrieved-tests JSON selects the first reference's file/class; `injection="libro"` uses lexical file selection. An empty reference list also falls back to lexical selection.

`run_test()` activates Conda, sets `PYTHONPATH`, and chooses the project-specific runner through `swe_test_cmd()`: typically pytest, but Django and SymPy have their own commands. It captures output and uses a 60-second timeout. The current implementation executes the **first injected test name**, so one focused test per candidate is the clearest input.

### 3.3 What `swe.patch` does: writing versus applying

After the buggy run, iCoRe resets/cleans the repository to remove the candidate, then calls:

```python
git_apply(repo_path, bug_report["patch"])
```

Inside `git_apply()`, these are two different operations:

```python
# Operation 1: save the diff text in a file.
with open(path.join(repo_dir_path, 'swe.patch'), 'w') as f:
    f.write(patch_content)

# Operation 2: run Git, which edits the actual source files.
process = sp.run(['git', 'apply', 'swe.patch'], cwd=repo_dir_path,
                 stdout=sp.DEVNULL, stderr=sp.DEVNULL)
assert process.returncode == 0
```

**`f.write()` only creates `swe.patch`. `git apply swe.patch` reads it and changes the source files named inside the diff.** Python launches Git; Git performs the source edits. If the `sp.run(...)` call were removed, the repository's source would remain unchanged.

For instance `astropy__astropy-12907`, the dataset fix tells Git to edit `astropy/modeling/separable.py`:

```diff
-        cright[-right.shape[0]:, -right.shape[1]:] = 1
+        cright[-right.shape[0]:, -right.shape[1]:] = right
```

After successful application, the actual Python file contains the second line. `swe.patch` is an arbitrary intermediate filename, not a test file. Applying it changes the local working tree; it does not merge a branch, create a commit, or push to GitHub. Return code `0` proves application succeeded, not that tests passed.

### 3.4 Run the same candidate on fixed code, then clean up

The evaluator reinjects the same candidate source and calls `individual_run()` again. The fixed state is **`base_commit` plus dataset `patch`**, not the latest repository version. The dataset's `test_patch` is not applied in this candidate-evaluation path.

```text
base_commit + candidate                    -> buggy result
reset/clean
base_commit + known production fix + candidate -> fixed result
reset/clean                                -> ready for next candidate
```

iCoRe reuses one checkout sequentially. Both runs use the same environment name. Setup happens before the candidate loop and is not repeated after applying the fix; a fix affecting compiled components may need an explicit rebuild. Verify the test imports the intended modified checkout.

### 3.5 Interpret results

Results are stored by instance ID and candidate filename, with `buggy`, `fixed`, and `success` fields. Each run records `compile_error`, `runtime_error`, `failed_tests`, `autogen_failed`, and `fib_error_msg`. Some exceptions are stored as error strings instead.

| Buggy run | Fixed run | Interpretation |
|---|---|---|
| Fails for the reported behavior | Passes | Successful BRT |
| Passes | Passes | Bug not exposed |
| Fails | Fails | Incorrect candidate, environment problem, or unrelated failure |
| Setup/import error or no valid test execution | Any | Reproduction not established |

The raw code considers assertion, runtime, or compile failures on the buggy side when setting `success`. Later ranking applies additional filtering. Inspect the failure reason; a missing dependency is not evidence of the reported defect.

Ranking groups similar generated tests/failures and prioritizes agreement with the report, cluster size, shorter tests, and fewer assertions. Export selects the top-ranked candidate and writes its test diff; it does not rerun the test or guarantee that the selected candidate succeeded.

### 3.6 Evaluate one candidate using the local CSV

After preparing the target clone, project environment, pipeline dependencies, and Bash execution, this calls the existing evaluator directly from the iCoRe root:

```python
import csv
import json
from pathlib import Path
from scripts.libro.postprocess_swe import twover_run_experiment

csv.field_size_limit(16 * 1024 * 1024)
with Path('data/swe-bench-lite/test.csv').open(
    encoding='utf-8-sig', newline=''
) as source:
    instance = next(row for row in csv.DictReader(source)
                    if row['instance_id'] == 'astropy__astropy-12907')

candidate = Path('candidate_test.py').read_text(encoding='utf-8')
results = twover_run_experiment(instance, [candidate], injection='libro')
Path('brt_result.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
```

This example has not been executed. It bypasses the batch wrapper's dataset-list and legacy skip-file reads, but still resets the target clone and needs the historical environment. To use retrieved-reference placement, replace `'libro'` with the relevant reference-test JSON path.

## 4. Files exchanged between stages

| Artifact | Shape / consumer |
|---|---|
| Keywords JSON | `{instance_id: [identifier, ...]}`; production and structural retrieval |
| Graph pickle | `<instance_id>_graph.pkl`; production symbol lookup |
| Production retrieval JSON | `{instance_id: {keyword: node_or_null}}`; node stores name, kind, path, source span/content, parent |
| Reference-tests JSON | `{instance_id: [{file, name, code_content}, ...]}`; generation and injection; order matters |
| `call_trees.db`, `df.json` | Per-instance SQLite trees and document-frequency counts |
| Similarity CSVs | Per-instance `semantic.csv`, `call.csv`, `score.csv`; LLM reranking |
| Candidate text | `<instance_id>_n<number>.txt`; Python source for injection |
| Execution JSON | `{instance_id: {candidate_filename: result_or_error}}`; ranking |
| Ranking CSV | Ranked generated filenames and success-rank statistics; patch export |
| Patch JSONL | `{instance_id, model_name_or_path, model_patch}`; selected generated test diff |

The saved `results/patches/` collections include the main method, `rq1` baselines, `rq3` ablations, and `rq4` parameter variants. Their presence does not establish successful execution of every patch. Saved GPT/Qwen/DeepSeek retrieval artifacts have different instance coverage; check keys against the selected dataset before reusing them.

## 5. Setup and required corrections

These are **known requirements in the inspected checkout**, not fixes already made. The recipe in Section 6 assumes they have been addressed.

### Runtime and inputs

- Use Linux/WSL with Bash, Git, and Conda. `fcntl`, shell commands, and hard-coded paths make native Windows execution unsuitable as-is.
- Install `requirements.txt` in the pipeline environment and obtain NLTK WordNet (`python -m nltk.downloader wordnet`). Dependencies are unpinned; verify compatibility, especially `pyan.CallGraphVisitor(..., pid=...)`.
- Set `REPO_ROOT_DIR`, valid model credentials/endpoints, and the correct Conda paths. Several modules assume `~/miniconda3`.
- Create/populate `swt.txt` for Lite or `tdd.txt` for Verified. Many modules open both, so the unused file must also exist. Original paper subset lists are not included.
- Create `env.txt` for completed setup environments. Create output directories before writing. The batch executor also unconditionally reads `tmp_data/final_gpt_@1_acc.txt`; create an empty file or remove that unused read.
- Run from the iCoRe root. Use disposable target clones and keep candidates/results elsewhere.

### Source and launcher defects

| Location | Required correction or check |
|---|---|
| `env_setup/env_setup.py` | Currently hard-coded to Verified/TDD. Adapt selection for SWT; persist only successfully created environments to `env.txt` |
| `repo_graph/graph.py` | Worker uses undefined `GRAPH_PATH`. Pass the output directory explicitly. Filter tasks by the selected instance IDs; the current loop does not |
| `test_retrieval/utils.py` | Align `func_call_similarity` with `retrieve_test.py`'s `call_graph_similarity`, including serializer/score calculation; also align match-flag names |
| Conda subprocess calls | Specify Bash for Bash-specific command strings; `shell=True` can otherwise run `/bin/sh` |
| `code_retrieval.sh` | Unsupported/swapped graph/keyword arguments and inconsistent keyword filename; use Section 6 commands |
| `test_retrieval.sh` | Use the current iteration's draft, arithmetic `$((i + 1))`, and a separate reranking message directory per iteration |
| Call-tree builder | Pass `--proj ""` to avoid its Django-only default. Check stored qualified names match generated lookup keys |
| Structural scoring | Nondefault `p1` is not propagated consistently. Empty/equal score sets need care; verify actual structural results rather than silently accepting fallback |
| Model calls | Generation uses aliases; initial retrieval/reranking pass model names directly. Ensure the configured endpoint accepts the selected names |

## 6. Full execution sequence

**Bash commands, after Section 5 corrections.** Start with one supported test-split instance, inspect each stage, then expand. The development CSV includes projects outside iCoRe's current mappings.

### 6.1 Prepare environments and production context

```bash
set -euo pipefail
BENCH=swt
BENCH_FLAG=--swt
MODEL=gpt-4o
EXP=icore
CODE=./retrieval_results/code/retrieval_results.json
KEYWORDS=./retrieval_results/code/keywords_gpt-4o.json
TREES=./retrieval_results/swe_test_cgs

mkdir -p retrieval_results/code retrieval_results/graphs retrieval_results/test
mkdir -p "$TREES" "results/$EXP" tmp_data
touch swt.txt tdd.txt env.txt tmp_data/final_gpt_@1_acc.txt
# Populate the active instance list before continuing.

# This entry point must first be adapted to the selected benchmark.
python -m scripts.env_setup.env_setup

python -m scripts.code_retrieval.repo_graph.graph \
  --graph_path ./retrieval_results/graphs "$BENCH_FLAG"
python -m scripts.code_retrieval.extract_keywords \
  --keywords_path "$KEYWORDS" --model "$MODEL" "$BENCH_FLAG"
python -m scripts.code_retrieval.retrieval \
  --keywords_path "$KEYWORDS" --graph_dir ./retrieval_results/graphs \
  --save_path "$CODE" "$BENCH_FLAG"
```

For TDD, change both benchmark variables and prepare `tdd.txt`. Check each selected instance has a graph and usable keywords/code. Environment setup must provide the named Conda environments and compatible project installations; graph/Jedi lookup may require the target project to be installed.

### 6.2 Initialize and refine test context

```bash
python -m scripts.test_retrieval.similarities.get_all_cg_parallel \
  --output_dir "$TREES" --proj "" --max_workers 2 "$BENCH_FLAG"
python -m scripts.test_retrieval.initial_retrieval \
  --related_tests_path ./retrieval_results/test/related_tests_1.json \
  --message_path ./retrieval_results/test/messages/initial/ \
  --model "$MODEL" "$BENCH_FLAG"

for i in 1 2 3; do
  next=$((i + 1))
  draft="./data/sketch_${i}/gen_tests_${MODEL}"
  refs="./retrieval_results/test/related_tests_${i}.json"
  similarities="./retrieval_results/test/test_similarity/${i}"

  python -m scripts.generator.llm_query \
    --exp_name "sketch_${i}" --query_time 1 \
    --context_code_path "$CODE" --context_test_path "$refs" \
    --out_dir "$draft" \
    --template_file ./data/prompt_templates/prompt_with_code_and_tests.json \
    --model "$MODEL" --temperature 0.0 "$BENCH_FLAG"

  python -m scripts.test_retrieval.retrieve_test \
    --gen_test_dir "$draft" --output_dir "$similarities" \
    --injection_path "$refs" --tree_path "$TREES" \
    --keywords_path "$KEYWORDS" --topk 5 --p1 0.1 "$BENCH_FLAG"

  python -m scripts.test_retrieval.rerank \
    --output_related_tests_path "./retrieval_results/test/related_tests_${next}.json" \
    --message_dir "./retrieval_results/test/messages/iteration_${i}/" \
    --test_similarity_dir "$similarities" --last_related_tests_path "$refs" \
    --model "$MODEL" --topk 5 "$BENCH_FLAG"
done
FINAL_TESTS=./retrieval_results/test/related_tests_4.json
```

Check tree databases contain tests and each iteration produces its draft, rankings, and next reference set. Missing call/combined CSVs can indicate text-only fallback. `--wo_call` is available for a deliberate ablation.

### 6.3 Generate and execute final candidates

```bash
GEN_DIR="./data/${EXP}/gen_tests_${MODEL}"
EXEC_RESULTS="./results/${EXP}/${MODEL}.json"

python -m scripts.generator.llm_query \
  --exp_name "$EXP" --query_time 10 \
  --context_code_path "$CODE" --context_test_path "$FINAL_TESTS" \
  --out_dir "$GEN_DIR" \
  --template_file ./data/prompt_templates/prompt_with_code_and_tests.json \
  --model "$MODEL" --temperature 0.7 --save_prompt "$BENCH_FLAG"

python -m scripts.libro.postprocess_swe \
  --gen_test_dir "$GEN_DIR" --model "$MODEL" --exp_name "$EXP" \
  --injection_path "$FINAL_TESTS" --result_file "$EXEC_RESULTS" "$BENCH_FLAG"
```

API failures may leave fewer than ten candidates. Use the same reference set for generation, execution, and export so placement is consistent. The evaluator performs the repository changes explained in Section 3.

### 6.4 Prepare report features, rank, and export

Ranking needs report features for the selected IDs in `scripts/libro/bug_report_parse_results.json`. Its existing contents may cover your selection. To fill missing entries without the parser CLI's hard-coded paths:

```bash
BENCH="$BENCH" python - <<'PY'
import json
import os
from pathlib import Path
from datasets import load_dataset
from scripts.libro.parse_bug_report import BugReportParser

bench = os.environ['BENCH']
name = ('SWE-bench/SWE-bench_Lite' if bench == 'swt'
        else 'princeton-nlp/SWE-bench_Verified')
selected = set(Path(f'{bench}.txt').read_text().split())
path = Path('scripts/libro/bug_report_parse_results.json')
features = json.loads(path.read_text()) if path.exists() else {}
for row in load_dataset(name)['test']:
    key = row['instance_id']
    if key in selected and key not in features:
        features[key] = BugReportParser(row['problem_statement']).to_json()
assert not selected - set(features), 'Some selected IDs have no report features'
path.write_text(json.dumps(features, indent=2), encoding='utf-8')
PY

python -m scripts.libro.selection_and_ranking \
  --exp_name "$EXP" --model "$MODEL" --result_file "$EXEC_RESULTS" \
  --gen_test_path "$GEN_DIR" --swe "$BENCH"

python -m scripts.libro.output_patch \
  --gen_test_dir "$GEN_DIR" --model "$MODEL" --exp_name "$EXP" \
  --injection_path "$FINAL_TESTS" \
  --result_file "./results/${EXP}/${MODEL}_patch.jsonl" "$BENCH_FLAG"
```

Ranking uses `--swe swt` / `--swe tdd`, unlike the other dataset flags. Its CSV is `results/<experiment>/ranking_<experiment>_<model>.csv`. For the exporter, `--result_file` names the **output patch JSONL**, not the execution-results JSON. Use a new export filename to avoid duplicate records.

## 7. Resume behavior and validation

| Stage | Important behavior |
|---|---|
| Graphs / call trees | Existing files can trigger skipping even if stale; verify contents |
| Retrieval / reranking | Existing result IDs and conversations are reused; separate iterations and experiments |
| Generation | Existing candidate files are skipped unless `--retry`; saved prompts can also be reused by experiment/instance |
| Execution | Current wrapper skips an instance once at least five results exist, even if more candidates were requested |
| Export | Appends records; the loaded `done_ids` list is not enforced |

Use fresh paths when changing dataset, model, prompt, or settings. Some aggregate JSON files are only written after a stage's loop. Graph and call-tree builders share instance-workspace paths, so do not run them concurrently for the same instances.

Before scaling up, confirm one candidate is injected, collected, fails for the expected reason on the buggy source, and passes on the fixed source. Inspect error strings and structural fallbacks rather than relying on process completion. An exported patch alone is not proof of reproduction. No dedicated regression suite or CI was found for the original pipeline.
