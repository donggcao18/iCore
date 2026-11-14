# A²Agent: Action-Aware Reinforcement Learning for Repository-Level Code Localization Agents

**Authors:** Doyeon Kim, Suyoung Bae, Yumin Lee, and Jee-Hyong Lee. **Venue:** EMNLP 2026.

Sources: [paper](https://arxiv.org/html/2608.29831v1), [official implementation](https://github.com/donian00/A2Agent), [training guide](https://github.com/donian00/A2Agent/blob/main/README.md). Source-based reading summary; experiments not reproduced.

## Problem and agent

An agent may never discover relevant code, or discover it but omit it from its final answer. A²Agent addresses both failures. Given an issue, it invokes source search, entity inspection, graph traversal, summaries, and optional commit search, then submits file/module/function locations.

## Learning signals

- **Discovery:** reward newly encountered gold locations at each granularity.
- **Final accuracy:** sum localization F1 across granularities.
- **Discovery–commitment:** reward including encountered gold locations in the final prediction.
- **Action credit:** group turns from the same issue by preceding tool-call histories, excluding the current action; compare discounted returns and combine local advantages with a trajectory-level anchor.

## Training

Teacher SFT on SWE-Gym initializes tool use. Policy rollouts then receive rewards and advantages; positive, bounded exponential advantage weights scale assistant-token likelihood loss. This is advantage-weighted learning rather than a PPO/GRPO clipped policy-gradient objective.

The paper evaluates Qwen3-4B/8B on SWE-bench Verified and Pro and reports gains over SFT and localization baselines. [Method and experiments](https://arxiv.org/html/2608.29831v1).

## Implementation details and caveats

The guide separates rollout generation, progress labeling, advantage estimation, and training. Gold labels are parsed from reference patches. Graph, BM25, and file-summary indexes support repository access. [Training guide](https://github.com/donian00/A2Agent/blob/main/README.md).

The released [progress labeler](https://github.com/donian00/A2Agent/blob/main/toolplan/data/progress_labeler.py) extracts mentions from observations, assistant text, and tool arguments. Its discovery signal should not be interpreted as proof that a function was inspected or understood.

## Relevance to our proposal — interpretation

Reuse the training organization and establish an endpoint-localization baseline before changing supervision. Then compare diagnostic SFT and diagnostic rewards with otherwise identical tools and budgets.

Reference edits are useful targets but not independently verified defect origins. Recent identical calls can also conceal different earlier evidence or runtime states. For diagnosis, distinguish mentioned, retrieved, inspected, observed, and supported claims. Intermediate location rewards alone do not measure whether the final propagation explanation is correct.
