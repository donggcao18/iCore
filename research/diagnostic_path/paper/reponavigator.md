# One Tool Is Enough: Reinforcement Learning of LLM Agents for Repository-Level Code Navigation

**Method:** RepoNavigator. **Authors:** Zhaoxi Zhang et al. **Venue:** ICML 2026.

Sources: [paper, arXiv v6](https://arxiv.org/html/2512.20957v6), [published record](https://proceedings.mlr.press/v306/zhang26an.html). Source-based reading summary; experiments not reproduced.

## Problem and method

The task is predicting files/functions requiring modification from an issue and repository. RepoNavigator supplies one tool, `jump`, which uses a language server to resolve a referenced symbol to its definition and return source context. The agent repeatedly selects symbols to inspect, then submits locations.

```text
Issue + supplied entry function
  → choose referenced symbol → jump to definition
  → inspect and repeat → submit file/function locations
```

## Training and reward

GRPO trains the policy on filtered SWE-smith instances without teacher SFT. Its reward combines localization Dice/F1 against reference-patch locations and successful tool-call rate. One trajectory-relative advantage is assigned across its actions; diagnostic-edge correctness is not explicitly rewarded.

## Controlled setting and findings

Appendix B supplies an entry file/function to the method and baselines, explicitly departing from standard SWE-bench input. A test function appears in the example. The exact “first FAIL_TO_PASS test” selection rule was not verified in this version.

The authors report improved localization after RL and favorable results for the single-tool configuration in their ablation.

## Interpretation and relevance to our proposal

**Our interpretation:** a known starting point makes a useful first controlled experiment. It isolates navigation from starting-point retrieval. Use a confirmed runnable failing case when execution evidence is required; a test identifier alone may not specify its failing inputs.

LSP resolution supplies navigation evidence, not proof of runtime execution or causal relevance. Our evaluation should distinguish successful endpoint localization, executed navigation, and a supported failure mechanism. Do not label the agent's jump history as gold diagnosis automatically.

**Potential reuse:** a small navigation action space and an endpoint-trained baseline. Compare it with diagnostic supervision under identical entry points, source access, and budgets. Report entry-conditioned results separately from issue-only localization.
