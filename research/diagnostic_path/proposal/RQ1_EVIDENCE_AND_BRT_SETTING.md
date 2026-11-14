# RQ1: Do diagnostic paths help downstream tasks, and what is a valid BRT setting?

Research note for [the diagnostic-navigation proposal](CONCISE_RESEARCH_PROPOSAL.md). Literature checked on 2 November 2026. This is a source-based assessment; the reported experiments have not been reproduced. Recommendations and proposed experiments below are our analysis, not findings from those papers.

## 1. Judgment

**There is enough evidence to pursue RQ1, but not to assume that an ordered function path is the useful ingredient.** Existing studies support benefits from causal explanations, explicit dependency structure, and state-transition information. Some already evaluate these ideas in repair and bug reproduction test generation. The remaining question must therefore be more precise than “does providing a diagnostic path help?”

The most defensible question is:

> Given the same localized production code and retrieved existing tests, does an evidence-supported account of failure-relevant state transitions improve reproduction-test generation beyond locations, code, and the same diagnostic facts presented without their relationships?

**For BRT, test retrieval and diagnostic navigation are compatible. However, supplying a hidden gold regression test as the entry point changes the information available and can reveal the output being requested.** A controlled supplied-entry experiment is useful, provided that the entry is carefully defined and its results are separated from ordinary issue-to-test generation.

## 2. What information should a diagnostic path contain?

Use **symptom-associated function** for a function connected to the observed failure. It may be a public API, an internal operation, or a failure-reporting function. An existing test can be a separate investigation entry point; it is not necessarily the symptom location in production code.

For this project, a diagnostic path should explain:

1. The relevant input or prerequisite condition.
2. Where failure-relevant state first becomes incorrect, or a required operation is omitted.
3. How that state is propagated, transformed, or consumed.
4. How the resulting behavior becomes observable.

Each transition should have supporting source or execution evidence. Navigation can proceed backward from the symptom, while the explanation can describe forward propagation from the origin. Neither needs to reproduce every explored function or every runtime call.

A dependency route is only a candidate explanation. Reachability alone does not establish that the route carries the faulty state. Some failures also require several interacting branches, so a compact diagnostic subgraph may be more faithful than forcing a single chain.

## 3. Evidence relevant to RQ1

All differences below are **percentage points (pp)**. They are within-paper comparisons, not a ranking across papers or independently established statistical significance.

### 3.1 GraphLocator: structure adds value with the localized entities held fixed

GraphLocator §6.1/Table 5 compares its localized code entities without explicit relationships against a Mermaid representation preserving its causal issue graph. Both variants have identical reported file/function localization scores. On SWE-bench Lite, repair success increases from **26.33% to 28.67% (+2.34 pp)** for Agentless and **29.00% to 30.67% (+1.67 pp)** for Trae Agent. The corresponding Java gains are **+0.78 pp** and **+0.79 pp**. This is useful evidence beyond simply retrieving better locations. However, it tests a generated graph representation, not a verified state-propagation chain, and does not completely separate relational information from serialization or prompt length. [GraphLocator, §6.1 and Table 5](https://arxiv.org/html/2512.22469v1)

### 3.2 From Finding to Fixing: explanations can help beyond gold locations, with important qualifications

On SWE-bench Verified, Table 5 reports **76.2%** repair success with gold symbols and **80.0%** with gold symbols plus bug-cause descriptions: **+3.8 pp** beyond the same location guidance. However, these descriptions are generated using the **gold patch**, making this a study of privileged guidance. They are not independently acquired diagnostic paths. The effect also varies: on the SWE-bench Pro bug subset, adding causes changes **53.8% to 52.9% (−0.9 pp)**. This supports investigating the usefulness of explanatory information, not assuming universal benefit or deployable acquisition. [From Finding to Fixing, §4.3 and Table 5](https://arxiv.org/html/2610.25826v1)

### 3.3 ReProAgent: direct evidence for a diagnostic stage in BRT

ReProAgent includes execution-path-based root-cause analysis before test planning. Its Qwen3-Coder ablation reports **56.88%** fail-to-pass success on SWT-bench Lite versus **53.26%** without this stage: **+3.62 pp**. On SWT-bench Verified, the comparison is **60.05% versus 56.58%: +3.47 pp**. This is directly relevant BRT evidence. But removing the entire stage also removes analysis, information, and computation; it does not isolate path structure from the same facts or code context. The broad claim that path-based diagnosis can assist BRT therefore already has precedent. [ReProAgent, §5.3 and Table 7](https://arxiv.org/html/2607.09123v1)

### 3.4 KernelPath: an important challenge to the function-chain hypothesis

On a **30-case diagnostic subset**, KernelPath reports GPT-5.4/Codex localization accuracy around **20–23.3%** with log-only, path-only, neutral length-matched, or self-inferred-state inputs, versus **90.0%** with explicit state-transition facts. Its interpretation is that function paths are underspecified without identifying the erroneous state, its expected condition, and the transition responsible. This is a diagnostic probe in Linux kernel localization, not an APR/BRT outcome study or evidence that a trained agent can acquire these facts. Nevertheless, it strongly motivates separating bare function chains from state-bearing diagnoses in our experiments. [KernelPath, §4.3.1](https://aclanthology.org/2026.emnlp-main.2201.pdf)

### 3.5 iCoRe: test context remains independently important

iCoRe retrieves both production code and existing tests. In Table 4, removing test retrieval reduces F→P@1 from **42.0% to 33.7% (−8.3 pp)** on SWT-bench Lite and **52.8% to 35.2% (−17.6 pp)** on TDD-bench Verified. This supports keeping test retrieval in a realistic BRT system. It does not show that test context substitutes for diagnostic information, nor does it test the incremental value of a diagnostic path with test retrieval held fixed. [iCoRe, §5.3 and Table 4](https://arxiv.org/html/2604.19224v1)

### 3.6 What these results establish collectively

My assessment is that the evidence supports three distinct possibilities:

| Possible source of improvement | Current support | Remaining uncertainty |
| --- | --- | --- |
| Better target-code identification | Established component of existing workflows | How much improvement remains after locations are already accurate? |
| Additional failure-mechanism information | Supported by explanation and state-fact studies | Can it be obtained reliably without future artifacts? |
| Explicit relationships between otherwise identical facts | Suggested by graph-structure comparisons | Does this improve BRT under matched information and budgets? |

These studies do **not** jointly establish that a uniquely correct path exists, that path-supervised training is necessary, or that process rewards outperform endpoint rewards. Those are separate questions. RQ1 should first establish the value of the diagnostic artifact; it does not require training an RL policy to conduct the first experiment.

## 4. Does supplying an entry point contradict test retrieval?

**No. The roles differ, but a sufficiently informative entry point can partly solve retrieval for the agent.** Consider the following proposed division of information:

| Information | Potential role in BRT |
| --- | --- |
| Symptom-associated production function | Identifies an operation or failure manifestation to investigate |
| Retrieved existing tests | Supplies fixtures, setup, helper usage, conventions, and a suitable test host |
| State-bearing diagnosis | Identifies triggering conditions, relevant transitions, and observable consequences |
| Newly generated reproduction test | Turns those conditions and expected behavior into an executable check |

This is a design hypothesis: production diagnosis can help determine **what behavior to exercise**, while existing tests can help determine **how to express it in this repository**. Test retrieval may also reveal behavior, and diagnosis may help locate a test host, so the roles overlap.

If the supplied entry is an existing test, some test-retrieval uncertainty has already been removed. That does not invalidate a conditional diagnosis experiment. It does mean that the experiment cannot establish that test retrieval is unnecessary or fairly measure its full contribution under ordinary inputs.

## 5. What RepoNavigator's controlled setting actually supports

In *One Tool Is Enough*, the authors give a precise entry file and function to their method and all baselines, explicitly describing this as a departure from official SWE-bench inputs. Appendix F describes the entry as normally a testing function. In the inspected v6 text, I could verify the supplied-entry protocol, but **not the specific rule that the entry is the first `FAIL_TO_PASS` test**. Treat that selection rule as unverified until checked against the relevant implementation or data preparation. [RepoNavigator, §4 and Appendices B/F](https://arxiv.org/html/2512.20957v6)

Our methodological conclusion: adopt the idea of isolating navigation with a shared start, but do not automatically transfer a localization input protocol to BRT. A test function can be harmless historical context, a privileged relevance hint, or an almost complete reproduction solution. These cases have very different implications.

## 6. How much information does the entry reveal?

SWT-Bench defines issue-to-test generation using the issue and codebase; a generated test succeeds when it fails before the fix and passes after the reference fix. Its golden reference tests are solutions to the test-generation task. SWE-bench's schema separately exposes `base_commit`, `patch`, `test_patch`, and `FAIL_TO_PASS`; availability in the dataset does not make every field a legitimate agent input. [SWT-Bench, Introduction and §3](https://arxiv.org/html/2406.12952v3), [SWE-bench dataset schema](https://www.swebench.com/SWE-bench/guides/datasets/)

The following classification is our recommended experimental policy:

| Supplied information and provenance | Assessment for ordinary issue-to-BRT evaluation |
| --- | --- |
| Production function mentioned in the original issue or traceback | Compatible with the task; disclose the extraction rule |
| Production anchor predicted from the issue and buggy repository | Compatible; include retrieval failures and its acquisition cost |
| Existing test retrieved from the buggy repository using only permitted inputs | Compatible test retrieval |
| Existing base-version test selected using hidden `FAIL_TO_PASS` metadata | Privileged relevance selection; use a separately labeled controlled setting |
| Hidden regression-test name or parameterized selector without its body | Can reveal the intended case, behavior, or input literals; still privileged |
| Test body, fixtures, or parameter additions taken from the future `test_patch` | Target leakage for ordinary BRT when it supplies the sought reproduction |
| Complete reproducer already present in the original issue | Legitimate original input, but analyze separately because the remaining generation task is easier |
| Diagnostic annotation created with the gold patch | Suitable as explicitly privileged upper-bound guidance, not deployable inference evidence |

**An existing test name selected from hidden metadata is not equivalent to supplying its full future body, but it is not ordinary blind retrieval either.** Information can leak through selection as well as through content. A selector may also refer to a newly added test or parameter case absent from the base snapshot. Resolve every supplied symbol against the buggy repository and record exactly what exists there; never silently materialize missing code from the reference test patch.

Giving all baselines the same privileged hint improves internal fairness. It does not restore comparability with systems evaluated using only the original issue and repository.

## 7. Recommended experimental settings

### Track A: realistic BRT — main application evaluation

Provide the original issue and buggy repository, including its historical tests. Use only these inputs to retrieve production anchors and existing tests. Keep reference patches, future tests, and hidden selectors outside the agent's observations. Any fixed-version checking used by the benchmark should occur in the evaluation harness; if a method exposes such feedback during generation, declare and evaluate that as a separate protocol.

For a diagnosis experiment, keep the test retriever fixed across compared variants. Report all assigned issues, including cases where the retrieved start is poor. Evaluate whether the diagnostic workflow earns its acquisition cost, rather than reporting only cases on which it successfully constructs an explanation.

### Track B: controlled RQ1 — isolate the value of diagnosis

Supply the same production anchor and code context to every variant. Use an issue-derived anchor where possible; otherwise label an annotated start as privileged. A base-version test host can also be supplied, but its source and selection rule must be explicit.

For BRT, avoid supplying a complete hidden reproducer. If a complete failing test is intentionally available, study diagnosis or APR, or explicitly reformulate the task as test improvement/amplification with a distinct unsolved target. Do not count reproducing the supplied test as success at discovering a new reproduction.

An oracle diagnostic annotation can provide an upper-bound study of useful information. It cannot establish that an inference-time agent can recover that information, and it should not be pooled with Track A results.

## 8. A discriminating RQ1 experiment

Use a fixed BRT generator and the same issue, anchor, target-code locations, and source snippets across conditions:

| Condition | Additional diagnostic context | Question tested |
| --- | --- | --- |
| C0: locations and code | None | Baseline with endpoint information already available |
| C1: function route | Ordered function names and structural dependencies, without state explanations | Is a structural route sufficient? |
| C2: diagnostic facts | Relevant conditions and state facts as an unordered inventory | Does additional semantic information help? |
| C3: linked diagnosis | The same facts as C2, with explicit, supported relationships and propagation order | Do relationships add value beyond the facts? |

The key comparisons are **C3 versus C0** for total diagnostic value, **C2 versus C0** for additional semantic information, and **C3 versus C2** for the value of relationships. C1 tests the weaker function-chain hypothesis challenged by KernelPath.

Keep source coverage and test context identical. For C2/C3, preserve the same factual propositions as far as possible; document the relational information deliberately added in C3. Match prompt length approximately and use neutral padding controls where useful. Hold model, sampling, candidate count, execution access, and generation budget fixed. Audit annotations so an apparent diagnosis benefit is not simply a concealed fix instruction or copied gold assertion.

Repeat this comparison with two test-context settings: no retrieved tests versus one fixed set of retrieved historical tests. This factorial design tests complementarity. A gain only when tests are retrieved would still be meaningful: diagnosis may need executable setup context to become useful.

Start with a manually checked pilot, using explicit privileged annotations if necessary to test potential value. Only after this study should a second evaluation replace those annotations with diagnoses acquired from permitted inputs. Report acquisition and generation costs together in that second evaluation.

### Outcomes and interpretation

Use fail-to-pass reproduction success as the primary BRT outcome, with repeated-run stability checks and review of whether the failing assertion matches the reported issue. A differential pass/fail result alone can reflect an unrelated behavioral change. Report results on the same issues with paired uncertainty estimates; do not infer significance from small reported gains alone.

Inspect generated tests for their triggering inputs, prerequisites, and observable assertions. This helps distinguish a diagnosis that changes test behavior from an explanation the generator merely repeats. Analyze entry quality, issue-provided reproducer availability, and dependency complexity as secondary factors without excluding hard cases from the main denominator.

Possible conclusions should remain distinct:

- **C2 improves on C0, but C3 does not improve on C2:** semantic diagnosis helps; explicit path representation may be unnecessary.
- **C3 improves on C2:** relational structure has incremental value under these controls.
- **Only oracle diagnoses help:** useful information exists, but reliable acquisition remains the main obstacle.
- **No meaningful gains beyond strong retrieval:** prioritize retrieval or a simpler diagnostic representation instead of assuming path-supervised training is worthwhile.

## 9. Recommended positioning in the proposal

> Prior work suggests that structured causal context and state-transition information can assist localization and downstream code tasks. However, existing evidence often changes the retrieved context, removes an entire analysis stage, or supplies privileged explanations. We investigate whether an evidence-supported diagnostic representation improves bug reproduction test generation beyond the same localized code and retrieved historical tests, and whether explicit relationships add value beyond the underlying diagnostic facts. We distinguish controlled supplied-entry studies from end-to-end issue-to-test evaluation.

**Recommendation:** pursue BRT as a viable application, retain test retrieval, and treat supplied entries as an experimental control rather than a default claim about realistic input availability. The strongest contribution would establish what diagnostic information is useful, then show that an agent can acquire it without hidden solution artifacts. It would not merely show that more gold-derived context helps a generator.
