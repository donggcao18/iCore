# An implementable direction for RQ2 and RQ3

Decision note, **4 November 2026**, building on the [verified RQ2 proposal](F:/GitHub/iCoRe/diagnostic_path/RQ2_DIAGNOSTIC_PATH_CONSTRUCTION.md) and [RQ3 proposal](F:/GitHub/iCoRe/diagnostic_path/RQ3_DIAGNOSTIC_NAVIGATION_SUPERVISION.md). This document narrows the first implementation. Proposed components and experiments have not been implemented or run.

For module boundaries, record schemas, acceptance checks, and build order, see the [practical implementation plan dated 5 November](F:/GitHub/iCoRe/diagnostic_path/RQ2_RQ3_PRACTICAL_IMPLEMENTATION_PLAN.md).

## 1. The direction I recommend

**Construct small, reviewed sets of state-transition witnesses from paired SWE-smith executions, then train a bounded diagnostic agent to acquire and explain those witnesses. Start with controlled diagnosis/APR.**

The first target is an accepted failure mechanism under a concrete test, containing an origin transition and a few supported propagation relations. It need not enumerate every executed function or identify a unique explanation valid for every input.

This connects the questions directly:

- **RQ2:** Can paired executions plus limited source review produce trustworthy witness annotations at useful cost and yield?
- **RQ3:** Given those annotations, does training on intermediate evidence improve the agent beyond rewarding the complete final diagnosis?

The hardest component in the earlier proposals was an assumed general diagnostic verifier. Narrowing the annotation vocabulary and eligible program behaviors makes a first verifier inspectable. MCTS and a learned process critic can follow if a concrete bottleneck justifies them; neither resolves uncertain labels by itself.

## 2. What changes from the earlier plan

### Complete paths are an unnecessary first dependency

If we require a universally correct, complete path before training anything, RQ2 becomes a large causal-analysis project. Instead, annotate a **sufficient reviewed witness for the selected failure**, preserve known alternatives, and mark gaps. Only claim correctness at the granularity supported by that evidence.

An annotation should include a diagnostic core, not just a list of interesting facts. The core must connect the origin transition to the observation through checked relations. Disconnected true facts do not qualify as a successful diagnosis.

### A scalar process bonus can be a disguised terminal objective

If each milestone is rewarded once, then:

```text
sum(newly covered milestones) = final milestone coverage
```

When this is also the final diagnostic score, adding that sum to a scalar GRPO reward merely increases the weight on final coverage. It does not establish a benefit from intermediate credit assignment. If acquired evidence and final committed claims differ, the bonus adds an evidence-acquisition objective, which is useful but a different claim.

The main RQ3 experiment below therefore keeps the total task reward identical and changes its temporal allocation. An additive milestone bonus can remain a secondary experiment, with a matching terminal-weight control.

## 3. A concrete example of the desired annotation

Illustrative code, **not an extracted SWE-smith instance**:

```python
def normalize_count(n):
    return 1 if n == 0 else n

def build_job(n):
    return {"count": normalize_count(n)}

def rate(n):
    job = build_job(n)
    return 10 / job["count"]

def test_rate_zero():
    assert rate(0) == 10
```

Suppose a single injected mutation changes the first return constant from `1` to `0`. Under the selected test, the working version passes and the mutant raises `ZeroDivisionError`.

The useful annotation is:

| Role | State-bearing witness | Required evidence |
| --- | --- | --- |
| Origin transition | `normalize_count(0)` produces `0`; the reference invocation produces `1` | Matched invocation, return observations, injecting-change attribution |
| Propagation | `build_job` stores that result in `count` | Return/local snapshot and source showing the assignment relationship |
| Consumption and observation | `rate` uses `count` as a divisor, producing the observed exception | Source use plus the concrete failure |

The navigation route can be `test → rate → build_job → normalize_count`. The explanatory propagation proceeds `normalize_count → build_job → rate → failure`. These orders differ for a valid reason. Do not require the agent to investigate in the explanation's forward order.

Three function names would omit the mechanism. Conversely, copying the entire runtime call sequence would add little diagnostic supervision. The witnesses identify the relevant carrier and each function's role.

## 4. RQ2: construct witnesses with a restricted pipeline

### 4.1 Select cases by observability, then report the resulting scope

For the initial implementation, prefer:

- One small injecting change in one production function.
- A test that runs independently and reproduces the same assertion/exception failure.
- A short, observable Python-level propagation mechanism.
- Scalars, strings, or bounded collections whose relevant properties can be compared reliably.
- A reviewed source connection between the origin effect and the observation.

Defer concurrency, hidden native-library effects, complex aliasing, and multi-origin interactions. Include changed control flow or omissions only when the supported observation mechanism is adequate. These are eligibility restrictions, not claims that excluded bugs lack diagnostic paths.

Select candidate cases before seeing whether a student model solves them. Retain the denominator and rejection reasons. A benchmark consisting only of examples the teacher could explain would conceal construction failures.

### 4.2 Reproduce the contrast correctly

Use SWE-smith's execution environments and instance-loading mechanisms. Its documented `base_commit` already contains the injected bug, and its `patch` introduces that bug. Recover the working version using verified construction provenance or reversal of the injecting patch in an isolated copy. Validate the chosen test on both versions. [SWE-smith, Appendix A.1](https://arxiv.org/html/2504.21798v2), [official toolkit](https://github.com/SWE-bench/SWE-smith)

The original version passing and the mutant failing establish an overall contrast. They do not identify every intermediate relation. Resolve test parameters, fixtures, production entries, and the precise observation separately.

### 4.3 Start with event recording, not a repository-wide slicer

Record Python calls, returns, exceptions, and selected local properties. Python's tracing API exposes these event types; line events occur before the associated line executes, which matters when interpreting local snapshots. A custom observer is still required. [Python tracing documentation](https://docs.python.org/3.11/library/sys.html#sys.settrace)

Use safe, bounded value projections instead of assuming that arbitrary object representations can be compared across runs. Match invocations using source identity, call context, and input properties; do not pair the nth event in one trace with the nth event in another after control flow diverges.

The Debugging Book provides an actual Python `Slicer` implementation with dynamic data/control-dependence tracking. It is worth testing on the selected subset before building similar instrumentation. DynaPyt provides more general runtime hooks for custom analyses. Neither has been tested here against the intended SWE-smith subset. [Debugging Book: Tracking Failure Origins](https://www.debuggingbook.org/html/Slicer.html), [DynaPyt](https://github.com/sola-st/DynaPyt)

A plain call/return tracer should be the initial backend. Add slicing or richer hooks only when specific rejected cases show that the additional analysis would recover useful coverage.

### 4.4 Generate candidates with privileged information, then review their support

The offline constructor can see both versions and the injecting change. Use that information to identify candidate state differences near the change, then ask a teacher to connect a small number of them to the failure using source and observations.

For each proposed relation, record:

```text
source symbol and source span
destination symbol and source span
carrier/property and invocation context
buggy observation; reference observation when available
relation role: introduce / propagate / consume / prerequisite
evidence references
status: observed / source-supported / experimentally checked / unresolved
```

A different value in a downstream function is not enough: its relevant input may already differ. Distinguish introduction from propagation. A downstream intervention that makes the failure disappear also does not independently identify the origin.

Have a reviewer check the core relations and the selected failure. Store partial annotations separately. Initially, use only complete-at-the-declared-granularity reviewed cores for primary training/evaluation; use partial cases for analysis or explicitly weak supervision.

### 4.5 Preserve the uncertainty instead of expanding the claim

The output is an audited, test-conditional witness set. It does not prove that the annotated path is the only valid explanation. A previously unseen, apparently valid path goes to adjudication; it is not automatically labeled wrong because it differs from the teacher's sequence.

This construction method is an implementable proposal whose yield still needs measurement. It should not be described as already providing automatic gold diagnostic paths for all of SWE-smith.

## 5. A small interactive environment that can support training

Separate the annotation store from what the actor can access:

| Actor-visible | Private scorer/constructor |
| --- | --- |
| Buggy source and runnable test context | Working revision and injecting change |
| Public source entities and ordinary neighbors | Verified origin labels and witness cores |
| Buggy-side observations obtained through tools | Reference observations and adjudication records |
| Its current structured claims and evidence IDs | Intermediate correctness scores |

Start with four operations:

```text
inspect(symbol_or_span)
probe(symbol, event_or_property)
record_claim(relation, carrier, evidence_refs)
submit(origin, diagnostic_core)
```

The initial graph can index source entities and structural candidates. The case-specific explanatory graph is assembled from claims; it does not have to be a second large graph-search service. This preserves GraphLocator's useful distinction without copying all its machinery. Its CIG is a hypothesis representation, not a label oracle. [GraphLocator, §§2–3](https://arxiv.org/html/2512.22469v1)

For reproducible training, precompute bounded buggy-side observations where possible. A cached `probe` must return what the public query requests, not only fields that differ from the clean run. Cache coverage must be defined independently of the gold origin; otherwise query availability itself can reveal the answer. Return “unavailable under this backend” consistently for unsupported queries.

Use the same observation and action budgets for all models. Do not expose the full private trace by default. Keep hidden correctness judgments outside the actor's prompt, and prevent private state from entering filenames or identifiers shown to it.

This cached environment measures evidence selection and integration over its supported queries. It does not establish that the model can invent arbitrary instrumentation. Evaluate live probes later as a separate capability.

## 6. RQ3: compare terminal diagnosis against reward redistribution

### 6.1 The common task objective

Ask every actor for the same output schema. Use a terminal score such as:

```text
R0 = origin correctness + final supported-diagnosis quality
```

Normalize both components to a declared range and tune their weighting on development cases. The diagnostic score must check relation roles, carriers, and evidence, not only function membership. Keep contradicted and unresolved claims separate, cap output size, and include a joint origin-plus-core success metric.

All experimental conditions use the same case labels and terminal scorer. Endpoint-only RL is retained as a separate target ablation.

### 6.2 Define an intermediate potential from the same witnesses

Let `Phi_t` measure the bounded fraction of reviewed evidence milestones the actor has genuinely acquired and integrated by step t. A milestone requires the specified observations/source context and a compatible recorded claim. A name appearing in a search result is insufficient.

Set the initial potential to zero. Repeated evidence does not add coverage. Incorrect or retracted claims may reduce the current potential. Use reviewed predicate templates initially, so that validation does not depend on a general semantic LLM judge.

This potential measures progress toward the reference mechanism under the current annotation. Its limitations and alternate-route handling must be audited along with final scoring.

### 6.3 Keep total reward identical

Use finite episodes with unit discount for this first comparison. Set terminal shaping potential to zero for every way an episode ends. For nonterminal actions:

```text
r_t = lambda * (Phi_t - Phi_previous)
```

For the terminal action:

```text
r_terminal = R0 - lambda * Phi_previous
```

Then, for every completed episode:

```text
sum_t r_t = R0
```

The terminal correction is essential. Without it, a process condition can simply receive more total reward for the same final quality. This construction is a finite-episode instance of potential-based shaping; the classical result concerns the objective under its assumptions, not a guarantee of improvement with a practical neural optimizer. [Ng, Harada and Russell, 1999](https://people.eecs.berkeley.edu/~russell/papers/icml99-shaping.pdf)

Example with `lambda = 0.5`, intermediate potentials `0, 1/3, 2/3`, and `R0 = 1.4`:

```text
Terminal-only rewards:  [0,       0,       1.4]
Redistributed rewards: [0.1667,  0.1667,  1.0666]
Total:                  1.4               1.4
```

Stop, timeout, abstention, and budget exhaustion need consistent terminal treatment. Invalid episodes still receive the declared terminal score. Record the actual final transition instead of silently losing its correction.

### 6.4 The trainer must consume per-action returns

Both variants should use the same turn-aware optimization machinery. The terminal-only condition gets terminal-only rewards; the process condition gets the redistributed rewards. Compute returns-to-go and apply their advantages to the corresponding assistant action spans, with the same documented baseline and normalization rules. Exclude tool-observation tokens from the actor loss.

**If ordinary GRPO simply sums each episode's rewards and broadcasts one advantage, the two conditions are identical at the reward level.** That implementation cannot test reward timing. Use a trainer supporting per-turn returns or implement action/token-level advantages; validate the mapping before expensive runs.

At unit discount, the shaped return differs from the terminal-only return by a state-dependent potential term. With an ideal policy-gradient estimator, this can behave like a baseline change rather than adding a new task objective. Its possible benefit is better credit/variance and learning dynamics. With clipping and normalization, practical behavior requires measurement; do not claim stronger causal supervision solely because rewards are dense.

Retain a terminal-weight control for any later experiment using additive coverage bonuses. Prior search-agent work already distinguishes scalar incorporation from action-aligned credit; our experiment should make that distinction explicit in the code setting. [Beyond Outcome Rewards](https://arxiv.org/html/2610.10179v1)

## 7. The experiment sequence I would run

All quantities below are planning targets, not estimates established by the literature.

### Experiment 1: can we construct reliable witness cores?

Start with **10 technical pilot cases**, then expand to approximately **30–50 independently reviewed cases** across several repositories. Measure the full funnel: reproducible contrast, trace availability, explainable origin effect, supported connecting core, and reviewer agreement.

Compare three annotation inputs on the same cases: source plus injecting change; source plus a function route; source plus paired state observations. Ask reviewers to assess relation correctness and evidentiary support without knowing which construction variant produced the candidate. This tests whether collecting state evidence improves labels rather than merely lengthening explanations.

The small audited set calibrates the method. It is not a sufficient training corpus or a basis for broad performance claims. Build additional training cases only after measuring the acceptance rate and identifying which annotation checks can be automated.

### Experiment 2: do these witnesses help repair?

Use a fixed repair model with identical localized code. Compare no diagnosis, a bare function route, and a reviewed state-bearing witness. Label this an oracle-artifact study when private reference information was used in the supplied witness.

Then test predicted witnesses separately. A gain from oracle context establishes possible value, not that a learned agent can acquire it. If even reviewed witnesses add no useful benefit, reconsider expensive diagnostic RL before scaling it.

### Experiment 3: what supervision helps acquire the witnesses?

Collect replayable teacher trajectories using only actor-permitted tools, then initialize one common policy. Train the following conditions from that same checkpoint:

| Condition | Objective and feedback | Interpretation |
| --- | --- | --- |
| Common SFT | Demonstrations only | Baseline without RL |
| Endpoint RL | Final origin reward | Existing localization objective |
| Terminal diagnosis RL | R0 entirely at the final action | Does the final artifact target suffice? |
| Redistributed diagnosis RL | Same R0 total, allocated using verified progress | Does temporal credit improve learning? |

The last two need the same turn-aware trainer, actor/update budget, label set, and inference environment. Log independent diagnostic correctness, origin accuracy, joint success, observation cost, and learning curves. Measure verifier cost for both conditions, including the process labels computed for analysis but unused by the terminal-only learner.

After a promising pilot, scale training to hundreds or more accepted cases and run a learning curve. The required quantity is an empirical question. Keep repository/mutation-family holdouts, separate audit cases from training, and repeat decisive comparisons across seeds.

The key downstream result is from the **agent's predicted** diagnoses, with the repair backend fixed. Also inspect cases where models predict the same origin, because that isolates gains beyond endpoint localization. Do not replace full-population results with this selected subset.

The potential-based redistribution formula is classical, so it should not be presented as a new RL algorithm. The possible research contribution is the diagnostic witness corpus, the distinction between source access and mechanism evidence, and a controlled demonstration of what supervision improves acquisition and downstream use in code diagnosis.

## 8. What can be reused in this workspace

The local source inspection suggests the following boundaries:

| Component | Reuse or required work |
| --- | --- |
| Source entities and structural candidates | Prefer the test/fixture-aware [DependencyIndex](F:/GitHub/iCoRe/scripts/test_retrieval/static_dependencies.py:66); the older [repository index](F:/GitHub/iCoRe/scripts/code_retrieval/repo_graph/graph.py:30) also contains snippet metadata |
| AST source extraction | Reuse selected helpers in [search_utils.py](F:/GitHub/iCoRe/scripts/code_retrieval/repo_graph/search_utils.py:8), while explicitly adding the selected test and fixtures; the current file scan skips many test paths |
| SWE-smith execution | Use the official toolkit/profile adapter; the local [ExecSpec](F:/GitHub/iCoRe/scripts/env_setup/exec_spec.py:26) expects SWE-bench fields such as `version` and `environment_setup_commit` |
| Dynamic causal dependencies | New/custom analysis; the local index is not a complete dynamic dependence graph |
| Observation collection and matching | New bounded tracer/adapter and cross-version alignment checks |
| Witness annotation and checking | New structured schema plus reviewed predicate rules |
| Turn-aware diagnostic training | New integration or an adapted external trainer; no such trainer was found in the inspected local scripts |

A²Agent has a concrete rollout → progress labeling → advantage estimation → training pipeline worth adapting structurally. Its published trainer uses advantage-weighted likelihood, and its location-based progress labeler does not implement our witness checker. Reusing it would be an adaptation, not a drop-in reproduction. [A²Agent implementation](https://github.com/donian00/A2Agent)

Suggested implementation units, **not existing commands**:

```text
prepare_case       -> pinned buggy/reference versions and test manifest
record_observations-> versioned event/snapshot files
annotate_witness   -> candidate core plus review/evidence records
make_public_cache -> buggy-only tool environment
score_diagnosis   -> origin/core scores and partial potentials
redistribute      -> per-turn rewards with terminal settlement
train_agent       -> common trainer for terminal and process variants
evaluate          -> independent diagnosis and fixed-backend repair results
```

The smallest useful deliverable is the first five units on a few real cases. A static graph export or a scalar GRPO reward alone would not establish the proposed experiment.

## 9. Decisions after the pilot

- If state-bearing witnesses are trustworthy but too costly, automate the specific recurring predicate classes or use teacher-assisted review. Scale the verified subset before attempting a general judge.
- If the agent can exploit source/test names without acquiring evidence, increase controlled difficulty and audit joint diagnostic success; do not infer understanding from origin accuracy.
- If terminal diagnosis RL matches redistribution, prefer the simpler terminal objective. That is a meaningful RQ3 result.
- If a closed-source workflow generates useful, supported diagnoses but the student cannot learn them reliably, report the workflow's RQ1 utility and retain it as the implementation fallback.
- If neither oracle nor predicted witnesses improve repair, reconsider APR as the application before developing more elaborate RL.

**Recommended research claim:** test whether a bounded, evidence-grounded diagnostic capability can be labeled and learned—not whether an arbitrary graph walk can be made to resemble one chosen gold path. The first implementable step is a small witness dataset and public observation environment; the first clean learning comparison is terminal diagnostic reward versus the same reward redistributed across actions.
