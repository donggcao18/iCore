# Earlier protocol: test-patch-only localization for Pylint Verified

The active [agent guide](DIAGNOSTIC_PATH_AGENT_GUIDE.md) now supplies entry points, root-cause candidates, and extracted buggy functions as inputs. This earlier protocol is retained for the prior six-instance investigation.

Active protocol, 6 November 2026. This replaces the earlier production-patch-guided assignment. The [alternative oracle protocol](DIAGNOSTIC_PATH_ORACLE_AGENT_GUIDE.md) is retained separately and is not the policy for this task.

## 1. Assignment and allowed information

For every `pylint-dev/pylint` instance in the specified local Verified CSV, inspect **only the test patch as the benchmark's diagnostic clue**, resolve its test driver and production entry in that instance's buggy repository, then iteratively trace failure-relevant dependencies to the root-cause candidate. Produce one combined JSONL file with one record per instance.

Allowed inputs:

- Instance ID, repository name, buggy `base_commit`, dataset provenance, and normalized `test_patch`.
- Buggy production source, existing tests, fixtures, and configuration at that instance's pinned base commit.
- Observations obtained by running the test or a documented local probe on buggy source.

Do not inspect the production `patch`, issue text, hints, fixed source, sibling instances' implementations, future Git history, repair-oracle retrieval artifacts, or gold localization labels. Do not use a second checkout as a fixed reference: two buggy copies provide workspace isolation, not a buggy/fixed contrast. Do not delegate unless separately requested.

The CSV reader may parse the serialized row, but must immediately project allowed fields and never expose, hash, interpret, print, or use the production patch. This is an input-use protocol, not a security boundary around the CSV parser. In the workspace's normalized SWT Verified CSV, `test_patch` is the test diff. Raw SWT field conventions differ; use the declared normalized input rather than guessing from field names.

## 2. Verify the checkout and test entry

1. Enumerate all Pylint rows before constructing diagnoses. Keep unsuccessful cases in the output.
2. Confirm each checkout's HEAD equals its `base_commit` and inspect working-tree status. Do not reset or overwrite user changes. Inspect source within this instance only.
3. Reconstruct the changed tests in memory or a separate test-only snapshot using corresponding base source. Preserve imports, enclosing classes, fixtures, parameterization, and helpers. Validate that diff context matches the base.
4. Identify all added/modified tests, including changed branches of an existing test. Record exact selectors and patch-side source spans. Do not silently select one case when several assertions test different behaviors.
5. Resolve the imported/called name. Distinguish `test_driver` from `production_entry_points`. Follow test wrappers to the real production function or constructor; aliases alone are insufficient.
6. Record the trigger, expected outcome stated by the test, and observation to explain. Passing cases are controls; do not assume every added test fails.

Use entry verification `source_verified` when the alias/wrapper chain is checked. Use `runtime_verified` only when execution confirms entry into that function, and state the execution scope. Otherwise use `unresolved` with the missing evidence.

## 3. Iteratively find the failure mechanism

Start from the operation in the test. At each step record the question, inspected source span, evidence, and next unresolved dependency. Follow return values, arguments, state mutation, branch conditions, setup, and omitted operations relevant to the assertion.

```text
Investigation: test driver -> helper -> production entry -> dependency -> origin candidate
Explanation: trigger -> origin transition/omission -> affected state/control -> consumer -> observation
```

An investigation route is not necessarily a nested call stack. Verify direct calls before describing them as calls. Inspect unedited intermediate code. Each diagnostic node must contain a revision-qualified source span, role, condition, and state/control proposition; each relation must identify its carrier/transformation and evidence. Preserve branches, repeated invocations, alternatives, and unsupported boundaries.

The last visited function is not automatically the origin. Explain where behavior required by the test is first lost or omitted. A generic helper can behave as designed while its caller uses it incorrectly. A missing guard may have several plausible placement sites. Without inspecting the fix, report source-grounded root-cause candidates rather than claiming developer-fix matches.

## 4. Verify without reading the fix

Prefer executing reconstructed patched tests in an isolated buggy snapshot. Save commands, environment, exit status, and exact failures. Do not execute a benchmark production patch.

If dependencies prevent full execution, use focused probes of actual buggy functions. Record whether a probe imports code, compiles selected unmodified AST definitions, supplies synthetic inputs/state, mocks a boundary, or uses another platform's path semantics. Save probe code and observations. An extracted-function probe is not execution of the full regression test.

Distinguish evidence per relation: `source_supported`, `observed_in_isolated_probe`, `observed_in_full_test`, `intervention_supported`, or `unresolved`. A normalized-input or long-option control tests a hypothesis; it does not establish that a production repair or working version passes.

Assign separate statuses:

- `diagnosis_status`: `source_supported_candidate`, `probe_supported_candidate`, or `unresolved`.
- `full_test_status`: `reproduced`, `not_run`, or `infrastructure_failure`.
- `review_status`: `self_reviewed`, `independently_reviewed`, or `human_adjudicated`.

Never claim gold quality, uniqueness, paired-version validation, or independent review without the required evidence. Retain partial results with explicit gaps and next checks.

## 5. Final artifact: one JSONL file

Write UTF-8 `diagnostic_path/pylint_verified_diagnostic_paths.jsonl`. Each nonempty line is one JSON object for one Pylint instance, with no Markdown fences. Include all selected IDs exactly once, including unresolved cases.

Required fields:

| Field | Content |
| --- | --- |
| `schema_version`, `instance_id`, `repo`, `base_commit` | Schema version and pinned case identity |
| `input_policy` | `mode: test_patch_only`, `production_patch_read: false`, allowed inputs and exclusions |
| `test_cases` | Every affected selector, reconstructed test span/hash, test role, and expected assertion/control behavior |
| `production_entry_points` | Qualified symbol, buggy span, alias/wrapper resolution, and entry verification scope |
| `trigger` | Inputs, options, state, fixtures, platform assumptions, and controls |
| `observation` | Expected test behavior, actual observation and its scope, or an explicitly unexecuted prediction |
| `navigation_path` | Ordered source anchors and investigation steps; distinguish exploration from a call trace |
| `root_cause` | Candidate location(s), incorrect transition/omission, justification, and uncertainty |
| `diagnostic_nodes`, `diagnostic_edges` | Forward mechanism with conditions, carriers, evidence references, and support levels |
| `mechanism_summary` | Compact state-bearing explanation connecting origin to observation |
| `source_evidence` | Unique ID, relative path, revision, line span, file hash, and source excerpt |
| `runtime_evidence` | Command, artifact path/hash, scope, environment, observations, and missing verification |
| `generation_handoff` | Entry/trigger, mechanism, observable distinction, BRT implications, evidence limits |
| `limitations`, `diagnosis_status`, `full_test_status`, `review_status` | Explicit confidence and remaining work |

Do not output just paths. Another agent should understand what to invoke, what activates the failure, how it propagates, and what correct behavior the test expects. Test-derived inputs/assertions are privileged for blind BRT discovery: label the handoff `oracle_test_conditioned` even though the production fix was never read.

## 6. Validate and report

- Parse every JSONL line; check required fields, unique IDs, and exact coverage of the input Pylint rows.
- Check spans/hashes against pinned buggy files and reconstructed patched-test text.
- Check every node/edge evidence reference and runtime artifact link/hash.
- Attribute observations to actual execution scope; never present a prediction as a full-test result.
- Confirm checkouts remain unchanged.
- Report the artifact path, instance count, evidence level, and remaining verification gaps.

For utility experiments, compare source-only, path-only, and mechanism handoffs under the same inputs and generation budget as described in the alternative oracle guide. Construction alone does not establish generation value.

## 7. Invocation

> Read every Pylint instance from the declared normalized Verified CSV. Use only allowed metadata and its test patch as benchmark clues. Verify test-to-production entry resolution in that instance's clean buggy checkout, iteratively follow failure-relevant dependencies toward the root-cause candidate, and document source/probe/full-test evidence separately. Do not inspect the production patch, issue text, fixed code, or other instances for clues. Return one validated JSONL record per instance with a state-bearing mechanism and a concise generation handoff; retain unresolved cases explicitly.
