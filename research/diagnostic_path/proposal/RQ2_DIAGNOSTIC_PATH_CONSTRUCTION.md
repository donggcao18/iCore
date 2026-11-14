# RQ2: Constructing diagnostic-path supervision from SWE-smith

Research note for [the proposal](F:/GitHub/iCoRe/diagnostic_path/CONCISE_RESEARCH_PROPOSAL.md), following [the RQ1 assessment](F:/GitHub/iCoRe/diagnostic_path/RQ1_EVIDENCE_AND_BRT_SETTING.md). Initially written on 2 November 2026; source and assumption audit completed on **4 November 2026**. The construction pipeline below is a proposed design, not an implemented or experimentally validated system.

**Active agent pilot (6 November 2026):** the [Diagnostic Path Agent Guide](DIAGNOSTIC_PATH_AGENT_GUIDE.md) now specifies known-endpoint path construction: supply a root-cause candidate, extracted buggy production functions, and a test-derived entry, then have an agent independently inspect the intermediate dependencies and explain propagation to the observation. The earlier [test-patch-only localization protocol](DIAGNOSTIC_PATH_TEST_PATCH_ONLY_AGENT_GUIDE.md) and [paired-version oracle protocol](DIAGNOSTIC_PATH_ORACLE_AGENT_GUIDE.md) remain separate. Known endpoints and privileged function selection must be reported; source/probe-supported paths are not paired-version validated references.

## 0. Verification outcome and corrections

**The literature supports the ingredients, but does not establish that this combined pipeline can automatically produce gold paths at SWE-smith scale.** The earlier note was too ambitious if read as a ready-to-run label-construction recipe. The practical recommendation is to establish a small manually audited reference set first, then measure which parts can be automated.

| Claim or design choice | Audit judgment |
| --- | --- |
| A failing test can serve as an investigation start | Reasonable controlled setting; a test selector still needs execution and source resolution |
| A test is the production interface | Imprecise; keep the test driver, production entry, and observation distinct |
| Known injected changes give us the diagnostic path | Unsupported; they give candidate origins and a controlled contrast |
| GraphLocator provides two useful graph abstractions | Confirmed; its inferred explanatory graph is not a causal correctness oracle |
| PersistBench uses MCTS for construction | Confirmed for memory–query examples; transfer to diagnostic annotation is our proposal |
| Paired traces and a slice establish a gold causal chain | Too strong; observed differences and possible dependence need additional checking |
| DynaPyt provides the complete required labeler | Unsupported; it is an instrumentation framework, and custom analysis remains necessary |
| Human review makes a path uniquely and universally correct | Unsupported; it establishes an audited reference under a stated protocol |

The material corrections below make `base_commit` semantics explicit, add the generated-issue leakage limitation, distinguish a successful downstream intervention from origin identification, and restrict the initial implementation scope. Verification covered primary-source descriptions and selected implementation files. **No SWE-smith instance, tracing backend, construction yield, or training result was empirically validated in this audit.**

## 1. Judgment and recommended direction

**SWE-smith is a promising starting point for diagnostic-label construction because it provides controlled code changes, executable environments, and tests affected by those changes. It does not already provide gold diagnostic paths.** Knowing the changed function and a failing test substantially reduces the annotation problem, but it does not identify which intermediate dependencies actually explain the failure.

My recommendation is to combine:

1. A repository graph for finding structural candidates, inspired by GraphLocator.
2. Paired executions of the buggy and original working versions for collecting evidence.
3. Failure-relevant slicing and targeted counterfactual checks for validating explanations.
4. An LLM for proposing and summarizing diagnoses, with beam search initially and optional MCTS for difficult cases.

The output should be an **evidence-backed diagnostic graph, with one or more accepted path projections**, rather than a single function sequence selected by an LLM. Reserve “gold” for a manually audited reference set; maintain explicit confidence levels for automatically constructed training labels.

Here, “gold” means a documented reference annotation with reviewed evidence. It does not mean that every alternative has been excluded or that the explanation applies to every input. The two-graph design is a useful software abstraction; the cited papers do not establish that both graphs are necessary for our task.

## 2. Is a `FAIL_TO_PASS` test the interface function?

### What SWE-smith actually gives us

SWE-smith constructs bugs in existing Python repositories and validates them by checking whether existing tests break. Its documentation describes running tests on the original code and again after applying the candidate bug. In this construction schema, `patch` is the diff that **introduces the bug**, and an instance branch contains that bug. This differs from treating a dataset's `patch` field as necessarily a repair. Verify the pinned release's schema and reconstruct the versions explicitly. [SWE-smith validation documentation](https://swesmith.com/guides/harnesses/)

The paper documents `base_commit` as pointing to code **with the bug already applied**, and includes combined bugs affecting multiple entities. Do not apply the injecting patch a second time to that revision. Also, “all changed functions” cannot automatically be treated as the root cause of every individual failing test. [SWE-smith, Appendix A.1 and B.3](https://arxiv.org/html/2504.21798v2)

### Use four separate roles

| Symbol | Role | Meaning in our proposed dataset |
| --- | --- | --- |
| T | Failing-test entry | A specific executable test case, including its parameterization and setup |
| E | Production entry | The relevant operation invoked by the test or its setup |
| O | Failure observation | The assertion operand, exception, log event, or incorrect output under investigation |
| R | Defect-origin candidate | The injected change responsible for this observation, after verification |

These are our proposed annotation roles, not a standard terminology supplied by the cited papers. A `FAIL_TO_PASS` entry identifies a test case; it does not directly supply a production entry or an assertion-level observation. Resolve the selector, preserve its parameters and fixtures, and inspect the actual failure before assigning E and O.

**T is a reasonable controlled investigation entry, but it should not be called the production interface.** A test is the driver and often contains the observation. E is closer to your original “interface” intuition. Even E need not be public, and a test can exercise several production operations.

Do not simply select the first production call in the test. It might create an irrelevant helper object. Identify the operation whose result or side effect contributes to O, and retain relevant fixture/setup operations. If the failure occurs during collection, import, or fixture setup, the test function itself may never execute; record that category separately.

A useful representation is:

```text
Test driver T and setup
    └── exercise production entry E under condition C
          └── execute defect-origin transition R
                └── propagate/consume affected state
                      └── produce observation O
```

This is an execution outline, not a requirement that causality follows nested calls. R may affect a cache during setup and O may occur in a later, separate call. Navigation may move backward from O toward R; the final diagnostic explanation should describe the relevant forward dependencies.

### Endpoint selection policy

Keep each label conditional on **one concrete test execution and observation**. For an instance with several `FAIL_TO_PASS` cases, construct separate witnesses first, then merge shared mechanisms where justified. List order has no diagnostic meaning. Preserve multiple parameterized cases when they exercise different mechanisms, and avoid filling the dataset with near-identical test variants.

For an initial pilot, prefer one injected change in one production function, a deterministic assertion/exception failure, and a test that can run independently. This is a deliberate scope restriction, not a claim that all SWE-smith instances satisfy these conditions.

## 3. What to adopt from the literature

### 3.1 GraphLocator: separate code structure from explanatory hypotheses

GraphLocator's **Repository Dependency Fractal Structure (RDFS)** represents hierarchical code entities and structural dependencies. It extracts entities and membership using tree-sitter, adds dependencies using static analysis, and loads expensive dependencies on demand. Its **Causal Issue Graph (CIG)** instead contains natural-language sub-issues grounded to code entities, with LLM-estimated causal dependencies. It expands these hypotheses through neighboring repository entities using priority-driven abductive reasoning. Importantly, the paper describes these as hypothesized relations rather than statistically validated causal effects. [GraphLocator, §§2–3](https://arxiv.org/html/2512.22469v1)

**Adaptation:** use the repository graph to propose where to inspect, and a separate diagnostic graph to record what the evidence supports. Do not turn every call/reference edge into a causal label, or treat an LLM's edge confidence as a calibrated probability. There is no need to reproduce the full multilayer graph before testing feasibility on a small Python subset.

RDFS is not a ready-made dynamic dependence graph. In particular, its structural usage relations do not by themselves provide event-level definitions/uses, alias tracking, or the control dependencies needed by the slicing step proposed below. Those require additional analysis.

### 3.2 PersistBench: search is a generator, not a correctness oracle

The relevant paper is *PersistBench: When Should Long-Term Memories Be Forgotten by LLMs?* Its MCTS nodes are memory–query pairs. An LLM generates controlled variations, a judge scores target-model behavior, and UCT guides search. Top samples are subsequently checked on models outside generation, followed by human verification. It is not searching code paths or establishing program causality. [PersistBench, §4.1](https://arxiv.org/html/2602.01146v1)

**Adaptation:** borrow the separation between proposal, search, independent checks, and review. Change both the search state and the validation signal for our task. An explanation that is persuasive to a judge, or difficult for a student model, is not thereby a correct program diagnosis. Keep difficulty-based curation separate from label validity.

### 3.3 Cause-effect chains and cause transitions: the closest conceptual foundation

Zeller's *Isolating Cause-Effect Chains from Computer Programs* compares passing and failing states, then uses delta debugging and state changes to isolate failure-relevant variables and values. Cleve and Zeller's *Locating Causes of Program Failures* additionally focuses on moments when the relevant causes change during execution. These works are directly concerned with reconstructing how a failure develops, rather than merely finding a structurally connected function. [Zeller, FSE 2002, author publication page](https://issta.cispa.saarland/papers/fse2002/?lang=en), [Cleve and Zeller, ICSE 2005](https://www.st.cs.uni-saarland.de/publications/details/cleve-icse-2005/)

**Adaptation:** treat the original working version as a reference execution and investigate which changed state or control decisions connect the mutation to the observation. This suggests experimentally checkable labels. It does not imply that the original tools transfer directly to modern Python, or that arbitrary state replacement produces meaningful executions.

### 3.4 Failure-inducing chops and delta debugging: reduce irrelevant context

Gupta et al.'s *Locating Faulty Code Using Failure-Inducing Chops* combines reduced failure-inducing input with forward and backward dynamic slicing, intersecting their relevant statements. Zeller and Hildebrandt's delta-debugging work provides systematic reduction with explicit passing, failing, and unresolved outcomes. [Failure-Inducing Chops, ASE 2005](https://www.cs.purdue.edu/homes/xyzhang/Comp/ase05.pdf), [Delta Debugging, TSE 2002](https://pm.st.cs.uni-sb.de/papers/tse2002/?lang=en)

**Adaptation:** use slices to restrict candidate explanations and reduction to simplify inputs or change sets while preserving the same failure. Our proposed mutation-to-observation intersection below is inspired by chopping, not an identical implementation of their input-based algorithm. A slice still contains possible relevance; it is not automatically a minimal causal explanation.

### 3.5 KernelPath and SWE-Search: representation and search precedents

KernelPath pairs origins with source-grounded state-propagation paths and shows that explicit state-transition facts can be more useful than function paths alone. That supports recording state, conditions, and propagation roles in our labels. Its kernel annotations do not supply ready-made SWE-smith labels. [KernelPath](https://aclanthology.org/2026.emnlp-main.2201.pdf)

SWE-Search provides a code-domain precedent for MCTS over agent exploration with value feedback and iterative refinement. It supports investigating search as an implementation technique; successful repair search is not ground truth for every diagnostic edge. [SWE-Search, §3](https://arxiv.org/html/2410.20285v6)

## 4. Proposed representation: an explanation with checkable evidence

Use two main graph abstractions:

| Graph | Nodes and edges | Purpose |
| --- | --- | --- |
| Repository graph | Functions, classes, fields, files; calls, membership, reads/writes, imports | Structural candidate generation and navigation |
| Case-specific diagnostic graph | State/control propositions at source locations; supported influence and prerequisite relations | Training target and explanation of this failure |

Runtime event traces and their dependency graph are an **evidence backend**. They need not be exposed as a third navigation interface to the student. Static, observed, inferred, and intervention-supported relations must remain distinguishable.

A diagnostic node should record:

- A code symbol, source span, and relevant invocation/event identifier.
- Its role: trigger, origin transition, propagation, consumption, or observation.
- A concise proposition about state or control, with applicable conditions.
- Observed buggy/reference values or properties, where available.
- Evidence references, instrumentation coverage, and remaining uncertainty.

Function names alone are too coarse. The same function can run repeatedly with different state. Retain event identities internally, then produce a readable function-level projection without merging distinct causal steps. Expected values from the reference run should be described as **reference behavior under this test**, not universal specifications.

Represent alternative supported explanations and partial orders explicitly. A function-level summary can revisit a function; an event-level graph ordered by a concrete sequential execution can distinguish those visits. Concurrent executions require additional ordering evidence and should be outside the first pilot.

## 5. Construction pipeline

### Step 1: establish a reproducible contrast

For a pinned instance, reconstruct the original version P0 and injected-bug version P1. Run the selected case T in fresh, equivalent environments. Require P0 to pass and P1 to fail with the intended observation O on repeated runs. Store versions, test selector, commands, dependencies, seeds, logs, and failure identity.

For the documented SWE-smith layout, start from the instance's buggy revision and recover P0 by reversing the recorded injecting change in an isolated copy, or use the verified original revision from construction provenance. Check both directions and store the resolved commit hashes and source hashes. Do not assume that a branch's parent commit, a moving branch name, or a field called `base_commit` independently identifies clean code.

Reject or quarantine environment failures, unresolved runs, inconsistent observations, and test-order dependence. “Both runs finish differently” is insufficient: confirm that the difference is the failure the annotation will explain.

### Step 2: validate the origin candidate

Map the injected change to production source spans. For the initial single-change subset, verify that applying the change reproduces the failure and reverting it restores the reference outcome. Trace whether the changed behavior is exercised.

For combined changes, reduce or test relevant change subsets while preserving O. A minimal failure-inducing subset need not be unique, and interacting edits may require an origin set rather than one R. Defer those cases if the first dataset is restricted to single-origin explanations.

An injected site is stronger provenance than an arbitrary repair location, but it is not necessarily a single executed line. Deleting a required assignment or function can cause failure through an **absent event**. Preserve changed source spans in both versions; do not require the missing statement to appear in P1's trace.

### Step 3: collect traces, starting with selective instrumentation

First collect calls, returns, exceptions, source locations, and selected argument/result properties for the relevant test and setup. Add reads, writes, predicates, and object-field observations around unresolved transitions rather than immediately tracing every value in the repository.

DynaPyt is a plausible instrumentation starting point: it provides Python runtime hooks and supports selected runtime behavior manipulation. Its documentation also describes instrumenting copies of code and running analyses with test frameworks. It is a framework for building the needed analysis, not an existing complete diagnostic slicer. [DynaPyt implementation and documentation](https://github.com/sola-st/DynaPyt)

Prototype custom dependency tracking for a restricted set of Python constructs. Check that instrumentation preserves the original pass/fail behavior. Record unsupported operations and opaque library boundaries; never invent internal dependencies when the tracer cannot observe them. Native extensions, dynamic dispatch, aliasing, serialization, and asynchronous effects may require summaries or exclusion.

**Feasibility boundary:** for the first reference set, selective observations plus manual source checking are sufficient to investigate the idea. A repository-wide dynamic slicer, general heap tracking, automatic cross-version trace alignment, and safe arbitrary state intervention are separate engineering tasks. The source audit did not verify a turnkey tool that supplies this entire stack for SWE-smith. Avoid making that stack a prerequisite for the initial 30–50-case study.

### Step 4: derive an observation-specific candidate region

Start from the exact value, exception, or event O. Compute supported backward dependencies, including control dependence where possible. Intersect these with forward influence from the mutation-associated state/control transition:

```text
candidate region = forward influence from R ∩ backward relevance to O
```

Use the repository graph to propose missing structural connections, not to certify them. A simple call trace or coverage report is not enough to compute these dependencies. Where precise slicing is unavailable, label the result as an approximate candidate region.

This intersection is a proposed candidate-reduction operation, not a proof rule. It assumes compatible event identities and sufficiently complete dependence information. Missing control, alias, or library dependencies can exclude the real mechanism. An empty intersection under incomplete instrumentation means **unresolved**, not “no diagnostic path exists.”

For omissions and branch changes, compare both executions: the relevant explanation may concern an operation present in P0 and absent in P1. A buggy-run-only slice can miss that mechanism.

### Step 5: align executions and identify meaningful divergence

Align source anchors, invocation context, and semantic object properties between P0 and P1. Do not align only by event index; a changed branch or loop can shift the entire trace. Avoid treating object addresses, timestamps, or harmless ordering differences as faulty state.

Look for divergence attributable to R that matters to O. The earliest recorded difference may be irrelevant, and the true first transition may fall inside an unobserved boundary. State that uncertainty instead of labeling the first visible difference as the origin by default.

At this stage, collect propositions such as “the returned count differs after the changed predicate” or “a required field remains unset because initialization was skipped,” each tied to evidence.

### Step 6: construct and check candidate diagnoses

Give a teacher model the structural candidates, relevant source, paired observations, and private origin annotation. Ask it for a compact graph of explicit propositions and evidence references. Require it to mark gaps and unsupported relations.

Check each proposed transition against source and runtime evidence. An unrelated adjacent function, an incorrect state carrier, a reversed influence relation, or an unsupported condition should prevent acceptance. Preserve necessary prerequisite branches even when they are not on a shortest route.

Known endpoints help focus this offline construction. They must not turn the task into “invent a plausible bridge between these names.” The teacher should be allowed to return **insufficient evidence**.

### Step 7: strengthen selected transitions with experiments

Use controlled changes at verified boundaries where feasible. Examples include restoring the injected predicate or replacing a simple scalar result with its reference-run value. Preserve the input and observation criterion; check whether the same failure is removed, rather than replaced by an unrelated crash.

Arbitrary object substitution can create unreachable states or violate invariants. Use narrow interventions, fresh runs, and explicit unresolved outcomes. For omissions, replaying the missing operation may be more meaningful than changing a later output.

Reverting the whole mutation supports the **overall mutation-to-failure relation**. It does not independently validate every intermediate edge. Record local experiments separately and avoid claiming that a single successful intervention proves a unique, universally necessary causal chain.

Likewise, replacing a downstream value and making the test pass establishes a rescue under that intervention; it does not locate the original defect. A symptom can often be blocked after it has propagated. Keep origin attribution tied to the verified injected change and the introducing/omitted transition, rather than relabeling whichever node is easiest to repair. This distinction is also central to KernelPath's repair-site versus origin analysis. [KernelPath, §4.4.1](https://aclanthology.org/2026.emnlp-main.2201.pdf)

### Step 8: compress, review, and retain alternatives

Compress verified event-level evidence into a readable diagnosis. Mark compressed edges as summaries and retain their underlying witnesses. Remove irrelevant details only if the explanation remains supported; shortest length is a presentation preference, not a correctness criterion.

Have an independent reviewer check grounding, transition roles, conditions, and completeness. Use human adjudication for the reference evaluation set and a stratified audit of automated training labels. Keep multiple valid explanations when they describe different supported routes or abstraction levels.

## 6. Where MCTS fits—and where it does not

**Do not begin with MCTS over arbitrary repository walks.** With known endpoint candidates, structural path enumeration is easy to produce and can still be diagnostically wrong. The hard part is deciding what additional evidence would resolve uncertainty.

Our proposed search state is:

```text
S = (partial diagnostic graph, collected evidence,
     unresolved claims, inspected region, remaining budget)
```

Possible construction actions are:

| Action | Result |
| --- | --- |
| Inspect a neighboring symbol or dependency | Additional candidate source evidence |
| Instrument a selected boundary and rerun T | A more informative runtime observation |
| Compare a selected property across P0/P1 | A checked state/control contrast |
| Test an admissible local intervention | Support, contradiction, or unresolved evidence |
| Add, revise, or remove a diagnostic proposition | An updated explanation |
| Finish or abstain | A candidate annotation or explicit lack of evidence |

MCTS would search **evidence-gathering and annotation decisions**, not merely choose a sequence of function names. The search tree represents alternative construction histories; the diagnostic graph is the artifact produced by a history. Cache repeated runs and equivalent graph states where safe.

Use verifier results as the main signal. Reproducibility, source grounding, and support for transitions should gate acceptance. Among accepted candidates, prefer coverage of the mechanism and fewer unsupported gaps, then clarity and cost. LLM judgments can prioritize expansion, but cannot override a contradicted execution or manufacture a missing dependency.

For an implementation using scalar search values, reward verified completion above partial progress, penalize contradicted claims, and cap credit for repeated observations. Do not reward merely reaching R, generating a longer graph, or obtaining a high judge score. Any partial score is a search heuristic; final acceptance is a separate procedure.

Start with greedy expansion or a small beam over the same actions. Add MCTS only if it improves audited label yield or quality at a matched construction budget. Compare against a slicing-only summary and a single teacher pass. Nothing in PersistBench establishes that MCTS is the best search algorithm for this problem.

## 7. What counts as gold, silver, and unresolved?

| Label tier | Required evidence | Appropriate use |
| --- | --- | --- |
| Audited reference | Stable contrast; checked origin attribution; grounded explanation; documented transition support; independent human review | Held-out diagnostic evaluation; small high-quality supervision set |
| Execution-backed training label | Stable contrast and grounded transitions with explicit instrumentation limits; selective experimental checks | Scalable SFT with confidence-aware filtering |
| Structural/teacher hypothesis | Connected source entities and a plausible explanation, without adequate state/dependency verification | Candidate pool; weak supervision only if clearly separated |
| Unresolved | Missing, conflicting, or unreliable evidence | Further investigation or exclusion from path supervision |

Intervention support should also be recorded **per relation**, rather than implied for an entire tier. These are operational confidence categories, not a claim to complete causal identification. An unexamined alternative is not a negative example. Save contradicted candidates with the specific reason they failed, and retain unresolved candidates as unknown.

The reference annotation should define supported transitions and acceptable alternatives. Mark a transition as mandatory only within a clearly specified witness or when its necessity is separately justified; an unobserved alternative may bypass it. Exact string matching against one generated explanation would penalize valid diagnoses and encourage imitation of wording rather than diagnostic correctness. Human review checks the documented evidence and abstraction level, rather than certifying universal causal completeness.

## 8. Store labels separately from what the student can observe

The dataset constructor can use P0, P1, the mutation, T, and the suspected origin. **A localization student should not receive the gold origin as input** unless the experiment is explicitly about explanation with known endpoints. Origin labels and reference diagnoses can be training targets without being inference-time observations.

A compact record should contain:

```text
case_id, pinned_versions, environment_manifest
mutation_family, change_spans, origin_candidates, verified_origin_set
test_selector, entry_provenance, production_entries, observation
diagnostic_nodes, diagnostic_edges, accepted_alternatives
evidence_refs, local_experiments, unsupported_boundaries
label_tier, review_status, failure_category
```

Store the teacher's private evidence and the student's observation/action history separately. If training tool-use trajectories, replay or regenerate them against the student's allowed environment. Check the queries, intervention values, and explanations as well as tool outputs for dependence on P0 or the hidden mutation diff. Simply deleting a privileged observation can leave subsequent actions or assertions unsupported. An offline explanation generated with privileged information is not automatically an executable student demonstration.

For process supervision later, label progress relative to supported claims and unresolved gaps. Do not treat every deviation from a teacher's chosen search order as an error. The final diagnostic artifact and the full investigation history are different supervision objects.

## 9. Special consequence for BRT

In SWE-smith, the selected existing failing test already reproduces the injected regression. Giving its complete body and counting its reproduction as a newly discovered BRT would make the downstream claim uninformative.

Two defensible uses are:

- **Diagnosis/APR training:** T is deliberately supplied as the known reproducer. Learn to explain the failure or repair the defect. Evaluate this controlled capability honestly.
- **BRT training or evaluation:** use T privately for annotation and validation. Construct an agent-visible test corpus that withholds the designated reproduction case and close duplicates, while preserving necessary historical helpers. Do not reveal its selector, inputs, assertion, or test-derived diagnostic details unavailable under the intended input protocol. Treat any oracle-diagnosis experiment separately.

Withholding tests requires careful repository preparation: parametrized cases, shared fixtures, helper functions, and duplicate coverage can defeat a simple function deletion. Audit what the agent can actually retrieve. If the issue already provides an equivalent reproducer, record that fact rather than claiming the reproduction was discovered independently.

**Correction to the earlier note:** withholding tests alone does not address generated issue text. SWE-smith's Appendix D constructs LM issues using the bug patch, a failing test's source, and failure logs; Appendix C.2 explicitly warns that the issue-generation procedure does not check for underspecification or solution leakage. Audit, redact, or regenerate reports under the intended observation policy before using them for a blind BRT claim. [SWE-smith, Appendices C.2 and D](https://arxiv.org/html/2504.21798v2)

My recommended first step is **controlled diagnosis/APR construction on SWE-smith**, followed by a separately designed BRT evaluation. This keeps the initial label-construction question tractable without confusing a supplied failing test with a test the model must generate.

## 10. Pilot and decision criteria

The following counts are proposed planning choices, not literature-derived estimates:

1. Select roughly **30–50 cases across several repositories**, initially emphasizing single-origin deterministic failures. Include return-value errors, state mutation, changed control flow, and omissions.
2. Manually construct a small reference set to expose the necessary evidence and annotation disagreements before scaling automation.
3. Implement paired runs and selective tracing. Compare structural routes, teacher-only explanations, and trace-backed explanations on the same cases.
4. Add a beam-based constructor with verification. Evaluate MCTS only on cases where alternative investigation choices matter.
5. Audit accepted labels, then use them for the RQ1 artifact-value experiment before investing in large-scale agent training.

For that first pilot, use a concrete acceptance rule: reproduce O in P1 and its absence in P0; attribute the contrast to the chosen change; document the origin transition and each summarized dependency with source spans and observation references; reject unexplained jumps or mark them as gaps; and have a second reviewer assess the explanation independently. Distinguish observed, source-inferred, and intervention-supported relations in the released annotation. These are proposed acceptance criteria whose reliability and cost must themselves be measured.

Measure the complete construction funnel: runnable contrast, attributable origin, observable mechanism, accepted annotation, and human-audited correctness. Report yield, cost, reviewer agreement, unsupported-edge rate, and rejection reasons by repository and failure type. Do not silently discard unsupported cases and then claim broad coverage.

Split by repository where feasible, and group shared mutation ancestors, changed functions, and near-duplicate test cases before splitting. Otherwise, siblings of the same injected bug can appear on both sides. For natural-bug transfer, an executable corpus such as BugsInPy can provide a separate check; its fix locations still require origin verification rather than automatic conversion into causal labels. [BugsInPy implementation](https://github.com/soarsmu/bugsinpy)

Proceed if a useful subset yields reproducible, reviewer-supported state-bearing explanations with manageable custom analysis. Narrow the scope if opaque boundaries dominate. If paths remain largely teacher assertions despite tracing, use them as hypotheses rather than gold labels and revise the supervision target toward verified local transitions.

## 11. Suggested formulation of RQ2

> Given a controlled regression, a failing-test witness, and known injected-change candidates, can we construct reliable reference diagnoses connecting the defect-origin transition to the observed failure? We combine repository dependencies with paired execution evidence, selective slicing, and targeted experiments. We assess annotation validity, alternative explanations, construction coverage, and cost, distinguishing verified relations from structural or model-generated hypotheses.

**Recommendation:** use GraphLocator's separation of repository structure and explanatory structure, borrow PersistBench's search-and-validation discipline, and make paired-execution debugging the foundation of correctness. MCTS can help allocate investigation effort; it cannot supply the missing ground-truth oracle.

**Scope after verification:** the defensible near-term contribution is a measured procedure for constructing audited, test-conditional diagnostic references on a restricted subset, followed by transfer experiments. Automatic cause-effect-chain construction already has classical precedents; neither that broad idea nor applying MCTS alone establishes novelty. General coverage, scalable label quality, and benefits from training on these references remain empirical questions.
