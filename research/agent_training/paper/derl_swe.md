# DeRL-SWE: Decoupled Reinforcement Learning with Nested Credit Assignment for Software Engineering

**Authors** Fuxiang Zhang, Xinyu Guan, Jujie He, Liang Zeng, Jiacheng Xu, Yang Liu, and Bo An.  
**Venue** ICML 2026 Workshop on Deep Learning for Code. [Workshop program](https://dl4c.github.io/papers/) · [Author listing](https://fuxiang-zhang.github.io/reading/)

## Task

One Qwen3-32B policy first selects files from the repository index, then reads those files and writes a search-and-replace patch. Training uses Group Relative Policy Optimization (GRPO) over the two calls in this Agentless pipeline. [Paper, §§3–4](https://openreview.net/pdf/7d0f97abf3ef6b2cbc72f267a3244313e61cf584.pdf#page=3)

## Training

1. **Repair with oracle localization.** Supply the files changed by the reference patch. Sample 16 patches per issue and reward each patch that passes the training suite.
2. **Joint localization and repair.** Sample six localizations and six repairs per unique file set. Each patch receives its binary test result, and each localization receives the mean result of its repairs. GRPO compares localizations within the issue and repairs within the same localization.

The authors show that this nested average gives an unbiased Monte Carlo estimate for the expected-return policy-gradient decomposition. The repair stage runs for about 300 optimizer steps and the joint stage for about 200. The roughly 3,600 training issues come from SWE-bench-extra, SWE-Gym, and SWE-Fixer-Train-110K after execution and difficulty filtering. [Paper, §§4–5 and Appendix B](https://openreview.net/pdf/7d0f97abf3ef6b2cbc72f267a3244313e61cf584.pdf#page=4)

## Results

- **47.4%** on SWE-bench Verified with one submitted patch. With multiple candidates ranked by externally generated proxy tests, the score rose to **61.8%**.
- In a matched localization-reward ablation, nested credit reached 44.4% at step 150, 3.3 percentage points above rewarding file-set F1. The final model also matched or exceeded the Qwen3-32B base on all six reported general benchmarks. [Paper, §§5.2–5.4 and Appendix C](https://openreview.net/pdf/7d0f97abf3ef6b2cbc72f267a3244313e61cf584.pdf#page=6)

## Relevance to agent training

Localization gets credit for giving the current repair policy useful context, so alternative file sets can earn credit when they lead to passing patches. This signal depends on the repair policy, test coverage, and the number of repair rollouts. Test generation is delegated to an external model for multi-candidate selection, and the reported localization is at file granularity. [Paper, §6](https://openreview.net/pdf/7d0f97abf3ef6b2cbc72f267a3244313e61cf584.pdf#page=8)
