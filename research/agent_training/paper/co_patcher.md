# Co-PatcheR: Collaborative Software Patching with Component-specific Small Reasoning Models

**Authors** Yuheng Tang, Hongwei Li, Kaijie Zhu, Michael Yang, Yangruibo Ding, and Wenbo Guo.  
**Venue** NeurIPS 2025. [Proceedings](https://papers.neurips.cc/paper_files/paper/2025/hash/3be60cdf94716ca358cc8fbc842c3716-Abstract-Conference.html) · [Code and models](https://github.com/ucsb-mlsec/Co-PatcheR)

## Task

Turn an issue report and repository into a validated code patch. Three separately fine-tuned Qwen2.5-Coder-14B models divide localization, editing, and validation across a fixed pipeline. [Paper, §3.1 and Figure 1](https://arxiv.org/pdf/2505.18955v2#page=3)

## Training and inference

| Model | Learned behavior |
| --- | --- |
| Loc-Gen | Shortlist files from the repository structure, locate suspicious lines within those files, and generate or critique a patch. |
| Val-assert | Write a proof-of-concept script with assertions that encode expected behavior. |
| Val-no-assert | Write a proof-of-concept script without assertions and judge candidate fixes using execution outcomes. |

Training uses supervised distillation on examples derived from 2,000 issues from SWE-bench training and SWE-Gym. Claude 3.7 Sonnet supplies Loc-Gen examples. The validators also draw on o4-mini for diversity. Filtering removes incorrect outputs and excessively long reasoning. At inference, the system generates five localization hypotheses, 60 candidate patches, and four proofs of concept per issue. It runs the proofs of concept and existing functionality tests, then breaks ties by voting over normalized patches. [Paper, §§3–4.1 and Appendix D](https://arxiv.org/pdf/2505.18955v2#page=4)

## Results

The selected patch resolves **46.0%** of SWE-bench Verified with PatchPilot and **45.2%** with Agentless-mini. These are best-of-60 selection results. The candidate pool contained a passing patch for 66.0% of issues in the PatchPilot scaling experiment. In a separate ablation with 20 candidates, the hybrid setup reached 43.2%, compared with 37.0% without proofs of concept and 35.6% without dynamic testing. [Paper, Table 1 and Figures 4–5](https://arxiv.org/pdf/2505.18955v2#page=6)

## Relevance to agent training

The system exposes a distinct output for each role, so localization, patch generation, and patch selection can be evaluated separately. Its reproduction scripts are evaluated chiefly through their contribution to patch selection. The localization metric requires the complete reference set of affected files or lines. A diagnosis would also need to explain how execution reached the failure. [Paper, §4.2](https://arxiv.org/pdf/2505.18955v2#page=7)
