# SLIT: Execution-Grounded Small Language Models for Issue-Reproducing Test Generation

**Authors** Runlin Liu, Zhe Zhang, Zeren Chen, Xiang Gao, Lu Sheng, Ping Yang, Mu Li, and Hailong Sun.  
**Venue** EMNLP 2026. [ACL Anthology](https://aclanthology.org/2026.emnlp-main.945/) · [Code and models](https://github.com/SLIT20/SLIT)

## Task

Given a bug report and the buggy checkout, generate a unit test that fails on that checkout and passes after the reference fix. SLIT trains a test generator and a separate candidate scorer. [Paper, §3.1](https://aclanthology.org/2026.emnlp-main.945.pdf#page=3)

## Training and inference

- **Generator.** Group Relative Policy Optimization (GRPO) uses a binary fail-to-pass reward plus half the sum of two trace scores. One is a recall-weighted F2 score over statement and branch coverage after removing coverage shared by all candidates and the reference test. The other compares normalized statement execution counts within functions touched by the reference fix.
- **Scorer.** Pairwise training ranks fail-to-pass tests above fail-on-both tests for the same issue, using test code and buggy-side execution logs. Both models start from Qwen2.5-Coder-14B-Instruct. The generator uses 6,000 SWE-smith issues from 97 repositories disjoint from the evaluation repositories, and its rollouts supply 60,000 preference pairs.
- **Inference.** Retrieve relevant code, vary the subsets of functions shown to the generator, and add repaired test candidates. Run candidates on the buggy checkout, discard passing tests, and use the scorer to choose one. The reference fix is available for training and benchmark scoring. [Paper, §§3.2–4.1](https://aclanthology.org/2026.emnlp-main.945.pdf#page=3)

## Results

| SWT-Bench split | Fail-to-pass rate | Change coverage |
| --- | ---: | ---: |
| Verified | 46.2% | 46.4% |
| Lite | 44.2% | 46.5% |

Under the same inference workflow on Verified, the base generator scored 28.4%, supervised fine-tuning scored 34.6%, and fail-to-pass-only RL scored 42.5%. For the separate ranking task, execution logs alone gave 85.0% pairwise accuracy, test code alone gave 71.67%, and combining them gave 92.33%. [Paper, Table 1 and Figures 2 and 6](https://aclanthology.org/2026.emnlp-main.945.pdf#page=6)

## Relevance to agent training

A reproduction policy can get partial credit before it produces a correct assertion, and logs can help separate plausible reproductions from unrelated failures. Coverage and execution counts capture where code ran and how often. Establishing the failure mechanism still requires checking what the test actually asserts and why it fails. [Paper, §6](https://aclanthology.org/2026.emnlp-main.945.pdf#page=9)
