# RQ3: What supervision should train diagnostic navigation?

Research proposal prepared on **4 November 2026**, following the [RQ1 evidence review](F:/GitHub/iCoRe/diagnostic_path/RQ1_EVIDENCE_AND_BRT_SETTING.md) and the [verified RQ2 construction proposal](F:/GitHub/iCoRe/diagnostic_path/RQ2_DIAGNOSTIC_PATH_CONSTRUCTION.md). Literature findings and proposed adaptations are distinguished below. No training or reward-verifier experiments have been run for this proposal.

## 1. Recommendation and research question

**Start with terminal supervision for both the predicted origin and the final diagnostic explanation. Then test whether verified intermediate progress and better action-level credit provide additional value. Do not assume that process rewards are necessary, or begin by training an unconstrained LLM judge to score every turn.**

An important distinction is that a diagnostic path can be a final output. Scoring that complete artifact at episode termination is still outcome supervision. It is richer than rewarding only the final function, but it does not tell individual investigation actions whether they made progress.

Refine RQ3 into three comparisons:

1. **Target:** Is origin-only supervision sufficient, or must the final diagnosis also be rewarded?
2. **Intermediate signal:** Does verified progress improve learning beyond the same terminal diagnostic objective?
3. **Credit assignment:** Is an accumulated process bonus sufficient, or should credit be assigned to the actions that acquired and integrated evidence?

The proposed contribution is a controlled study of these distinctions in evidence-grounded code diagnosis. Process rewards and action-level credit already have precedents; applying either term alone would not establish novelty.

## 2. What existing research supports

### A²Agent: the closest code-localization precedent

A²Agent combines terminal localization accuracy, rewards for newly discovered gold locations, and discovery–commitment supervision. It estimates action credit by grouping turns from the same issue using preceding action histories, excluding the current action. Teacher SFT initializes its policy. Its training objective is advantage-weighted likelihood, rather than a PPO/GRPO clipped objective. This supports separating reward design from credit assignment, but its gold-location signals do not verify a state-propagation mechanism. Its implementation's progress labeler counts entity mentions, which is weaker than evidence that a relation was understood. [A²Agent, §4 and Appendix C](https://arxiv.org/html/2608.29831v1), [released progress labeler](https://github.com/donian00/A2Agent/blob/main/toolplan/data/progress_labeler.py)

**Our adaptation:** compare discovery against evidence-backed progress, and reward incorporation of supported relations into the final explanation. Do not directly treat mentions, tool arguments, or visited function names as diagnostic understanding.

### Graph-R1 and RepoNavigator: useful outcome-oriented baselines

Graph-R1 trains multi-turn hypergraph retrieval using GRPO and an outcome-directed reward combining format and final-answer token F1. It does not require a gold code diagnostic path. This supports the plausibility of learning useful interaction from terminal feedback, without establishing causal-path correctness. [Graph-R1, §3](https://arxiv.org/html/2507.21892v1)

RepoNavigator learns repository navigation with a supplied entry point and terminal localization/tool-success feedback. It trains directly without teacher-distillation SFT. Its results therefore also caution against claiming that SFT is universally required. Neither endpoint accuracy nor successful symbol resolution independently verifies a diagnosis. [RepoNavigator, §§3–5 and Appendix B](https://arxiv.org/html/2512.20957v6)

**Our adaptation:** retain an endpoint-trained baseline with exactly the same entries, tools, and budgets as the diagnostic variants. The different semantics of a hypergraph and a code graph do not prevent transfer of the interaction-training idea; they do change what actions and rewards mean.

### Beyond Outcome Rewards: direct evidence that signal and credit must be studied together

This search-agent study compares grounded evidence coverage, dependency-constrained coverage, and answer-string matching, each incorporated as a trajectory bonus or local retrieval-action credit. Its reported average QA F1 is **51.25** for outcome-only, **52.64** for coverage with scalar incorporation, and **54.34** for coverage with local incorporation. These are within-paper comparisons, not code-diagnosis results. It also finds that stricter dependency requirements do not consistently help; their effect depends on credit assignment. [Beyond Outcome Rewards, §§3–5 and Table 8](https://arxiv.org/html/2610.10179v1)

**Our adaptation:** use the same verified progress events when comparing scalar and local credit. Test whether the agent acquired useful evidence separately from whether it incorporated that evidence correctly. Do not enforce one reference retrieval order just because the final mechanism has an ordering.

### Process reward models: useful tools with a different meaning from causal verification

Choudhury's *Process Reward Models for LLM Agents* learns action-value targets from Monte Carlo continuations and iteratively improves the policy. A separate paper, *AgentPRM: Process Reward Models for LLM Agents via Step-Wise Promise and Progress*, models expected future success and differences in progress, using MC or TD/GAE-based estimates. These are distinct papers sharing the AgentPRM name. [Choudhury, 2025](https://arxiv.org/html/2502.10325v1), [AgentPRM: Promise and Progress, 2025](https://arxiv.org/html/2511.08325v1)

**Our adaptation:** a learned value estimate could help prioritize investigation after a reliable terminal objective exists. A high probability of later finding the right function is not a label that the current causal claim is true. If the rollout objective checks only the endpoint, a value model inherits that limitation.

### Process-supervision evidence and verifier limitations

*Let's Verify Step by Step* finds benefits from process-supervised reward models in mathematical solution selection. Importantly, it evaluates best-of-N selection with a fixed generator; it does not establish that RL with process rewards improves a code agent. [Let's Verify Step by Step, §2.1](https://arxiv.org/html/2305.20050v1)

ProcessBench evaluates identification of erroneous mathematical steps and reports generalization limitations in existing PRMs. This is evidence for independently testing a process evaluator, rather than assuming that a strong generative model reliably detects errors in an unfamiliar code domain. [ProcessBench](https://arxiv.org/html/2412.06559v1)

**Overall assessment:** the literature justifies testing intermediate supervision. It does not establish which diagnostic progress signal is reliable, whether it transfers across repositories, or whether it beats a strong terminal diagnostic objective at matched cost.

## 3. Training task and observable environment

Use the RQ2 setting: a buggy repository, a concrete failing-test entry T, and an observation O. During annotation, the constructor also has the injected change and working reference version. During localization training, the actor receives the permitted buggy-side inputs, not the gold origin, mutation diff, or private reference execution.

The initial task should be **controlled diagnosis with a known failing test**, with later APR evaluation. A BRT experiment requires the separate information restrictions described in RQ2; supplying the already failing test and asking the agent to rediscover it would not measure ordinary issue-to-BRT generation.

Keep a small action vocabulary:

| Action | Public result or purpose |
| --- | --- |
| Inspect a symbol or source span | Read the selected buggy-side code |
| Retrieve typed neighbors | Obtain structural candidates from the repository graph |
| Run an allowed probe | Execute the selected test or inspect approved runtime properties in the buggy version |
| Record or revise a diagnostic claim | Add an externally inspectable proposition with source and observation references |
| Submit | Return origin prediction and compact diagnostic graph/path |

Probes should use a restricted, tested interface. Arbitrary state editing, access to the clean version, and full general-purpose tracing are not prerequisites for the first experiment. The initial actor can investigate using source inspection and selective buggy-side execution.

The actor's state is its **actual available history**: issue/test context, observations, claims, and remaining budget. Identical recent tool calls do not imply identical knowledge or execution state. If using history grouping, measure its coverage and context mismatches; do not treat it as exact counterfactual state equivalence.

## 4. Define terminal supervision before process supervision

The final output should contain an origin prediction and a compact diagnosis. Diagnostic relations should describe the relevant condition/state, its introducing or omitted transition, and its influence on the observation, with citations to acquired evidence.

Use two separately reported terminal scores:

- **Origin score:** correctness against the RQ2-verified origin or accepted origin set. For multi-origin cases, use a set metric rather than assuming one function.
- **Diagnostic score:** supported transition coverage and correctness under the reference annotation protocol, including accepted alternatives and relation direction/roles.

A proposed training reward is:

```text
R_terminal = w_origin × origin_score
           + w_diagnosis × diagnostic_score
           − w_error × confirmed_contradiction_penalty
```

These weights are design parameters selected on development cases. Report the components independently and include a joint success measure so that a fluent partial explanation cannot hide incorrect localization.

For terminal diagnostic scoring, do not rely on matching a sequence of function names. Evaluate proposition/relation content with source grounding. Separate **supported**, **contradicted**, and **unresolved** claims. Missing annotations should not automatically turn a novel alternative into a negative label. Conversely, unresolved prose should not earn correctness credit. Bound the number of submitted claims and audit unscored content so that uncertainty is not a route to unlimited unsupported explanations.

A simple early implementation can restrict claims to a reviewed predicate vocabulary and a small set of source-grounded transition types. More general semantic judging is an extension requiring its own validation.

This gives a strong **terminal-diagnosis baseline**. Comparing process rewards only against a final-function reward would leave open whether the gain came merely from changing the output target.

## 5. Proposed process signal: newly supported diagnostic milestones

Reward progress on externally checkable evidence and claims, rather than how convincing an investigation narrative sounds.

Let M be the reviewed milestone set for the training case. A milestone might require identifying a particular incorrect state, obtaining the observation that supports it, and correctly assigning its role in a transition. It should not merely require mentioning the associated function.

Let Z_t contain milestones supported by the actor's permitted evidence and recorded claims after action t. Define:

```text
p_t = total weight of milestones newly supported at t
      / total reference milestone weight
```

Credit each milestone only once per episode. If the reference contains alternative sufficient explanations, use an adjudicated alternative-aware scoring rule rather than requiring the union of every possible path. A shorter supported explanation should not lose solely because it omits a redundant branch.

Example:

| Event | Proposed progress treatment |
| --- | --- |
| The origin function name appears in search output | Discovery proxy only; no diagnostic milestone yet |
| The agent inspects a changed predicate | Source access; diagnostic credit depends on what is established |
| A probe and source inspection support that a field remains unset | Credit the corresponding reviewed state/transition milestone |
| The agent links that state to a later failing operation with valid evidence | Credit a newly supported propagation relation |
| The same result is printed again | No additional milestone credit |
| A plausible hypothesis is explicitly tested and rejected | Useful investigation; do not punish it as a false committed conclusion |
| A final claim contradicts the cited observation | Confirmed error, eligible for a penalty |

Evidence may be acquired in any order. Score a relation once its prerequisites are available; do not force the actor to inspect functions in the reference path's order. The causal explanation's order and the investigation's order are different objects.

For the first version, reward only milestones with sufficiently reliable automatic checks. Leave difficult semantic or native-library transitions to audit rather than making an unvalidated judge the primary reward source. Useful negative-evidence acquisition can be annotated as an additional milestone class later; it should not be fabricated from every rejected hypothesis.

## 6. How intermediate rewards enter training

### Comparison A: scalar accumulation

Add the same milestone events to the terminal score:

```text
R_episode = R_terminal + λ × sum_t p_t
```

A trajectory-level update can then broadcast one group-relative advantage to all trainable actor tokens. This is the simplest implementation. It tests the value of the extra signal, but does not establish that an individual tool call received accurate credit.

**Design clarification, 4 November 2026:** if this non-repeating milestone sum equals the final diagnostic coverage, the scalar bonus can simply reweight the terminal diagnostic objective. If it measures acquired evidence rather than final committed claims, it adds a distinct acquisition objective. Neither case alone isolates temporal credit. The [integrated experiment plan](F:/GitHub/iCoRe/diagnostic_path/RQ2_RQ3_IMPLEMENTABLE_EXPERIMENT_PLAN.md) therefore specifies a cleaner comparison with identical episode reward totals and explicit terminal correction.

Because milestone credit is non-repeating and normalized, its positive total is bounded. This limits rewards for revisiting the same evidence. Final diagnostic scoring remains necessary: acquiring evidence and using it correctly are different capabilities.

### Comparison B: returns and credit tied to actions

Keep the **same milestone events and terminal objective**, but use per-turn returns, for example at unit discount within a bounded episode:

```text
G_t = R_terminal + λ × sum_{u >= t} p_u
```

Use these returns to construct advantages for the corresponding assistant action spans. This lets earlier enabling actions receive credit for later progress while avoiding crediting a later action for evidence already obtained. The estimator needs a documented, action-independent baseline; a history-conditioned value baseline is one option, and a query-conditioned leave-one-out baseline is another. State the normalization and eligibility rules, because these affect the update and can introduce bias or variance.

Use the same actor optimizer and comparable baseline machinery across scalar/local conditions. If that is not possible, report the algorithm change as an additional factor rather than attributing all gains to process supervision. An action-local residual of the kind studied in *Beyond Outcome Rewards* is another implementation option; it should be evaluated as its own credit heuristic, not described as identical to return-to-go.

Important implementation requirements:

- Map rewards to the **executed** tool call and exact assistant-token span, including delayed probe results.
- Exclude tool-observation tokens from the actor loss.
- Keep a terminal learning signal for final origin selection and explanation assembly.
- Handle constant-reward groups explicitly; zero group-relative advantages are a lack of contrast, not a successful update.
- Preserve evidence provenance when an action supports several later claims; avoid counting the same milestone twice.
- Log the fraction of steps receiving usable local feedback and the relative gradient magnitude of terminal/process components.

A standard GRPO callback returning one scalar per completion implements Comparison A. Comparison B requires support for action/token-specific advantages or a trainer with per-turn returns; changing the callback alone is insufficient. “Process-level supervision” therefore does not necessarily mean adding a new neural loss or training a new PRM.

## 7. Reward-shaping theory: a useful constraint, not a blanket guarantee

Potential-based shaping uses:

```text
F_t = γ Φ(s_{t+1}) − Φ(s_t)
```

Under the appropriate MDP, discounting, and boundary assumptions, this preserves optimal policies. The assumptions and terminal handling matter. [Ng, Harada and Russell, 1999](https://people.eecs.berkeley.edu/~russell/papers/icml99-shaping.pdf)

For our finite episodes, the discounted shaping sum telescopes to `−Φ(s_0) + γ^T Φ(s_T)`. Making terminal potential zero removes the last term; retaining final diagnostic coverage instead changes the objective. Our milestone bonus is best presented as an explicit task objective, not automatically as policy-invariant shaping. Clipping, reward normalization, partial observability, and approximate state representations also prevent an unqualified invariance claim.

Further, with zero terminal potential, adding the telescoped constant to an entire trajectory's scalar reward provides no additional within-query GRPO contrast when initial potentials match. Per-turn credit or a different explicit objective is needed to exploit intermediate structure. This is an algebraic design implication, not a reported code-agent result from the shaping paper.

## 8. Training schedule and label requirements

### Phase 0: establish a usable verifier

Use RQ2's small audited set to validate terminal and milestone scoring. Include wrong origins with plausible explanations, correct origins with incorrect transitions, unsupported citations, repeated claims, symptom-blocking repairs, and valid alternative routes. Measure false positive/negative judgments and unresolved coverage independently of the actor.

This small set calibrates the task; it is not enough by itself to establish scalable RL training. Additional training instances and reference annotations must be constructed and audited before making a generalization claim.

### Phase 1: initialize tool use and diagnostic output

**SFT before RL is the recommended engineering default here**, because the actor must learn tool schemas, evidence references, and structured diagnostic claims. Use replayable demonstrations collected under the student's observation policy. A teacher's access to a gold patch or working execution must not leave impossible observations or private expected values in the student's interaction history.

The purpose is to obtain enough valid exploratory episodes for RL, not to force imitation of one search order. Retain multiple supported routes where available. Keep an SFT-only model as a serious baseline. Direct RL remains a valid ablation if the starting model already produces usable tool interactions; the proposal does not claim SFT is theoretically necessary.

### Phase 2: train outcome baselines

From the same initialization, compare origin-only terminal RL with terminal origin-plus-diagnosis RL. Hold output format, environment, and observation budget fixed. First establish whether a diagnostic final objective is sufficient.

### Phase 3: add verified progress and compare credit

Compare scalar milestone bonuses with action-level credit using the same case annotations and reward events. Tune process strength using development cases, and select checkpoints using both origin and independent diagnostic evaluation. Do not select solely on the training verifier's score.

### Phase 4: optional learned process model

If verified progress is useful but expensive, train a critic from those audited signals, or estimate continuation values against the full terminal diagnostic objective. Evaluate the critic on held-out repositories, unsupported transitions, and counterexamples before using it for reward or search.

Keep two outputs conceptually distinct: **claim support** and **expected future success**. A value critic can assist action selection without certifying causal truth. Label uncertainty and policy dependence remain limitations. A learned PRM is a scaling extension, not a prerequisite for testing RQ3.

## 9. Controlled experimental matrix

All actor-training variants use the same initial checkpoint, case split, tool access, supplied entry, and bounded observations. Scalar/local comparisons share the same verified milestones. Proposed labels below name experimental conditions, not new algorithms.

| Condition | Terminal target | Intermediate signal / credit | Main question |
| --- | --- | --- | --- |
| SFT only | Demonstration targets | No RL | Is RL needed beyond demonstrations? |
| Endpoint RL | Origin accuracy | No diagnostic process signal | What can endpoint learning accomplish? |
| Terminal diagnosis RL | Origin + final diagnosis | No intermediate diagnostic reward | Is changing the final objective sufficient? |
| Discovery RL | Same terminal diagnosis | Newly encountered gold locations, accumulated | Is a simple localization proxy sufficient? |
| Verified progress, scalar | Same terminal diagnosis | Newly supported milestones, accumulated | Does evidence-based progress add value? |
| Verified progress, local | Same terminal diagnosis | Same milestones with action-specific credit | Does credit placement add value? |

For the first pilot, prioritize SFT only, endpoint RL, terminal diagnosis RL, and verified progress with scalar incorporation. Add local credit only after the verifier and scalar signals are usable. The full matrix then establishes the claimed mechanism.

Two informative follow-up controls are:

- **Misaligned-credit control:** permute usable process credit among eligible actions while preserving its scale and amount as closely as possible. A drop would support the importance of alignment, though the control itself can inject noise; interpret it together with the scalar comparison.
- **Dependency-order control:** compare rewarding evidence as it becomes supported with forcing one reference investigation order. This tests whether strict path imitation unnecessarily penalizes useful alternative exploration.

Use both a fixed actor-update budget and an end-to-end cost account. Process verification, extra rollouts, annotation, and critic training are additional resources. If evaluating a learned critic at inference, also compare it against an untrained actor given the same search budget; otherwise training and inference-time search benefits are confounded.

## 10. Evaluation and success criteria

### Primary outcomes

1. Verified origin accuracy or origin-set F1.
2. Supported diagnostic relation precision/recall under independent review, with alternative-aware matching.
3. Joint origin-and-diagnosis success.
4. Fixed-backend APR success; BRT only under its separately declared input protocol.

The downstream generator, code context, and test context must be held fixed when measuring the value of the learned diagnostic artifact, following RQ1. Also report conditional analyses with endpoint quality matched so a repair gain cannot be attributed only to better location prediction. Such conditional analyses complement the full-population result and do not replace it.

### Process and reliability measures

Measure verified evidence acquisition, evidence-to-final-claim incorporation, unsupported/contradicted claim rates, repeated actions, stopping behavior, tool/context costs, and episodes with tied terminal rewards. Distinguish a coherent final explanation from proof of the model's internal reasoning: externally checkable actions and claims are our observable target.

Audit unseen valid routes rather than treating every unmatched claim as wrong. Keep the actor's training scorer separate from the human-audited evaluation set. Use repository/mutation-family splits from RQ2, repeated seeds for the decisive comparisons, and paired uncertainty estimates on the same held-out issues. Report annotation and verifier coverage alongside performance.

### What would support the proposal?

- Terminal diagnosis RL beats endpoint RL on diagnosis: the final target matters.
- Verified progress beats terminal diagnosis RL at matched cost: intermediate supervision adds value.
- Verified progress beats location discovery: diagnostic evidence matters beyond visiting labels.
- Local credit beats scalar incorporation using the same events: action attribution matters.
- These gains survive independent review and improve APR/BRT under the declared protocol: the learned capability has application value.

If only the first comparison succeeds, prefer the simpler terminal objective. If process training improves the training score but independent correctness declines, reject the verifier or reward design. If diagnostic quality improves without downstream gains, report that distinction rather than claiming the applied task benefits.

## 11. Feasibility, risks, and fallback

The main dependency is **RQ2 label validity**, not the choice of RL algorithm. Private annotations may score training episodes, but correctness labels, hidden origins, and reference-run states must not be inserted into the actor's inference context. Generated SWE-smith reports require the leakage audit described in RQ2.

The largest practical risks are incomplete reference paths, opaque runtime boundaries, a scorer that rewards familiar wording, and local advantages that compare incompatible contexts. Restricting the initial predicate vocabulary and probing interface makes these failures easier to inspect. Track rejection reasons and unsupported cases rather than claiming a general causal verifier.

If automatic milestone checking remains unreliable, use the audited annotations for terminal diagnostic scoring and SFT, and retain a closed-source teacher plus source/execution verification as the working diagnosis workflow. Evaluate that workflow under RQ1. A useful diagnostic artifact does not require claiming that RL has solved its acquisition.

## 12. Proposal wording

> We investigate what supervision enables repository agents to construct evidence-supported diagnoses from a known failure entry toward a defect origin. We separate origin-only rewards, terminal diagnostic supervision, and intermediate progress rewards, then compare trajectory-level and action-level credit using the same verified evidence milestones. Our process signals target newly supported state transitions rather than function mentions or exact imitation of one search order. We evaluate origin accuracy, independently reviewed diagnostic correctness, acquisition cost, and downstream repair or reproduction-test utility, explicitly accounting for incomplete labels and privileged annotation information.

**Recommended path:** establish the terminal-diagnosis baseline first; test bounded, verified milestone rewards next; investigate action-specific credit and learned critics only when the preceding comparisons justify them. The intended result is evidence about which supervision is sufficient, not a presumption that process-level RL must win.
