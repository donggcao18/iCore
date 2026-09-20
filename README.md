# README
This is the replication package of paper "iCoRe: An Iterative Correlation-Aware Retriever for Bug Reproduction Test Generation".

It includes the complete pipeline for setting up the environment, retrieving relevant production and test code, and leveraging LLMs to generate bug reproduction tests.

# How to Run

1. Install Dependencies
Clone the repository and install the required Python packages:
```sh
pip install -r requirements.txt
```

Set API credentials in your shell before running retrieval or generation:

```bash
export OPENAI_API_KEY='your-api-key'
# Optional when using a custom OpenAI-compatible endpoint:
# export OPENAI_BASE_URL='https://your-endpoint/v1'
```

Both GPT-4o model names use `OPENAI_API_KEY` and `OPENAI_BASE_URL`.
For Qwen, set `QWEN_API_KEY` and `QWEN_BASE_URL`; for DeepSeek, set
`DEEPSEEK_API_KEY` and `DEEPSEEK_BASE_URL` to your hosting provider's values.
Variables are read at process startup. `.env` files are not loaded automatically.
Environment setup alone does not require an API key.

Additional models: `--model qwen/qwen3.8-27b:free` and `--model z-ai/glm-5.2:free`.
Qwen uses `QWEN_API_KEY` and `QWEN_BASE_URL`; GLM uses `GLM_API_KEY`
and `GLM_BASE_URL`. For OpenRouter, set both base URLs to
`https://openrouter.ai/api/v1` and use your OpenRouter API key for both models.
The exact `--model` value is sent unchanged for these two models in generation,
initial retrieval, and reranking; no separate model-ID setting is needed.

2. Setup the SWE-bench Environment
Initialize the environment necessary for SWE-bench tasks:
```
python -m scripts.env_setup.env_setup
```

3. Retrieve relevant context
First, retrieve the relevant production code:

```sh
bash code_retrieval.sh
```

Next, retrieve the relevant test code:

```sh
bash test_retrieval.sh
```

4. Generate Bug Reproduction Tests

```bash
python -m scripts.generator.llm_query \
    --exp_name sketch \
    --query_time 10 \
    --context_code_path ./retrieval_results/code/retrieval_results.json \
    --context_test_path ./retrieval_results/test/related_tests_4.json \
    --out_dir ./data/icore/gen_tests_gpt-4o/ \
    --template_file ./data/prompt_templates/prompt_with_code_and_tests.json \
    --model gpt-4o \
    --temperature 0.7 \
    --swt
```

# Repository Organization
The repository is organized as follows:
```
.
├── data/                  # Contains prompt templates for the LLM
├── results/               # Generated bug reproduction tests based on the retrieved context
├── retrieval_results/     # Retrieval results from iCoRe
├── scripts/               # Core code for iCoRe
│   ├── code_retrieval/    # Retrieval for relevant production code.
│   ├── test_retrieval/    # Retrieval for relevant test code.
│   ├── generator/         # The basic BRT generator.
│   ├── libro/             # A Python adaptation of the LIBRO framework.
│   └── env_setup/         # Environment configuration scripts for SWT-bench/TDD-bench.
├── code_retrieval.sh      # Shell script to trigger production code retrieval
├── test_retrieval.sh      # Shell script to trigger test code retrieval
└── requirements.txt       # Python dependencies
```
