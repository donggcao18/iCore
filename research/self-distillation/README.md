# Issue-only teacher rollout pilot

This pilot measures whether a root-informed model can produce a useful investigation starting from the original bug report. The default teacher gets a root-function candidate list during generation. Neither the prompt nor the tools supply a designated entry, a new regression test, a patch, or a fixed revision. Existing tests in the buggy commit are searchable like other Python files.

RepoNavigator reports that it provides the exact entry file and function to every evaluation baseline. It describes the training entry as normally a testing function. This is a shared starting condition in the paper. The open question is how much localization depends on that starting information when the agent must otherwise find its own entry from the issue. A successful teacher rollout establishes feasibility for demonstration generation. Measuring the effect on localization requires a root-hidden comparison, and measuring the effect of training requires held-out student evaluation. [RepoNavigator, Appendices B and F](https://arxiv.org/html/2512.20957v6)

## Run one teacher case

Run from the iCoRe root with Python 3.10+, Git, the local case checkout, and the OpenAI Python SDK. `--check` validates the commit and root labels locally. The default model ID is OpenRouter's `nvidia/nemotron-3.5-lightning:free`. [OpenRouter model](https://openrouter.ai/nvidia/nemotron-3.5-lightning:free)

```powershell
python -m pip install openai
$env:TEACHER_BASE_URL = 'https://openrouter.ai/api/v1'
$env:TEACHER_API_KEY = Read-Host 'OpenRouter API key' -MaskInput
python research/self-distillation/run_teacher.py --model 'nvidia/nemotron-3.5-lightning:free' --instance-id pylint-dev__pylint-6386
```

`-MaskInput` is available in PowerShell 7.2 and later. If your key is already set as `OPENROUTER_API_KEY`, add `--api-key-env OPENROUTER_API_KEY`. The runner sends text chat completions and parses one JSON action at a time. The defaults allow 12 turns including the final submission and 4,096 generated tokens per turn. For a self-hosted model, override the model and endpoint. The optional `--disable-thinking` switch uses a vLLM or SGLang chat template setting. [OpenRouter Quickstart](https://openrouter.ai/docs/quickstart)

On Windows, the runner recognizes the missing `ssl/cacert.pem` path set by some Conda Bash hooks and uses the same environment's `Library/ssl/cacert.pem` when available. In Git Bash you can also use the installed certifi bundle by running `export SSL_CERT_FILE="$(python -m certifi)"`.

The default cohort uses original issue text from the local normalized Verified CSV and function-level candidates drawn from the existing iCoRe annotations. For 4970, the rcfile issue follows `SimilarChecker.close` into `Similar._compute_sims`, so those functions are the issue-only scoring candidates. The E2R regression test follows the standalone `--duplicates=0` route. `--check` prints a lexical flag for reports that mention a root function name or file. Review these flags before scoring cases where the report itself gives useful location information. If the local clones are missing, the existing `python -m scripts.clone_pylint_verified` command prepares them. [iCoRe known-endpoint annotations](../diagnostic_path/pylint_known_endpoints_inputs.jsonl)

## Run all six Pylint cases on a GPU

Run from the iCoRe root on the GPU server with the same PyTorch and Transformers environment that ran `test_qwen.py`. Local mode loads the model once on the first visible CUDA GPU. With `--instance-id` omitted, it runs the six bundled cases in teacher mode. The runner writes each completed rollout to the usual JSONL output.

```bash
python research/self-distillation/run_teacher.py --local --model 'Qwen/Qwen2.5-Coder-7B-Instruct'
```

For each case, the runner accepts a Git clone at `repos/<instance_id>` or `repos/<instance_id>/pylint`. It checks the selected commits before loading Qwen. If a checkout is missing its pinned commit, prepare a fresh directory and point the runner at it.

```bash
python -m scripts.clone_pylint_verified --output-dir "$PWD/research/self-distillation/runs/pylint-checkouts"
python research/self-distillation/run_teacher.py --local --model 'Qwen/Qwen2.5-Coder-7B-Instruct' --repos-dir "$PWD/research/self-distillation/runs/pylint-checkouts"
```

## Tools and scoring

The pilot implements the classic SWE-agent search and windowed viewer commands as JSON actions in the local runner. The viewer keeps an open file and shows up to 100 numbered lines. The pilot adapts the search bundles to tracked Python files, case-sensitive literal content search, and evidence checks. Its `submit` command records a localization diagnosis. [SWE-agent search bundle](https://github.com/SWE-agent/SWE-agent/blob/main/tools/search/config.yaml) · [SWE-agent windowed bundle](https://github.com/SWE-agent/SWE-agent/blob/main/tools/windowed/config.yaml)

| Tool | Action |
| --- | --- |
| `find_file` | Finds files by basename or glob in the repository or a known directory. |
| `search_dir` | Finds files containing a term and returns match counts and up to three matching line numbers per file. |
| `search_file` | Shows matching lines in a known file or the currently open file. |
| `open` | Takes a known file path and shows a window near its latest search match, at the supplied entry, or at the top. |
| `scroll_up`, `scroll_down` | Moves the window in the open file. |
| `submit` | Ranks inspected functions by qualified name and cites source windows for a short diagnosis. |

The runner reads immutable Git blobs from the pinned commit. Search terms require a citation to the issue, supplied entry, or a previous observation. `find_file` also accepts the general patterns `*` and `*.py`. The viewer checks that the path was revealed and uses the first match in the latest relevant search result to choose its window. The window cites the search result in the evidence chain. A file that fits in one window is shown in full.

When the issue supplies a usable literal, the initial input includes a valid search example drawn from the issue. Both teacher and blind runs get the same example. Rejected actions count toward the turn budget and stay in the run log. The next model call receives the rejection message alongside the last valid observation.

For Pylint `6386`, `search_dir` for `--verbose` finds `pylint/config/utils.py`. Opening that path shows the matching code and the surrounding option preprocessing logic.

Each completed rollout is flushed to `research/self-distillation/runs/<timestamp>.jsonl`, or to `--output`. A row contains the actions, bounded tool responses, root-read chains, ranked diagnosis, and metrics. The CLI prints condition totals for root reads, top-1, top-5, MRR, and rejected actions. A `review_candidate` has a correct top-ranked function and zero rejected actions. Root labels and scoring fields are held in the experiment logs, so build any eventual training inputs from the public issue and reviewed dialogue fields.

For the first batch, review each candidate for three things.

1. The first query is a reasonable choice given the reported symptom.
2. Each later lookup follows a clue visible in the cited observation. For a broad query, check whether the selected hit was a plausible choice without the root hint.
3. The cited source supports the proposed condition or state change and explains the reported failure. Mark a gap when a relation needs runtime evidence.

Report the reviewed yield over all attempted cases alongside candidate yield. A correct root printed in the final answer counts only after the agent has read that function. The bundled labels are candidates for this engineering pilot. Use independently verified roots and diagnosis references for research conclusions.

## Matched controls

To measure the value of the root hint for producing teacher trajectories, run both conditions with the same model, cases, and turn budget.

```powershell
python research/self-distillation/run_teacher.py --conditions teacher blind --instance-id pylint-dev__pylint-6386 --repeats 3
```

For a supplied-entry ablation, use `--cases` with a JSONL manifest. Relative checkout paths are resolved from the manifest directory. Roots and the optional entry must name real functions at the pinned commit. The issue should be available without consulting the root, patch, or gold test. Here is the row format.

```json
{"instance_id":"example-1","checkout":"./repos/example","commit":"0123456789012345678901234567890123456789","issue":"Enabling sticky mode returns zero instead of one.","roots":["worker.py::compute"],"entry":"tests/test_worker.py::test_sticky_mode"}
```

| Condition | Prompt contents |
| --- | --- |
| `teacher` | Issue and private root hint |
| `blind` | Issue |
| `entry-teacher` | Issue, supplied entry, and private root hint |
| `entry-blind` | Issue and supplied entry |

Compare `blind` with `entry-blind` on the same cases to estimate the contribution of supplying the entry under this scaffold. The built-in cases omit entry labels because their designated regression tests come from a test patch. A custom entry must exist in the exact tree available to both conditions. Report cases where the entry is itself the root. A direct RepoNavigator comparison also needs its agent and tool scaffold on the same entry ablation, ideally with the same evaluation cases and budget.

```powershell
python research/self-distillation/run_teacher.py --cases cases.jsonl --conditions blind entry-blind teacher entry-teacher --check
python research/self-distillation/run_teacher.py --cases cases.jsonl --conditions blind entry-blind teacher entry-teacher
python -m unittest discover -s research/self-distillation -v
```
