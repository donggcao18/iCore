# SWE-Ladder: Learning to Localize, Reproduce, and Patch via Progressive Privileged Self-Distillation

**Authors** Xudong Cai, Zhengxi Lu, Yuchen Yan, Jian Shao, Jun Xiao, Yueting Zhuang, and Yongliang Shen.  
**Status** arXiv preprint, October 2026. [Preprint record](https://arxiv.org/abs/2610.33667) · [Code](https://github.com/sekongger/SWE-Ladder)

## Task

Train one tool-using Qwen3-4B-Instruct-2507 policy to resolve issues end to end. The training curriculum changes which actions receive teacher guidance. The deployed agent chooses its own sequence of searches, tests, and edits. [Paper, §3](https://arxiv.org/pdf/2610.33667v1#page=3)

## Training

| Phase | Evidence shown only to the teacher | Iterations |
| --- | --- | ---: |
| Localization | Changed production files, original hunk ranges, and pre-fix source lines | 30 |
| Reproduction | Fail-to-pass test identifiers and available test code | 20 |
| Patching | Verified repair diff | 20 |

A stateful tool-call router labels student actions. A frozen teacher from the same base checkpoint scores student-generated prefixes, and on-policy self-distillation applies only to response tokens routed to the active phase. Group Relative Policy Optimization (GRPO) continues to train on the full trajectory using the final patch test outcome with termination-aware shaping. The training set contains 1,149 issues from SWE-smith, SWE-ReBench, R2E-Gym, and SWE-Gym. [Paper, §§3–4.1 and Appendix A.5](https://arxiv.org/pdf/2610.33667v1#page=3)

## Results

| SWE-bench Verified pass@1 | Base | GRPO | GRPO + full-patch distillation | SWE-Ladder |
| --- | ---: | ---: | ---: | ---: |
| R2E-Gym | 9.6% | 10.4% | 12.4% | 16.4% |
| OpenHands | 13.0% | 11.2% | 12.4% | 17.6% |

On R2E-Gym, distilling all stages simultaneously reached 12.4%, and reversing the curriculum reached 11.4%. The full schedule raised the localization diagnostic from 33.6% to 52.0% and post-edit verification from 22.6% to 30.0%. Reproduction moved from 59.4% to 60.0%, with a dip during training. [Paper, Tables 2–4 and Figure 4](https://arxiv.org/pdf/2610.33667v1#page=6)

## Relevance to agent training

The teacher can use different verified artifacts to supervise different actions even when the student runs a single repair loop. The localization diagnostic measures recall of reference files in the final edit. The reproduction diagnostic counts a generated script that emits a failure signal. Those measures leave open whether the agent identified the failure mechanism. The main ablations use one backbone and scaffold, and the reported training curves have no independent seed repeats. [Paper, Limitations](https://arxiv.org/pdf/2610.33667v1#page=9) · [Appendix A.1](https://arxiv.org/pdf/2610.33667v1#page=12)
