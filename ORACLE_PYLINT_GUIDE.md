# Pylint oracle test context

The extractor reads the Pylint rows in `data/swe-bench-lite/test.csv` and
identifies test functions changed by the developer's `test_patch`. It clones
`pylint-dev/pylint` into `repos/pylint` on the first run. The clone is ignored
by this repository. For each row, it reads the historical `base_commit` and
applies `test_patch` in a temporary directory; it never resets the clone.

From the root of this repository, run:

```sh
python -m scripts.test_retrieval.extract_oracle --dataset lite --repo pylint-dev/pylint
```

Use `--repo-dir` to point at an existing full Pylint clone, `--csv` for another
benchmark export, `--output-dir` for another destination, or `--instance-id`
to process one ID. The output directory is
`retrieval_results/oracle/test/lite/pylint/` and contains:

- `related_tests_oracle_patched.json`: all changed test functions using the
  developer's post-patch source. This is a hindsight upper bound; it includes
  newly written BRTs and leaks their assertions into the generator.
- `related_tests_oracle_base.json`: the same selected functions when they
  already existed at `base_commit`, using their buggy-revision source. Some
  instances have an empty array because their targets were newly added.
- `oracle_manifest.json`: function names, paths, new/modified status,
  `FAIL_TO_PASS` membership, and extraction evidence.

During BRT generation, an empty oracle list supplies no test context. If all
retrieved code nodes are `null`, the prompt also omits the code context and
uses the issue plus any available oracle tests. If both are empty, it uses the
issue alone. Missing instance keys still stop the launcher to catch a wrong
input file before API calls.

Both `related_tests_*.json` files have the exact `{instance_id: [{name, file,
code_content}, ...]}` shape consumed by the BRT generator. `FAIL_TO_PASS`
tests are ordered first, followed by other functions changed in the patch.
The extractor does not cap the number of tests.

Prepare production-code oracle context first:

```sh
python -m scripts.code_retrieval.extract_oracle --dataset lite --repo pylint-dev/pylint
```

See [ORACLE_CODE_GUIDE.md](ORACLE_CODE_GUIDE.md) for production-patch selection
and extracting code together with augmented test context.

Augmented retrieval now uses `strict_direct_existing_object_v1`: production-guided
additions require an unambiguous call, class construction, constructor invocation,
or variable read in the test body, resolved to an exact changed object available
at `base_commit`. Import aliases and known receiver types are resolved. Calls to
inherited implementations use the original method identity. References to a
function/class without invoking it, module/file references, substitute callers or
classes for newly added objects, and helper/fixture/setup execution paths do not
qualify. Nested definition bodies, lambdas, deferred generators, local annotations,
test decorators, and generated source
strings do not count as body-level invocation evidence. Resolution remains static;
it does not establish branch execution or bug reproduction.

`related_tests_oracle_code_base.json` contains only strict production matches.
`related_tests_oracle_base_augmented.json` merges those matches with the original
test-patch base oracle, preserving original-test priority. Both retain the same
three-field generator schema. `--top-k` is a ceiling (default 10, 0 means uncapped),
and empty selections remain valid. `oracle_augmented_manifest.json` records each
direct call/read location, original-test provenance, rejected substitute/module
targets, and ambiguous matching uses.

```sh
python -m scripts.test_retrieval.augment_oracle --dataset lite --repo pylint-dev/pylint
python -m scripts.test_retrieval.augment_oracle --dataset swt-verified --repo pytest-dev/pytest \
  --csv data/swt-bench-verified/oracle_input_pylint_pytest.csv
```

Under the strict policy, optional indirect-candidate diagnostics are enabled
with `--include-auxiliary`; these candidates appear only in the manifest, never
in generator input. In strict mode, `--max-depth` (default 8) applies only to
these diagnostics. Strict selection has no module-proximity fallback.

To use the previous broad selection for actual retrieval, pass
`--selection-policy legacy-paths`. This follows dependency edges up to eight
steps and applies the old module-proximity fallback if no path reaches a test.
For SymPy on SWE-bench Lite, use the dedicated launcher:

```sh
bash scripts/launchers/retrieve_oracle_tests_broad.sh
```

Or invoke the module directly:

```sh
python -m scripts.test_retrieval.augment_oracle --dataset lite --repo sympy/sympy \
  --selection-policy legacy-paths --max-depth 8
```

Results go to `retrieval_results/oracle/test/lite/sympy/legacy_d8/` by default.
`--top-k 0` removes the default ten-test cap. The launcher can also select a
different dataset with `DATASET=swt-verified` if its normalized CSV contains
SymPy rows.

The separate relaxed version uses `relaxed_exact_object_paths_v1` and leaves the
strict files intact:

```sh
python -m scripts.test_retrieval.augment_oracle_relaxed \
  --dataset swt-verified --repo pytest-dev/pytest --max-depth 3
python -m scripts.test_retrieval.augment_oracle_relaxed \
  --dataset lite --repo pylint-dev/pylint --max-depth 2
```

Its defaults write into `relaxed_d3/` or `relaxed_d2/` below the corresponding
strict repository output directory. Verified Pylint/pytest runs automatically
use `data/swt-bench-verified/oracle_input_pylint_pytest.csv`, which contains the
normalized SWT tests and restored production patches. `--csv`, `--instance-id`,
`--repo-dir`, `--output-dir`, and `--top-k` are also supported. An existing output
directory with different depth, cap, or framework settings is rejected; use a
new directory for another experiment.

Every selected test must reach an exact changed object that exists at the buggy
base commit through one to three edges (or at most two with `--max-depth 2`).
Allowed edges include resolved calls, constructors, variable reads, property
getters, decorator application, explicit fixture requests, and dependencies of
those fixtures. Pytest built-in fixtures are resolved from the historical plugin
registry, and explicit `pytest_plugins` declarations are supported. Ambiguous
receivers, import-only references, same-file proximity, broad autouse/setup links,
and substitute classes/callers for newly added objects do not qualify.

A conditional pytest model also connects `pytester` execution of a generated
`unittest.TestCase` program to the runner hook and `TestCaseFunction.runtest`.
These two dispatch edges have confidence 0.8 and are labelled in the manifest;
use `--no-framework-models --output-dir <new-directory>` for an experiment with
only resolved syntax relations. Literal argument propagation prunes simple known
early exits, including CLI help paths. Other branches and dynamic dispatch remain
static approximations, so a relation is not proof that the test exercises the bug.

Candidates are ranked by path length, relation confidence, validated original
test-patch membership, similarity to new/failing gold tests, and changed-object
coverage. Gold patched snippets guide selection only; exported snippets always
come from the base commit. Unlike the strict combined file, original test-patch
candidates also need a qualifying relation in this version, so unrelated patch
refactors cannot occupy its cap. Default `--top-k 10` is a ceiling; `0` is uncapped.

Both `related_tests_oracle_code_base.json` and
`related_tests_oracle_base_augmented.json` contain the same validated ranked pool,
with the existing `{instance_id: [{name, file, code_content}, ...]}` schema.
`oracle_augmented_manifest.json` records exact paths, edge locations/confidence,
gold hint names, excluded original candidates, and rejected base-object mappings.
`run_summary.json` records input provenance and coverage. Newly added objects
with no exact base counterpart can still produce an empty selection.

To use this version with the existing Verified test-oracle BRT launcher, supply
its path and a separate experiment output root:

```sh
ORACLE=./retrieval_results/oracle/test/swt-bench-verified/pytest/relaxed_d3/related_tests_oracle_base_augmented.json \
OUTPUT_ROOT=./data/oracle_test_relaxed_d3 \
bash scripts/launchers/run_brt_oracle_test_swt_verified.sh
```

Set `REPO=pylint-dev/pylint` and the matching Pylint oracle path for that repository.

The generated depth-three run covers the same 44 Lite/Verified Pylint/pytest
instances as the strict run. It contains 187 test snippets across 24 instances.
Compared with the strict production-only pool, it recovers 15 empty instances:

| Dataset/repository | Strict production instances with tests | Relaxed instances with tests | Relaxed snippets |
| --- | ---: | ---: | ---: |
| Lite / Pylint | 1 / 6 | 3 / 6 | 23 |
| Lite / pytest | 3 / 17 | 8 / 17 | 64 |
| Verified / Pylint | 2 / 6 | 5 / 6 | 38 |
| Verified / pytest | 3 / 15 | 8 / 15 | 62 |

All exported snippets were checked against their base source, all 233 evidence
paths were checked for continuity and the three-edge bound, and SHA-256 hashes
confirmed 40 protected strict/code outputs and launchers were unchanged. The
79 affected unit tests passed. Details are in
[relaxed_d3_run_summary.json](retrieval_results/oracle/test/relaxed_d3_run_summary.json).
The four previously empty Verified cases `10051`, `10081`, `10356`, and `5631`
now have candidates. `5262` remains empty because its new property has no exact
base counterpart under this policy.

For a local Pylint-only generation run, install the project requirements and
provide a model API key. Then use a unique experiment name for each context:

```sh
python -m scripts.generator.llm_query \
  --dataset_csv data/swe-bench-lite/test.csv \
  --repo pylint-dev/pylint \
  --exp_name oracle_pylint_base \
  --query_time 1 \
  --context_code_path retrieval_results/oracle/code/lite/pylint/code_retrieval_oracle_base.json \
  --context_test_path retrieval_results/oracle/test/lite/pylint/related_tests_oracle_base.json \
  --out_dir data/oracle_pylint_base/gen_tests_gpt-4o \
  --template_file data/prompt_templates/prompt_with_code_and_tests.json \
  --model gpt-4o \
  --temperature 0.7
```

For the hindsight experiment, replace `base` with `patched` in the experiment
name, context path, and output directory. `llm_query.py` reuses prompts saved
under an experiment name, so do not reuse an experiment name when changing its
context. The code context shown here contains all six local Pylint IDs.

The extractor uses no LLM or package installation. Generated BRTs still need
the repository's normal buggy/fixed evaluation; these JSON files do not
establish that a generated test reproduces a bug.
