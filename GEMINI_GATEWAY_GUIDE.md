# Run iCoRe with the Gemini team gateway

The gateway uses the OpenAI-compatible API, so the existing `openai` dependency
is sufficient. No Google SDK or LiteLLM Python package is needed.

`scripts/config.py` registers these gateway model IDs:

- `gemini-3.1-flash-lite`
- `gemini-3.5-flash-lite`
- `gemini-3.8-flash`
- `team-chat` (an alias whose underlying model is chosen by the gateway)

Each reads `LITELLM_API_KEY` and `LITELLM_BASE_URL`. Model IDs are sent unchanged
in keyword extraction, initial test retrieval, reranking, draft generation, and
final BRT generation. Choose a Gemini ID explicitly to select that model.

## Set credentials

In the Bash shell on your Linux/Conda benchmark machine, from the checkout root:

```bash
conda activate icore
export LITELLM_BASE_URL='https://ranges-isa-disabled-milton.trycloudflare.com/v1'
read -r -s -p 'LiteLLM team API key: ' LITELLM_API_KEY
echo
export LITELLM_API_KEY
export MODEL='gemini-3.5-flash-lite'
export REPO='pytest-dev/pytest'
export REPO_ROOT_DIR='/absolute/path/to/disposable/benchmark/repos'
```

Paste your team gateway key at the hidden prompt. Use the gateway key for
`LITELLM_API_KEY`; a Google AI Studio key is not a substitute for this gateway's
credentials. You do not need to change `OPENAI_API_KEY` or `QWEN_API_KEY`.

Replace `REPO_ROOT_DIR` with the directory containing the benchmark clones
(for example, `/home/you/iCoRe/repos`, containing `pytest/`). The full benchmark
workflow uses the existing Linux/Bash/Conda environment.

To set credentials in PowerShell for Python commands launched from that shell:

```powershell
$env:LITELLM_BASE_URL = 'https://ranges-isa-disabled-milton.trycloudflare.com/v1'
$teamKey = Read-Host 'LiteLLM team API key' -AsSecureString
$env:LITELLM_API_KEY = [System.Net.NetworkCredential]::new('', $teamKey).Password
$env:MODEL = 'gemini-3.5-flash-lite'
```

These assignments last for the current shell and its child processes. Set them
in the actual shell that launches the pipeline, including an SSH or WSL shell.
The project does not automatically load `.env` files. Keep the real key out of
tracked files. Credentials are read when Python starts; restart running commands
after changing them.

If the tunnel URL changes, update only `LITELLM_BASE_URL`, keeping the `/v1`
suffix. The supplied URL is also the config's fallback when this variable is
unset. Use the plain URL without Markdown brackets or asterisks.

## Prepare the benchmark environments

If dependencies and benchmark environments are not already prepared:

```bash
python -m pip install -r requirements.txt
bash scripts/launchers/setup_swt_verified.sh
```

The setup launcher prepares both Pylint and pytest using the normalized SWT
Verified CSV. Existing prepared environments can be reused across models.

## Run the complete pipeline

With the variables above exported, run:

```bash
bash scripts/launchers/run_retrieval_brt_swt_verified.sh
```

The exported `MODEL` overrides the launcher's DeepSeek default for both runners.
This runs production-code retrieval, initial test retrieval, three test
refinement rounds with generated drafts, final BRT generation, and buggy/fixed
evaluation. The default repository is all 15 Verified `pytest-dev/pytest`
instances, with one final candidate per instance.

Outputs for the selected Gemini model are under:

```text
retrieval_results/swt-bench-verified/gemini-3.5-flash-lite/
  code/pytest-dev/pytest/
  test/pytest-dev/pytest/
  brt/pytest-dev/pytest/i3_s1/
```

Final evaluation writes `execution_results.json` and `summary.json` in the BRT
directory. Existing caches retain the pipeline's resume behavior. The evaluator
resets and cleans the disposable benchmark clones; preserve local changes there
before starting, and run only one workflow per clone at a time.

To change models or candidate counts:

```bash
MODEL=gemini-3.8-flash SAMPLES=10 \
  bash scripts/launchers/run_retrieval_brt_swt_verified.sh
```

Use the same model when resuming retrieval or generation. Model-specific output
directories keep Gemini runs separate from existing model runs.

The ordinary chat examples use `stream=false`; initial test retrieval and
reranking use `stream=true` and function tools, including a forced `list_root`
call. The gateway must support those requests for the complete pipeline.
No gateway compatibility test or benchmark run is included in this change.
