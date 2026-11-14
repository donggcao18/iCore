# Capable but Careless: Do Computer-Use Agents Follow Contextual Integrity?

**Benchmark:** AgentCIBench. **Authors:** Anmol Goel and Iryna Gurevych. **Venue:** EMNLP 2026.

Sources: [published record](https://aclanthology.org/2026.emnlp-main.55/), [arXiv v1](https://arxiv.org/html/2606.23189v1), [official implementation](https://github.com/UKPLab/emnlp2026-agentcibench). Numerical details can differ between versions; experiments not reproduced.

## Problem

Agents may complete a task while disclosing information inappropriate for its recipient or context. The benchmark distinguishes visual co-location, ambiguous-task oversharing, and recipient misalignment.

## How the method works

Each scenario specifies application state, a request, a recipient, required information, and prohibited disclosures. MCTS mutates scenarios; proxy rollouts and judging favor successful tasks that expose disclosure failures. This search reward constructs evaluation cases, rather than RL-training the evaluated agents.

OpenApps supports structured-state evaluation and end-to-end rendered-UI interaction. Scoring combines text matching with an LLM judge, requiring textual support for judge-identified leaks. Task utility and leakage are reported separately, including leakage conditioned on engagement to distinguish privacy from refusal.

## Findings and interventions

The authors evaluate 15 agents and report average leakage of 67.9%. They also test recipient-typed, rubric-informed, and restrictive prompting defenses. This is primarily an evaluation/harness contribution, not a diagnostic-path learning method. [Paper](https://arxiv.org/html/2606.23189v1).

## Implementation

The repository separates scenario generation, structured-state evaluation, visual evaluation, defense configurations, and aggregation. It provides a BrowserGym-based personal workspace, scenario data, and optional Docker setup. [Implementation guide](https://github.com/UKPLab/emnlp2026-agentcibench/blob/main/README.md).

## Relevance to our proposal — methodological analogy

The connection is evaluation design, not code causality:

- Task success can conceal failure on an additional important dimension.
- Known scenario labels permit targeted failure analysis.
- Conditioning metrics on engagement avoids rewarding non-participation.

For diagnosis, analogously separate location success from mechanism fidelity. A model that refuses to explain should not appear more accurate simply because it makes fewer claims; report explanation coverage and correctness together.

This analogy does not imply that privacy labels or scenario-generation rewards can be reused as causal-path labels. Scenario search can overrepresent discovered failure patterns, so generalization requires separate cases and models.
