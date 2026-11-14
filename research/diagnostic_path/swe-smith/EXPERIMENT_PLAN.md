# SWE-smith diagnostic paths for function localization

## Dataset snapshot

The complete `SWE-bench/SWE-smith` `train` split is in [`data/`](data/). The pinned revision is `ea6d7173829c7ec8fa16c22055699ff2e9188091`. All 11 Parquet shards match the Hub's SHA-256 hashes and total 277,767,206 bytes. Local inspection found 59,136 unique instance IDs across 222 distinct `repo` values, with a nonempty `FAIL_TO_PASS` list on every row. The [`dataset manifest`](dataset-manifest.json) records the per-shard counts and hashes. This is the original SWE-smith release line specified for the experiment. [Pinned dataset card](https://huggingface.co/datasets/SWE-bench/SWE-smith/blob/ea6d7173829c7ec8fa16c22055699ff2e9188091/README.md)

The Parquet columns are `instance_id`, `patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`, `image_name`, `repo`, and `problem_statement`. Here `patch` injects the bug. The published table has no `base_commit` column, so resolve the buggy tree through the repository profile and instance ref, and record the actual checkout SHA and image digest. Verify the direction of the diff against both trees before mapping lines. [SWE-smith harness documetntation](https://swesmith.com/guides/harnesses/)

Counting distinct raw `FAIL_TO_PASS` selectors per instance gives 46,388 instances with multiple selectors (78.4%) and 4,176,967 instance–selector pairs overall. The largest list has 10,936 distinct selectors. These are metadata counts before runner collection or any execution.

To reproduce the download, run this from the experiment directory with the Hugging Face CLI available and compare the downloaded shard hashes with the manifest.

```powershell
hf download SWE-bench/SWE-smith --repo-type dataset --revision ea6d7173829c7ec8fa16c22055699ff2e9188091 --include 'data/train-*.parquet' --local-dir .
```

## 1. Fix the two endpoints

Given a buggy checkout, a **gold interface function**, and its observed failure, the agent ranks production functions as root causes. Each annotation is scoped to one instance and one concrete failing test case.

| Role | Ground truth and provenance |
| --- | --- |
| Gold interface `I` | Resolve the selected `FAIL_TO_PASS` entry through test collection to the exact function or method. Record the selector, parameter ID, qualified symbol, and source span. Use the same interface for every localization arm. If collection or fixture setup prevents the function from running, retain the record separately from the first pilot. |
| Benchmark root `R` | Use the production function enclosing the injected change. In the pilot, require an unambiguous single-function mapping from the diff and require restoration of that function's injected change to make the selected test pass. Store the mapping on both versions. Cases with unresolved mappings or interacting edits go to a separate cohort. |
| Observation `O` | Record the actual failed assertion, exception, or diagnostic from the gold interface's execution. A production reporter, when present, is a separately annotated symbol. A reporter can also be the benchmark root. |

This gives the constructor both endpoints. SWE-smith supplies the failing-test identity and the injected change. The experiment supplies the function mapping, replay evidence, and a diagnostic path with an explicit acceptance rule. The agent gets `I` and its buggy-side evidence. The scorer uses `R` to assess root-cause localization. [SWE-smith validation protocol](https://swesmith.com/guides/harnesses/)

## 2. Run the failing tests and collect relevant entities

**Inventory the selectors.** Resolve each listed selector to an exact collected case, preserving parameter IDs. Keep a separate record for each `(instance_id, selector)`. Two parameterizations can share the same gold interface function and exercise different branches. For the pilot, take instances with one to five selectors and attempt every selector. At scale, inventory all selectors and set a reproducible per-instance execution budget. When sampling is necessary, stratify by test file and function before sampling parameterizations with a fixed seed. Record the attempted and unattempted selectors and their counts.

1. **Screen each selected test in isolation.** Use the pinned Linux SWE-smith profile. Run the exact selector in a fresh process on each tree, with its normal fixtures and runner configuration. Collect the test outcome and phase, failure fingerprint, executed production symbols, and observed caller and callee pairs. Keep fixture and helper events with the test that triggered them. Record suite-dependent failures, collection errors, timeouts, and failures that do not reproduce in isolation as separate outcomes. An executed symbol is eligible for closer inspection. The trace also surfaces indirect calls and shared setup that may be absent from the test body.
2. **Choose which tests warrant a detailed trace.** Within an instance, prioritize distinct failure fingerprints or distinct routes through production code. Reserve some detailed traces for tests with matching fingerprints and call routes, since they can carry different values or follow different branches inside the same functions. Retain separate records until the mechanisms can be compared. Run the shortlisted cases three times per version for stable baselines. Confirm source imports and patch direction, and freeze the failure fingerprint before interventions. Where available, run a related `PASS_TO_PASS` case that reaches the same component to contrast shared activity with the failing behavior.
3. **Select entities per observation.** Use iCoRe's [`DependencyIndex`](../../../scripts/test_retrieval/static_dependencies.py) for the static baseline. In the detailed paired traces, follow dependencies forward from the changed event at `R` and backward from the exact failed operand, branch, or exception at `O`. Retain the connected events that both depend on the divergence and contribute to the observation. Track arguments, returns, branch decisions, exceptions, and state written in one invocation and read in another. Align events by qualified symbol, call site, and invocation context. Record omitted calls or decisions as explicit gaps until a replay can support the link. A function used only for setup, logging, or formatting can appear in the execution inventory. Promote it to the path when its behavior contributes to this failure. Keep the gold interface fixed and identify the relevant production call or state transfer separately.
4. **Verify, then consolidate.** Apply the replay checks in §3 to each candidate. Export the investigation route from `I` to `R` and the executable propagation chain from `R` to `O` within that selected test. Verify dependencies at the invocation level and project to functions for training. If multiple selectors yield the same verified mechanism, retain all selectors as evidence and use a representative demonstration or cap their combined training weight. Keep separate demonstrations for distinct triggers or propagation paths, and keep all selectors from an instance in the same split. This extends the two-view representation from the [earlier Pylint run](../KNOWN_ENDPOINT_PATH_RUN.md).

### Extract production functions from one test

1. **Map runtime frames to source.** Index the checkout's production modules and test and fixture definitions with the AST. Resolve the exact collected pytest node and trace its setup, call, and teardown. For the first synchronous Python cases, use `sys.setprofile` for the initial call inventory. Resolve each project frame using its repository-relative filename and code location, then attach the AST-qualified function name and source span. Check the filename resolves to the intended checkout. Keep test and fixture frames as context and mark library, native, or untraced process boundaries explicitly. [pytest node IDs](https://docs.pytest.org/en/stable/example/markers.html#selecting-tests-based-on-their-node-id) · [Python profiling and tracing hooks](https://docs.python.org/3/library/sys.html#sys.setprofile)
2. **Keep the invocation as well as the function.** Record the exact selector, phase, function ID, invocation ID, caller invocation, call site, and outcome. A loop can invoke one helper repeatedly, while only one invocation supplies the failed assertion. Use the detailed trace to distinguish a normal return from exception unwinding. For a fixture-produced object consumed in the test body, record the relevant object and field across both events. Keep repeated calls distinct until projecting the verified path to function names.
3. **Recover the dependency from the failed check.** Find the failed assertion operand, the relevant field of a returned object, or the exception that reached the runner. Use source-level assignment and call sites to propose links back to their producers. Place targeted probes to confirm actual argument binding, returned values, the field's last relevant write, or the branch outcome. A call inventory supplies reachability. The recorded value or decision and its consumer establish the proposed path. Label entities separately as `executed`, `trace_supported`, or `replay_verified`, and preserve the evidence for each selector.

### Compare and verify entities across different tests

Compare by repository-relative qualified symbol first, then by the mutation, invocation context, and the value or decision passed to the next entity. Keep a per-selector graph and attach selector IDs to any shared edge. A union of call inventories is useful for scheduling probes. An intersection can suggest a common function to inspect, while test-specific wrappers and consumers stay on their own paths. Each selector supplies its own values and expected failure fingerprint for verification.

| What the tests show | Verification and export |
| --- | --- |
| Same root and same state transition, different wrappers or assertions | Reuse the probe location. Verify the transition against each test's own invocation and observation. Share the verified segment with selector-specific evidence and retain the distinct routes from the tests. |
| Same function, different branch, input, invocation, or consumer | Place a probe at the relevant call or expression in each test. Keep distinct roles or edges when the replays confirm distinct mechanisms. |
| A function is reached in one test only, or is reached in a passing control | Check whether it carries the divergent state for the relevant test. Keep a verified test-specific edge even when other tests skip it. A passing control can help distinguish ordinary activity from a divergent invocation. |
| A different patched function appears responsible | For a multi-function patch, restore and reinject each candidate edit independently for the affected test. Assign a test-specific root only when both checks pass. Leave interacting edits or ambiguous results for a separate cohort. |

Modify behavior only in disposable replay copies, at one observed boundary at a time. For a call argument, substitute the captured value at the specified call site before consumption. For a return, substitute the result after the callee runs so its side effects still occur. For a predicate or field read, intercept the evaluated decision or value once, preserving the surrounding evaluation. Match the selector, phase, call site, and invocation context, then check that the probe fires exactly once. Run the same-value control, rescue, and recreation described in §3. Keep the original test assertions and production reporter in place.

**Two-entry pilot candidate.** `pytest-dev__iniconfig.16793ead.func_basic__fku2qhix` lists `test_iniconfig_get_convert` and `test_section_get` in `testing/test_iniconfig.py`. Its patch changes conversion handling in `IniConfig.get`. Trace each test to see whether it reaches that function directly or through a wrapper, and whether each failure consumes the same incorrect conversion result. If the two tests share the divergence, use a probe at the same source location with each test's own invocation, value, and expected observation. This candidate was identified from the local metadata and diff. Its runtime routes remain to be measured.

## 3. Verify the pathway by replaying its intermediate steps

### Deterministic call-path baseline

Run [the call-path tracer](trace_call_path.py) on the buggy instance checkout with the original injecting diff and the exact `FAIL_TO_PASS` selector. Run it with the repository's pytest environment, including its normal import configuration.

```bash
python trace_call_path.py --checkout /path/to/buggy-checkout --patch /path/to/mutation.diff --selector 'testing/test_iniconfig.py::test_iniconfig_lineof' --output /path/to/route.json
```

The tracer maps added or replaced Python lines to their enclosing functions on the buggy checkout and checks that the added text matches that checkout. When a mapped function is called, it records the actual stack back to the collected test function. It exports all distinct observed routes with call sites and hit counts. For a failed test, it selects the shortest route, breaking ties by the recorded frame tuples. Each route comes from one observed stack, which avoids joining edges from different invocations. The prototype observes synchronous Python calls during the test body. Setup-only origins, deletion-only mutations, and work in other threads or processes can require different instrumentation.

Compare the oracle and tracer on the same checkout, selector, and patch. Report whether the tracer reaches a patch-mapped function, and whether the oracle's call edges and full route appear in a single observed stack. A route that reaches the patch can still come from an invocation unrelated to the failed assertion. Use the later replay checks to verify propagation and compare the two methods on that criterion separately. Preserve failed tests with no observed route in the coverage denominator.

### Verify the root before testing the pathway

Restore only the injected edit at `R` on the buggy tree. Require the predicted first divergence to disappear and the unchanged test to pass. Then inject the same edit at `R` on the working tree and require the first divergence and original failure fingerprint to recur. Save the exact changed lines and trace evidence. This establishes the benchmark root for this test. A downstream state repair may also make the test pass, which makes it a useful place to test propagation.

### Test each claimed intermediate step in both directions

For each intermediate state or branch on the proposed chain, specify the invocation, the value or decision to replace, and the immediate successor event or condition expected to change. Start with scalar arguments, returns, and simple predicates that can be replaced at a precise program point. Use values observed in the paired runs. When an omitted branch has no safe, comparable replacement point, record that link as unresolved.

| Replay | Change at the intermediate point | Required result |
| --- | --- | --- |
| Buggy tree, same-value control | Write back the buggy value through the intervention hook | Same local successor and same failure fingerprint |
| Buggy tree, rescue | Substitute the working value | The root divergence still occurs upstream. The claimed successor changes in the predicted way, the observed failure disappears, and the original test passes. |
| Working tree, same-value control | Write back the working value through the same hook | Same local successor and passing test |
| Working tree, recreation | Substitute the buggy value | The root behaves as in the working run. The claimed successor changes in the predicted way and the original failure fingerprint recurs. |

For every intervention, require exactly the intended dynamic hit and verify the execution prefix through that point. Keep the test, assertion, and reporter logic intact. Reject runs that skip the original test check, change fixtures or inputs, hit the wrong invocation, or produce a different failure. Replay a reachable off-path candidate with an observed, type-compatible substitution when available. If that substitution also recreates the fingerprint, investigate it as an alternative route. Trace the immediate successor in all four runs so a matching final symptom alone cannot validate the proposed link.

### Acceptance and evidence record

- **`verified`** means the endpoints and root replay pass, every claimed propagation link has an observed dependency and the expected replay evidence, and there are no unsupported gaps. Include at least one independently intervenable production intermediate in the main pathway cohort. Keep direct root-to-observation cases as a separate stratum. The verification applies to the selected test, execution environment, and substitutions used in the replay.
- **`trace_supported`** means the observed dependencies support a candidate route with at least one unresolved intervention. Use this as an evaluation baseline. Mark a path `rejected` if replay contradicts a claimed transition. Keep missing observations and unsupported substitutions distinct from contradictions.
- Store source hashes and spans, image digest, the exact selector, event IDs, intervention point and value, expected and actual successor, hit count, failure fingerprint, and a replay command for each run. Accept multiple routes when they independently pass the protocol. If branches only work together, retain the case as a possible joint mechanism and defer it from the single-chain cohort.

**First replay candidate.** Shard `00000` contains `pytest-dev__iniconfig.16793ead.func_basic__4p5vyz64`. The gold interface selector is `testing/test_iniconfig.py::test_iniconfig_lineof`. The diff reverses the section-name argument that `SectionWrapper.lineof` passes to the config object's `lineof` method. First check the lookup and assertion empirically. If the expected divergence is observed, restore or recreate the argument at the callee boundary and repeat at the downstream lookup result if it can be intercepted safely. Require the predicted successor in each replay. This example comes from local diff and test metadata. Its runtime chain remains unverified.

## 4. Research contribution and comparisons

The proposed contribution is **root-cause localization supervision from replay-verified paths between benchmark-defined endpoints**. The claim to test is that validating intermediate steps yields better training labels and improves root localization when the visible failure points at another function. Prior work already uses paired execution, slicing, and interventions for debugging. The proposed distinction is to reuse SWE-smith's test and mutation labels to construct repeatable supervision, validate intermediate transitions with rescue and recreation replays, and measure the downstream value of that supervision. [Cleve and Zeller, Locating Causes of Program Failures](https://doi.org/10.1145/1062455.1062522) · [Causality-Guided Adaptive Interventional Debugging](https://arxiv.org/abs/2003.09539) · [InPreSS and dual slicing](https://conf.researchr.org/details/icse-2023/icse-2023-technical-track/184/Responsibility-in-Context-On-Applicability-of-Slicing-in-Semantic-Regression-Analysi)

Evaluate pathway correctness separately from localization. With both endpoints fixed, compare static routes, paired slices, and replay-filtered paths on a reviewed sample. Measure how many candidate entities survive screening and how many reviewed path entities each stage retains. On instances with multiple failing tests, compare using the first listed selector with the budgeted execution screen. Report how many distinct verified mechanisms each selection rule finds at a given execution cost. For the verifier challenge set, propose plausible wrong paths that keep both endpoints and swap an intermediate producer, branch, or invocation. Establish which claims are contradicted using independent source review and held-out replays. Preserve valid alternatives and unresolved cases in separate categories. Report supported-path precision, rejection of contradicted paths, abstention, and replay cost. Where the dataset supports it, also pair instances with the same interface and similar failure fingerprints but different verified roots, to test whether localization transfers beyond the symptom.

## 5. Train and evaluate in two stages

### Start with one localization episode

Use the SWE-smith repository profile to start the pinned Linux image and resolve the buggy checkout for one instance. The upstream `registry.get_from_inst(row).get_container(row)` initializes the task by checking out its instance ref. Verify the actual revision and image digest before exporting source. For the rollout and trainer adapter, use SkyRL's Mini-SWE-Agent example as a reference. Its example prepares SWE-Gym and evaluates submitted patches. This experiment needs a SWE-smith loader and a localization scorer. [SWE-smith container setup](https://github.com/SWE-bench/SWE-smith/blob/main/swesmith/profiles/base.py#L453-L480) · [SkyRL Mini-SWE-Agent example](https://github.com/NovaSky-AI/SkyRL/blob/main/examples/train/mini_swe_agent/README.md)

| Environment call | First implementation |
| --- | --- |
| `reset(case_id)` | Open a fresh episode for an opaque case ID tied to one exact selector. Return the gold interface, buggy failure output, and the tool budget. |
| `step(read_symbol)` or `step(find_references)` | Return a bounded buggy source span or structural neighbors from `DependencyIndex`. Issue evidence IDs scoped to the current episode. |
| `step(get_observations)` | Return bounded observations from the selected buggy test. Start with a frozen trace collected independently of the root label. |
| `step(submit)` | Accept the ranked production function IDs and optional diagnostic relations with evidence references. Terminate the episode and score the submission privately. |

Implement the first episode over a read-only bundle of buggy source and cached observations. Run the paired tests and verification replays once during dataset construction. Mount or export only the bundle for policy rollouts. Keep the image's Git history, patch, working tree, private replay data, root labels, and scorer outside the actor's access. If a later experiment needs live probes, give each rollout a fresh container or resettable checkout, and use bounded test commands with a timeout. Reuse the image and dependencies across rollouts. The existing iCoRe `PytestRunner` and `classify` functions are starting points for parsing test results. The existing `evaluate_frozen` path expects a SWE-bench `base_commit` and applies a repair patch, so SWE-smith needs its own runtime adapter. [iCoRe execution harness](../../../scripts/brt_harness/execution.py)

Start with terminal reward `1` when the top-ranked function matches the privately verified benchmark root and `0` for a valid incorrect submission. Keep infrastructure failures and truncation explicit in the rollout record. After testing this baseline, add a separately logged score for verified diagnostic relations and the evidence cited by the agent. A valid alternative path can earn credit when it matches accepted evidence. Keep verified localization and repair outcomes as separate scores. In a repair episode, score a submitted patch in a fresh buggy environment with the official harness and both `FAIL_TO_PASS` and `PASS_TO_PASS` checks. [SWE-smith evaluation harness](https://swesmith.com/guides/harnesses/)

Run a scripted episode on one `iniconfig` case before connecting a trainer. Verify the buggy failure and working pass, the expected rewards for correct and incorrect roots, repeatable reset behavior, budget exhaustion, and that public observations do not expose private labels. Then run a small group of model rollouts on the same cases. Log reward variation across completions for each case, termination reasons, and assistant token spans. Apply actor loss only to assistant actions. A scalar completion reward is enough for the terminal baseline. Learning from rewards assigned to individual actions requires the trainer to preserve and use that alignment. [Existing iCoRe actor and scoring design](../proposal/RQ2_RQ3_PRACTICAL_IMPLEMENTATION_PLAN.md#5-actor-environment-contract)

**Initial SWE-smith training.** Assign splits before any training or annotation. Group all cases and selectors from the same repository across mirrored revisions and keep near-duplicate mutations together. Use only train-group SWE-smith tasks to build or select successful repair trajectories for a common agent checkpoint. Preserve repository-held-out development and evaluation sets across both stages.

**Later localization training.** Branch from that same checkpoint. Use identical accepted cases, observations, tools, and training budgets for the trained arms. Serve the agent an opaque ID, the buggy source, the gold interface and failure output, and permitted buggy-side tools. Keep the patch, working tree, known origin, interventions, and reference traces in the constructor and scorer. Use buggy-side evidence references in demonstrations. Exclude generated `problem_statement` in the primary controlled experiment and test it separately after checking for origin hints.

| Arm | Supervision |
| --- | --- |
| Common checkpoint | Evaluate the initial agent before localization training. |
| Endpoint only | Train on the verified production function. |
| Endpoint and static route | Add the graph route as an auxiliary target. |
| Endpoint and paired slice | Add the trace-derived candidate before intervention filtering. |
| Endpoint and replay-verified path | Add the verified investigation route and propagation chain. |

Use the common eligible subset for the controlled comparison and publish the coverage of each method on the full cohort. Format paired-slice and replay-verified labels with the same fields and similar length limits. Track training tokens and hold output and tool budgets constant at evaluation. Compare these two arms to estimate the added value of intervention-based verification. Report how often the two targets differ on this subset so that an arm with the same labels is interpreted as such.

Report top-1 and top-5 function accuracy, mean reciprocal rank, tool calls, and tokens. On cases where the reporter and origin differ, report the fraction with the reporter ranked first and accuracy when the origin is absent from the traceback. Break out same-function and assertion-only failures. Audit the evidence and direction of predicted propagation links, including alternative valid paths. Report extraction yield by stage and use paired differences with repository-level bootstrap intervals on the scaled holdout.

## 6. Execution order and gates

1. **One complete case.** Reproduce the `iniconfig` test in Linux and verify both endpoint mappings. Save the root restoration and reinjection, the four intermediate replays, and a plausible route that the checks reject. Confirm the record can be independently replayed from its saved evidence and commands.
2. **Ten-instance engineering pilot.** Use 10 single-function mutations across at least three small Python repositories. Include instances with multiple failing selectors, an assertion-only failure, a separate production reporter, and a case where the reporter is the root. Review every candidate chain that advances to detailed tracing. Log execution coverage, where interventions are infeasible, how often paths are rejected or left unresolved, and how many replays each accepted path takes.
3. **Scaled comparison.** Freeze the acceptance rules after the pilot, then construct a few hundred cases using the preassigned splits. Run the pathway verifier evaluation and the matched training arms. Assess held-out top-1 accuracy and reporter-first errors together with pathway correctness and construction yield. Keep the engineering pilot out of the performance holdout.

Keep the raw snapshot in `data/`. Store derived artifacts under an ignored output root such as `data/derived/`, with separate public actor bundles and private annotations. Keep `cases`, `rejections`, and `splits` keyed by the pinned dataset revision and exact test selector.
