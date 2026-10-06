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
`retrieval_results/test/oracle/lite/pylint/` and contains:

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

For a local Pylint-only generation run, install the project requirements and
provide a model API key. Then use a unique experiment name for each context:

```sh
python -m scripts.generator.llm_query \
  --dataset_csv data/swe-bench-lite/test.csv \
  --repo pylint-dev/pylint \
  --exp_name oracle_pylint_base \
  --query_time 1 \
  --context_code_path retrieval_results/code/oracle/lite/pylint/code_retrieval_oracle_base.json \
  --context_test_path retrieval_results/test/oracle/lite/pylint/related_tests_oracle_base.json \
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
