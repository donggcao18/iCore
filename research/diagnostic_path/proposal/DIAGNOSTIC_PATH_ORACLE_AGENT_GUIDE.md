# Alternative oracle protocol: diagnosis using test and production patches

This is the earlier privileged protocol. The active [test-patch-only agent guide](DIAGNOSTIC_PATH_AGENT_GUIDE.md) excludes the production patch and working version; use that guide for the current Pylint investigation.

First pilot protocol, 6 November 2026. Companion to [RQ2](RQ2_DIAGNOSTIC_PATH_CONSTRUCTION.md) and the [research objective](CONCISE_RESEARCH_PROPOSAL.md). This is an executable investigation brief for an agent, not a claim that any case has already been verified.

## 1. Assignment and purpose

Act as a privileged, offline diagnostic constructor. You may inspect the test patch, production-code patch, repository, and both buggy and working versions. Starting from a concrete test witness, iteratively investigate how the exercised production behavior leads to the failure and where the relevant defect originates.

Here, “closed agent” means this bounded constructor role; it imposes no model-provider requirement. Do not delegate unless separately requested. Return a concise, source-grounded investigation record, not a transcript of private reasoning.

The deliverable is **a diagnostic explanation with a path projection**, not just `test -> function A -> function B`. It must tell another agent:

- Which operation and conditions activate the defect.
- Where state or control first becomes incorrect, or a required operation is omitted.
- What carries that effect through the program and where it becomes observable.
- Which source and execution evidence support those statements.
- How these facts guide the requested final generation task.

For BRT generation, the intended value is to identify a reachable trigger, suitable setup, and an assertion that distinguishes buggy from working behavior. For repair generation, it is to identify the responsible transition, the behavior to restore, and the constraints a repair must preserve. These are hypotheses about utility; locating the right function alone may already be enough. Section 8 specifies the comparison needed to find out.

## 2. Case inputs and version contract

Before investigating, complete this case header. Record unavailable fields explicitly; never invent them.

```yaml
case_id: <dataset instance ID>
dataset_and_release: <name and pinned source>
repository: <local path and repository identity>
base_revision: <resolved commit>
production_patch: <file or dataset field>
production_patch_direction: <repair | inject_bug | unknown>
test_patch: <file or dataset field; absent if unavailable>
selected_test: <exact selector, including parameterization>
bug_report: <source or absent>
environment: <runtime, dependencies, setup instructions>
downstream_task: <BRT | repair | diagnosis>
handoff_policy: <oracle_mechanism | restricted>
investigation_budget: <time/tool/run limits, or report actual cost>
```

Use `B` for buggy production code and `W` for working production code throughout. Verify patch direction from provenance and source, then check it through execution when possible:

| Input convention | B | W |
| --- | --- | --- |
| Verified repair patch on buggy base | Base production code | Base plus production fix |
| Verified injecting patch on already buggy instance revision | Instance production code | Verified reversal of injection, or verified original revision |
| Unknown or conflicting provenance | Unresolved | Unresolved |

The existing RQ2 note targets SWE-smith; this workspace also uses SWE-bench-style repair patches. Do not transfer the meaning of `patch` or `base_commit` between them without checking. A repair location is a candidate for the defect origin, not proof of that origin: a fix may compensate downstream, add a guard, or refactor several locations.

Treat the test patch as the **investigation entry artifact**. A diff is not a complete executable test: reconstruct the changed test, imports, fixtures, helpers, configuration, and parameters. Distinguish the test driver `T`, relevant production entry `E`, exact observation `O`, and origin candidate `R`. Inspect relevant setup as well as the test body.

Prepare isolated snapshots; preserve the user's checkout. In the ordinary repair-patch case, run the same reconstructed witness on `B + test changes` and `W + test changes`, with the production difference isolated. If the test patch also changes production behavior, dependencies, or fixtures materially, separate and document those changes or mark the comparison confounded. Record the actual composition of each snapshot.

## 3. Investigation procedure

### A. Establish the witness

1. Read the case metadata and both patches. Resolve each relevant hunk to complete source in its own revision; old and new line numbers are different coordinates.
2. Choose one test case and one observation. If the patch contains several tests, list them and select explicitly; do not assume the first test explains every production edit.
3. Record the trigger inputs, relevant fixtures/setup, production operation, and asserted or exceptional behavior. An import/setup failure may occur before the test body executes.
4. Run the selected witness on both versions in equivalent fresh environments and repeat to check stability. Save exact commands, exit codes, logs, revision/source hashes, and environment details. Require the intended failure on B and successful intended behavior on W for an execution-backed contrast.
5. If execution is unavailable, continue source investigation, but label the result source-supported and leave runtime claims unverified. Infrastructure failure is not reproduction of the defect.

### B. Navigate from the test toward the responsible transition

Use the test as the starting interface for investigation, while recording the production entry separately. Follow the value, state, or control decision relevant to O; do not simply expand every call.

For each iteration:

1. State the unresolved connection, for example: “Which operation sets the field consumed by this assertion?”
2. Inspect the smallest relevant source region and its callers, callees, reads/writes, or configuration dependencies. Include inheritance, dispatch, fixtures, and prior operations when they matter.
3. Record a candidate relation with its carrier: return value, argument, object field, collection item, exception, branch condition, or missing operation.
4. Check the relation with source and selective observations. Add a call/return probe, field snapshot, or branch observation only where it resolves a concrete gap. Preserve the original failure under instrumentation.
5. Accept, revise, reject, or leave the relation unresolved. Choose the next inspection from the remaining gap.

Keep a short action log: question, inspected span or command, evidence result, next unresolved gap. This records an auditable investigation history without requiring one prescribed search order.

Use the production patch to focus attention and compare behavior. Do not stop merely because you reached an edited function, and do not invent a bridge to it. Inspect unedited intermediate code. For every origin candidate ask: does it introduce the wrong behavior, propagate an already wrong input, or merely observe it?

### C. Explain forward from origin to observation

Construct two separate views:

```text
Navigation view: T -> E -> inspected dependencies -> candidate R
Mechanism view: trigger/setup -> R -> affected state/control -> consumers -> O
```

Navigation describes where investigation went; the mechanism describes how the failure develops. They can have different orders, revisit a function, or cross separate calls through shared state. A structural call edge does not establish causal influence.

For every mechanism node record a revision-qualified source span, symbol, role, condition, and concrete state/control proposition. For every edge record the carrier, transformation or prerequisite, and supporting evidence. Distinguish invocations when a symbol executes repeatedly. Record absent events for omissions rather than fabricating a buggy-side execution of deleted code.

Use these evidence labels, independently where appropriate:

| Label | Meaning |
| --- | --- |
| `source_supported` | Inspected source supports the relation under stated assumptions |
| `observed` | A recorded execution shows the specified event/property |
| `intervention_supported` | A documented controlled experiment supports the specified claim |
| `unresolved` | Evidence is absent, incomplete, or conflicting |
| `contradicted` | Source or execution conflicts with the proposed claim |

A trace that visits A and B supports visitation, not automatically a value-dependence edge between them. A changed downstream value is not automatically the origin. Record opaque boundaries and compressed edges with their underlying source/observation references.

### D. Check the explanation

Compare B and W at the suspected transition and relevant consumers. Match invocation context and semantic properties, not raw object addresses or event indexes. Identify why the observed difference matters to O.

When feasible, test a narrow restoration or change subset in a fresh snapshot. Record the exact intervention, hypothesis, command, and outcome, including a different crash or an inconclusive run. Whole-patch restoration supports the overall contrast; it does not independently validate every intermediate edge. A downstream rescue establishes a way to block the symptom, not necessarily the original defect.

Preserve alternative supported routes and multiple origins if needed. Do not force a single linear chain through branches or interacting edits. Stop with a partial result if the budget is exhausted or a critical boundary remains opaque; list the missing evidence and next useful check.

## 4. Required private output: `<case_id>.diagnosis.md`

Use this structure for each case. All evidence links must resolve to saved artifacts or revision-qualified source; cite line spans in the record even if a local clickable link points only to the first line.

```markdown
# <case_id>: <one-sentence failure mechanism>

## Status and scope
Status: execution-backed candidate | source-supported candidate | unresolved
Review: not reviewed | independently reviewed | human adjudicated
Selected test, observation, applicable inputs, alternatives, and limitations.

## Provenance and reproduction
Dataset, patch direction, B/W identities, patch hashes, environment,
snapshot composition, commands, repeat outcomes, and evidence references.

## Entry, trigger, and observation
T: test selector and setup
E: relevant production operation(s)
C: triggering conditions
O: exact actual behavior and expected/reference behavior, with its basis

## Diagnostic core
| ID | Role | Revision / path / symbol / span / invocation | Condition and state/control proposition | Evidence |
| --- | --- | --- | --- | --- |

## Supported relations
| From -> To | Carrier and transformation/prerequisite | Evidence labels | Evidence references | Gaps |
| --- | --- | --- | --- | --- |

## Readable projections
Navigation route, marking actual inspected steps versus proposed links.
Forward mechanism, retaining branches and repeated invocations when needed.
Short explanation of why this origin produces this observation.

## Checks, alternatives, and uncertainty
Origin attribution versus repair-site evidence; interventions and outcomes;
rejected hypotheses; opaque boundaries; next useful checks.

## Generation implications
BRT: reachable trigger, minimal relevant setup, observable distinction,
assertion basis, and compatibility constraints.
Repair: responsible transition, behavior to restore, and invariants.
Mark the irrelevant task as not applicable.

## Investigation log and cost
Concise actions/evidence, run count, elapsed time, and model/tool cost if known.
```

Candidate status does not confer “gold” quality. Apply the reference-set review criteria in RQ2 before upgrading an annotation. A reviewer should check whether each relation explains this observation, rather than merely connecting patched code to a test.

## 5. Required downstream output: `<case_id>.handoff.md`

Prepare a compact, self-contained context package for the final generation agent. Include the following fields, filtered by the declared handoff policy:

1. **Task and scope:** what to generate and which behavior the diagnosis covers.
2. **Entry and trigger:** production operation, input/state conditions, and necessary setup.
3. **Mechanism:** a readable route plus one state/control statement per relevant transition; include small buggy-source excerpts or explicit source anchors where needed.
4. **Observable distinction:** actual buggy behavior and the expected behavior, with the source of that expectation. A working-run value is evidence for this witness, not automatically a universal specification.
5. **Generation guidance:** for BRT, how to exercise and observe the distinction using available test conventions; for repair, the transition and invariants to address.
6. **Evidence and limits:** compact evidence references, runtime verification status, unsupported links, and known alternatives.
7. **Provenance:** `privileged_constructor: true`, task, handoff policy, and which fields derive from the hidden test or production patch.

Do not place the full investigation log in the final-model prompt. Do not substitute a finished reference test or developer fix for the diagnostic explanation.

`oracle_mechanism` is the first pilot policy: the handoff may include privileged, witness-derived trigger and expected-behavior details. Label the resulting experiment **oracle diagnosis-conditioned generation**. This is useful for testing whether a high-quality explanation can help, but it cannot establish independent discovery of a reproducer.

`restricted` is for a declared inference-time observation policy: exclude hidden test bodies/selectors, test-specific inputs/assertions, fixed implementations, reference-run values, and privileged derived facts unavailable under that policy. Record omissions and their effects on completeness. Removing raw patches alone does not remove their information. If the diagnosis cannot remain useful without those details, report that limitation; a blind setting requires a diagnosis acquired from allowed inputs, not a claim that this privileged constructor was blind.

## 6. Illustrative output: why a path alone is insufficient

This toy example is invented, not a verified repository case. Suppose `normalize_count(0)` incorrectly returns `0` instead of `1`; `build_job` stores that return in `job['count']`; `rate` divides `10` by this field. The witness calls `rate(0)` and expects `10`.

**Path-only projection:** `test_rate_zero -> rate -> build_job -> normalize_count`.

**Useful oracle handoff:** “Exercise `rate` with a zero count. `normalize_count` leaves the count at zero; `build_job` carries that value into `job['count']`; `rate` consumes it as a divisor and raises `ZeroDivisionError`. The working behavior normalizes zero to one, producing `10` for this input. A BRT should assert the intended result through `rate`, rather than treating the exception as expected success. A repair should restore the zero-count normalization while preserving nonzero behavior.”

For a real case, attach source spans and observed evidence to each statement. The function list identifies locations; the explanation supplies the trigger, state carrier, failure mechanism, and generation-relevant distinction. Whether that extra information improves generation is measured below.

## 7. Completion checks

- [ ] Patch direction and both source versions are explicit.
- [ ] One concrete witness, production entry, and observation are identified.
- [ ] Execution claims have saved commands/logs; unavailable checks are marked.
- [ ] The origin is distinguished from a changed function or downstream repair site.
- [ ] The diagnostic core connects the responsible transition to O through supported relations, or is explicitly partial.
- [ ] Every relation identifies state/control, evidence strength, and relevant conditions.
- [ ] Alternatives, omissions, setup effects, and opaque boundaries are retained where relevant.
- [ ] The handoff explains how the mechanism informs final generation.
- [ ] Privileged information and handoff restrictions are documented.
- [ ] Independent review status is explicit; self-review is not called independent review.

## 8. Verify the purpose in final generation before scaling

Freeze the downstream task, model, cases, prompts, sampling settings, candidate count, execution feedback, and evaluation budget. On the same cases compare:

| Arm | Added diagnostic context | Question |
| --- | --- | --- |
| A: location/source baseline | Oracle-selected relevant buggy code and allowed reference-test context | Is accurate code selection already sufficient? |
| B: path only | A plus the ordered symbol/path projection | Does ordering locations add value? |
| C: mechanism handoff | A plus trigger, state/control transitions, observation, and evidence summary | Does explaining the mechanism help beyond locations and ordering? |
| D: facts without mechanism | A plus the same trigger, expected-behavior facts, evidence summary, and extra source used in C, but without the connecting transition explanation | Is any gain mainly due to additional input/oracle facts? |

Keep the source inventory identical across arms; any extra snippets required by C must also be available in the controls. Record actual prompt tokens and run a comparable-budget variant. Use the same privilege policy in every arm and report which facts each receives. D is a useful control, though remaining facts can still imply some mechanism; describe that limitation.

For BRT, generate a fresh candidate with expected-behavior assertions and evaluate the frozen candidate on B and W under the same harness. Report intended bug failures on B, passes on W, invalid/infrastructure outcomes, unrelated failures, and duplication of the supplied reference witness. Oracle test-conditioned success is not evidence of independently discovering that witness. For repair, evaluate the generated patch on the selected witness and a fixed, declared regression suite; never use the known production fix as the candidate.

Report paired per-case results and uncertainty, failure categories, annotation coverage, and construction cost. Include failed or unresolved construction attempts in the construction denominator. Inspect cases where the handoff misled generation. A useful pilot result is an improvement of C over A/B, with D testing the contribution of mechanism beyond added facts. No improvement is also informative: revise the artifact or its proposed purpose before training a diagnostic agent at scale.

## 9. Invocation brief

Give another agent this file and a completed case header, then use:

> Follow DIAGNOSTIC_PATH_AGENT_GUIDE.md for the specified case. Inspect the test patch as the investigation entry and the production patch as privileged contrast evidence. Iteratively trace the failure-relevant state/control dependencies to the responsible transition, verify what you can, and distinguish source-supported claims from observed or experimentally checked claims. Produce the private diagnosis and the policy-filtered generation handoff. Return a partial/unresolved result when evidence is insufficient. Do not claim a correct path from patch overlap or call connectivity alone.
