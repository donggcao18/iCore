# Code Agents Don’t Understand Root Cause: Evaluating Code Agents on Linux Kernel Fault Localization

**Benchmark:** KernelPath. **Authors:** Yuekeng Li et al. **Venue:** EMNLP 2026.

Sources: [published record](https://aclanthology.org/2026.emnlp-main.2201/), [full paper and construction appendix](https://aclanthology.org/2026.emnlp-main.2201.pdf). Source-based reading summary; experiments not reproduced.

## Motivation

Naming the faulty function does not establish understanding of how erroneous state reaches a downstream failure log.

## How the benchmark is constructed

KernelPath contains 200 Linux 5.10 cases. Annotators normalize kernel-related materials into candidates. A Code Graph grounds log sites and candidate origins; graph search proposes routes. Source checking selects diagnostic state-propagation paths. Origins, paths, and dependence layers are fixed before Claude Code instantiates logs. Consistency, realism, and leakage checks filter cases. The construction graph is not itself the agent's interactive reasoning environment.

```text
Case materials → source grounding → diagnostic labels
  → log instantiation → filtering → agent evaluation
```

## Evaluation

Agents receive logs and repository access, then produce ranked origins and diagnostic chains. Top-k measures localization; Order-Weighted Path Recall measures ordered reference-node recovery, weighted around the fault position.

Dependence layers distinguish local data/control flow, same-context interprocedural propagation, and asynchronous/cross-context propagation.

## Findings and boundaries

Endpoint success can coexist with weak process recovery. In a controlled probe, explicit state-transition facts help much more than function paths alone. This is an evaluation study, not an RL training recipe. Its source-grounded labels do not establish intervention-proven universal causality.

## Relevance to our proposal — interpretation

Our target should include the relevant state, triggering conditions, and transformations, rather than only ordered function names. Consider separately scoring origin accuracy, transition support, and prerequisite/order recovery.

For our corpus, natural GitHub reports should remain distinct from reports generated after selecting a diagnostic path. Accept alternative explanations where supported. Complement path recall with unsupported-claim precision: listing extra functions should not improve a diagnosis for free.

**Potential reuse:** the separation of endpoint and mechanism, graded dependence complexity, and label-first construction discipline. It does not supply ready-made gold paths for SWE-bench or an implemented training environment for our agent.
