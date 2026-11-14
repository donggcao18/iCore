# Agent guide: find diagnostic paths between supplied entry and root cause

Active assignment, 6 November 2026. The current task is **known-endpoint diagnostic-path construction**. Entry points and root-cause candidates are supplied; the agent must discover and verify the intermediate dependencies and explain the failure mechanism. The [earlier localization protocol](DIAGNOSTIC_PATH_TEST_PATCH_ONLY_AGENT_GUIDE.md) and [paired-version oracle protocol](DIAGNOSTIC_PATH_ORACLE_AGENT_GUIDE.md) are separate settings.

## 1. Inputs and objective

Read `diagnostic_path/pylint_known_endpoints_inputs.jsonl`. Every line provides:

1. `given_root_cause`: a supplied candidate description and buggy-source locations. For this pilot these are prior source/probe-supported candidates, **not gold annotations**.
2. `production_functions`: extracted buggy functions/option definitions selected by the existing production-code oracle. Their selection is privileged; their content is buggy source, not a fix.
3. `test_entry`: test-derived selectors, expected behavior, resolved production entries, and the normalized test patch that establishes the entry/observation.
4. The instance ID, pinned buggy revision, and local checkout.

Use these endpoints to focus investigation. Find the diagnostic path by inspecting actual source and collecting evidence. You are not asked to rediscover localization from scratch, nor to invent a plausible bridge between supplied names.

The output must explain **how the production entry reaches or depends on the origin and how the origin's effect reaches the test observation**. Reaching the supplied root function is insufficient. Confirm which supplied functions matter; unrelated extracted functions need not appear in the path.

## 2. Information boundary

Allowed: the input JSONL, this guide, each instance's own buggy source/tests/fixtures/configuration, and fresh observations from your own probes or tests.

Do not read prior diagnosis outputs, their probe files, the previous handoffs, `scripts/export_pylint_diagnostics.py`, `scripts/probe_pylint_diagnostics.py`, or validation code that embeds previous diagnoses. The input preparer deliberately passes only the endpoint facts, not the previous path, graph, investigation route, or full mechanism summary.

Do not inspect production patches, fixed source, issue text/hints, oracle manifests or patched-code retrieval outputs, future Git history, or sibling implementations for clues. The prepared input already contains the authorized buggy function extracts; inspect missing intermediate source directly in the case's checkout. A second buggy checkout is only an isolation aid, not a working reference.

Treat supplied endpoints as claims to check. If source contradicts an endpoint, return `endpoint_conflict` with evidence rather than forcing a path. If an edge cannot be established, mark a gap. Do not call a candidate gold or uniquely correct. No further delegation is needed for this run.

## 3. Procedure for each instance

1. Verify HEAD equals `base_commit` and note working-tree status. Do not reset or edit the user's checkout.
2. Resolve the supplied test entry, including imports, aliases, test helpers, fixtures, and parameterization. Check the expected observation against the test patch. Distinguish the test driver from the production entry.
3. Read and check the supplied buggy-function extracts against source. Inspect their complete source context if necessary; extracts are starting context, not a closed list of allowed path nodes.
4. Work from the entry forward through relevant calls and state/control dependencies, and from the root backward through relevant callers/consumers. Meet at verified dependencies. Search by concrete unresolved questions: which callback receives this option, which object carries this value, which branch consumes it?
5. Maintain a short `investigation_log`: question, inspected span or command, result, and next gap. Do not output a private reasoning transcript.
6. Build an entry-to-origin route. Label each edge as a direct call, callback registration/dispatch, returned value, field mutation, control prerequisite, or explicitly summarized dependency. Source adjacency and call coverage alone do not establish influence.
7. Build a forward mechanism from the origin's incorrect transition/omission to the observation. Record concrete conditions and values/properties. It can reverse the navigation direction, branch, or revisit a function. Do not require causality to follow nested calls.
8. Where useful, create fresh focused probes or run the reconstructed test in an isolated snapshot. Preserve the original failure criterion. Document mocks, AST extraction, synthetic inputs, platform substitutions, and unobserved boundaries. A probe is not a full regression-test run.
9. Review all edges and prune irrelevant context. Retain supported alternatives. A direct one-function mechanism is valid; do not pad it into a longer path.

For every relation assign `source_supported`, `observed_in_isolated_probe`, `observed_in_full_test`, or `unresolved`. Use observation labels only for what the actual execution establishes; manually joining separately observed endpoints is still a source-supported connection unless the connection itself was observed.

## 4. Output

Write one UTF-8 JSON object per line to:

`diagnostic_path/pylint_verified_known_endpoints_diagnostic_paths.jsonl`

Include all six input instances exactly once, including unresolved/conflicting cases. Do not overwrite the earlier test-only JSONL. Save any new scripts/observations under `diagnostic_path/known_endpoints_run/`.

Required record fields:

| Field | Content |
| --- | --- |
| `schema_version`, `instance_id`, `repo`, `base_commit` | Identity; use schema version `2.0` |
| `input_provenance` | Input-file hash, known-endpoint policy, given root/entry/function identities; acknowledge privileged function selection |
| `endpoint_verification` | Entry/root verification, conflicts, and distinction between supplied facts and newly checked evidence |
| `test_entry` | Driver, production entry, setup, trigger, expected observation; parameterized controls where relevant |
| `entry_to_root_path` | Ordered node IDs with the actual production entry first and an origin candidate last; edge definitions live in `path_edges` |
| `diagnostic_nodes` | ID, repository-relative path, qualified symbol, buggy revision, source span, role, condition, state/control proposition, evidence IDs |
| `path_edges` | Source/target IDs, relation type, carrier/transformation, support, evidence IDs; summarize intermediate witnesses explicitly when compressing |
| `origin_to_observation` | Ordered node IDs or explicit branches explaining propagation/consumption and the observed or predicted failure |
| `mechanism_edges` | Same relation schema for the forward mechanism |
| `mechanism_summary` | Compact state-bearing explanation, not a function list |
| `evidence` | IDs, source spans/file hashes/excerpts or probe/test artifact paths/hashes, command, runtime environment, observations and scope |
| `investigation_log` | Concise evidence-gathering actions performed during this run |
| `generation_handoff` | Entry, trigger, mechanism, expected behavior, useful BRT assertion/setup, and evidence limits; policy `oracle_endpoint_and_test_conditioned` |
| `status`, `full_test_status`, `limitations` | `source_supported`, `probe_supported`, `partial`, or `endpoint_conflict`; full-test status separately; missing checks and next steps |

Use `full_test_status: not_run` if only source/probes were checked. Candidate status is separate from full-test status. For an unresolved path use a partial sequence plus explicit gaps, not fabricated edges. Expected test behavior is not a working-version observation.

Include enough source evidence for another agent to check the links. Each path edge must connect consecutive path nodes; if discovery needs several branches, represent them explicitly. The test assertion may be a node from `base_plus_test_patch_in_memory`, but mark that revision kind and separate it from buggy production-source spans.

## 5. Completion requirements

- One parseable record per input instance, no duplicates or omissions.
- Entry and origin appear as endpoints, or an explicit endpoint conflict/gap explains their absence.
- Every ordered route has corresponding edges and grounded source nodes; every evidence ID resolves.
- No unsupported jump is disguised as a direct call.
- New observations are saved, with exact scope; full-test execution is not implied by a helper probe.
- User checkouts remain unchanged.
- The handoff explains why the path helps generation: reachable trigger, failure-relevant state/control, and a useful observable distinction.

## 6. Agent invocation

> Read only DIAGNOSTIC_PATH_AGENT_GUIDE.md and pylint_known_endpoints_inputs.jsonl as task inputs. For every supplied Pylint instance, verify the test-derived entry and root-cause candidate against its own buggy source. Starting with the supplied function extracts, discover and check the intermediate dependencies; independently construct entry-to-root and origin-to-observation paths. Save the complete six-instance JSONL and any fresh evidence under the specified paths. Do not read earlier diagnoses, earlier probes, production patches, or fixed source. Mark unsupported links and endpoint conflicts explicitly.
