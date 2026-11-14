# Practical implementation plan for RQ2 and RQ3

Prepared **5 November 2026**. This engineering plan operationalizes the [experiment design](F:/GitHub/iCoRe/diagnostic_path/RQ2_RQ3_IMPLEMENTABLE_EXPERIMENT_PLAN.md). The modules, schemas, configuration, and commands below are proposed implementation contracts; they have not been implemented or executed. Existing reusable files are linked explicitly.

## 1. First deliverable and scope

Build one complete case package that contains:

1. A verified buggy/working contrast for a concrete SWE-smith test case.
2. Source and runtime observations with stable provenance.
3. A reviewed diagnostic core connecting the injecting transition to the failure.
4. An actor environment that exposes only buggy-side evidence.
5. A terminal scorer and an intermediate-progress scorer.

Complete this package for **10 technical pilot cases before building the training integration**. The first application is diagnosis/APR with a supplied failing test. A separate BRT protocol can follow.

V1 eligibility: one small production-function mutation; a deterministic, independently executable pytest case; Python-level state propagation; values representable by simple bounded projections. Prefer an origin distinct from the failure manifestation and at least one nontrivial propagation relation. Include and report cases with the origin visible in the traceback rather than selecting only cases on which a student fails. Reject unsupported mechanisms with explicit reason codes.

Keep unknown origins private from the actor. The constructor's knowledge of the injecting change is supervision, not an actor input.

## 2. Repository layout to add

Use a new package so that experimental code does not change the current retrieval/BRT workflow:

```text
scripts/diagnostic_path/
    schemas.py                 # versioned records and validation
    cli.py                     # orchestration and resumable stages
    smith_adapter.py           # pinned instances and execution profiles
    prepare.py                 # isolated buggy/working materialization
    trace_probe.py             # standalone observer inside benchmark Python
    trace_io.py                # bounded projections and observation records
    align.py                   # candidate cross-version event matches
    source_index.py            # adapter around existing DependencyIndex
    annotate.py                # teacher candidates + review export/import
    public_bundle.py           # buggy-only, label-free actor bundle
    environment.py             # deterministic tools, budgets, claims, submit
    scoring.py                 # evidence checks and diagnostic scoring
    rewards.py                 # terminal and redistributed per-turn rewards
    rollouts.py                # model/tool loop and assistant-span tracking
    training_adapter.py        # per-turn training export
    evaluate.py                # paired diagnostic and APR comparisons

diagnostic_path/configs/        # experiment settings
tests/test_diagnostic_path_*.py # meaningful contract/invariant tests
```

Generated data should live outside the source package, under a configurable artifact root:

```text
<artifact_root>/
    inventory.jsonl
    rejection_log.jsonl
    splits.json
    cases/<opaque_case_id>/
        public/                # only directory mounted into actor workers
            case.json
            sources.jsonl
            observations.jsonl
            availability.json
        private/               # constructor/scorer access only
            manifest.json
            working_observations.jsonl
            alignment.jsonl
            witnesses.json
            review.json
        executions/            # raw constructor artifacts, not actor-mounted
        rollouts/
        scores/
```

Use atomic writes and stage manifests containing input hashes, configuration hashes, versions, and completion status. Resume only when these match; a stale observation cache must not survive a source or test change silently.

## 3. Reuse existing code deliberately

| Existing component | How to use it | Boundary |
| --- | --- | --- |
| [DependencyIndex](F:/GitHub/iCoRe/scripts/test_retrieval/static_dependencies.py:66) | Build symbol IDs, source spans, possible calls/uses, tests, and fixture relationships | Conservative static edges are candidate relationships, not runtime causal facts |
| [snapshot](F:/GitHub/iCoRe/scripts/brt_harness/execution.py:23) | Export exact commit contents into isolated temporary directories where suitable | SWE-smith containers still need a profile/transport adapter |
| [PytestRunner](F:/GitHub/iCoRe/scripts/brt_harness/execution.py:98) | Reuse structured collection/setup/call classification for compatible pytest cases | Validate benchmark pytest/plugin compatibility; do not assume all repositories use pytest |
| [source helpers](F:/GitHub/iCoRe/scripts/code_retrieval/repo_graph/search_utils.py:87) | Extract function spans/snippets | The older repository scan skips many tests; use the test-aware index and explicit entry resolution |
| [existing fixed-version evaluation](F:/GitHub/iCoRe/scripts/brt_harness/execution.py:180) | Reuse the idea of frozen candidates and repeated isolated runs | It applies a SWE-bench repair patch forward; do not pass a SWE-smith injecting patch to it unchanged |

The local `ExecSpec` is SWE-bench-oriented and requires fields absent from SWE-smith's documented format. Use the official SWE-smith profile/container machinery as the execution authority. Its validation procedure compares existing tests before and after bug introduction. [SWE-smith harness documentation](https://swesmith.com/guides/harnesses/)

Run benchmark execution and later model training on the intended Linux/container host. The orchestration workstation does not need the historical project dependencies installed locally. Pin external toolkit commits and container images after the first successful case.

## 4. RQ2 implementation, in dependency order

### Milestone A: inventory, splitting, and version reconstruction

**Inputs:** pinned dataset revision, instance IDs, configured repository profiles.

Implement:

- Parse each injecting patch into files, source spans, mutation family, and candidate function mappings.
- Reject test-only changes, multi-origin changes, unsupported test runners, and unresolved source mappings for V1.
- Resolve a concrete parameterized test selector using collection output; retain its fixtures and exact execution command.
- Materialize the documented buggy revision. Recover the working version from verified provenance or reverse the injecting patch in an isolated copy. Check patch direction explicitly.
- Verify project imports originate from the materialized version, not a stale installed checkout.
- Run the selected test three times per version. Require stable intended failure on the buggy side and stable passing on the working side.

SWE-smith documents that `base_commit` already includes the bug and that `patch` introduces it. Store resolved revisions and source hashes rather than treating field names as a general clean/buggy convention. [SWE-smith, Appendix A.1](https://arxiv.org/html/2504.21798v2)

Private manifest contract:

```json
{
  "schema_version": "1",
  "case_id": "case_0001",
  "dataset_revision": "<pinned revision>",
  "repository": "<repository>",
  "buggy_revision": "<resolved revision>",
  "working_tree_sha256": "<hash>",
  "injecting_patch_sha256": "<hash>",
  "environment_digest": "<digest>",
  "test_selector": "tests/test_core.py::test_behavior[param]",
  "failure": {"kind": "exception", "type": "ZeroDivisionError"},
  "candidate_origin_symbols": ["core.py::normalize_count"],
  "mutation_family_id": "<family identifier>",
  "status": "contrast_verified"
}
```

Assign repository holdouts and mutation-family groups before generating demonstrations. All tests, trajectories, and variants of one mutation stay in the same split. Recheck splits after duplicate detection. The 10-case engineering set is not a held-out performance claim.

**Acceptance:** one manifest plus reproducible execution artifacts per accepted case; every failure has a machine-readable reason. Initial statuses include `contrast_verified`, `flaky`, `setup_error`, `unsupported_runner`, `multi_origin`, and `mapping_unresolved`.

### Milestone B: bounded observation recording

Implement a standalone, standard-library observer that runs in the benchmark interpreter. Begin with calls, normal returns, exceptions, and selected local snapshots. A tracing return event can also accompany exception unwinding, and line events occur before execution of that line; preserve these distinctions. [Python tracing API](https://docs.python.org/3.11/library/sys.html#sys.settrace)

Observation contract:

```json
{
  "observation_id": "obs_17",
  "run_id": "buggy_repeat_1",
  "symbol_id": "core.py::normalize_count",
  "invocation_id": "call_8",
  "parent_invocation_id": "call_5",
  "event": "normal_return",
  "source_line": 12,
  "properties": {"argument.n": 0, "return": 0},
  "source_sha256": "<buggy source hash>",
  "projection_complete": true
}
```

V1 projections: exact built-in scalars and small bounded containers of supported values. Represent unsupported objects explicitly; do not run arbitrary `repr`, getters, or expressions to serialize them. Record truncation and observation limits. Scope capture by repository source paths using a rule independent of the origin label.

Keep import/test setup events distinguishable from the test body. If the relevant origin effect happened during setup, retain the setup evidence. Initially reject multithreaded or unsupported subprocess propagation rather than silently treating an incomplete trace as complete.

**Acceptance:** uninstrumented and instrumented outcomes agree; observation IDs resolve to real records; exception unwinding is not mislabeled as a normal `None` return; capture limits produce explicit incomplete status.

### Milestone C: alignment and witness review

Build candidate alignments using symbol identity, call context, input projections, and local source mappings. Tag ambiguity. Do not automatically align by absolute trace position after a branch or loop changes.

The teacher proposes a small core from the paired records and source. A reviewer checks introduction, propagation, consumption, carrier identity, and connection to the actual failure. Reference values help annotation but remain private; actor claims need not reproduce entire private reference snapshots.

Private witness contract:

```json
{
  "case_id": "case_0001",
  "verified_origins": ["core.py::normalize_count"],
  "accepted_cores": [{
    "core_id": "private_core_a",
    "facts": [{
      "fact_id": "private_f1",
      "symbol_id": "core.py::normalize_count",
      "role": "introduce",
      "property": "return",
      "predicate": {"op": "eq", "value": 0},
      "reference_predicate": {"op": "eq", "value": 1},
      "support_policy": "runtime_and_source",
      "buggy_evidence": ["obs_17"],
      "review_status": "accepted"
    }],
    "relations": [],
    "completeness": "partial"
  }],
  "review_status": "needs_propagation_review"
}
```

This example intentionally contains only an origin fact and is **not** an accepted complete diagnostic core. A primary case needs reviewed connecting relations and an observation fact. Each relation records source/destination facts, carrier mapping, supporting spans/observations, and evidence strength. The reviewer explicitly marks `accepted_for_primary` only when the declared core is connected and supported.

Start with approximately 2–5 facts/relations per case as a presentation target, not a correctness rule. Allow alternative cores. Unknown alternatives go to adjudication, not automatic negative labeling. Audit a held-out subset with a second reviewer and report disagreement before resolution.

**Acceptance:** schema-valid complete core; every reference resolves; no gap disguised as an edge; annotation status and support strength are explicit. Measure both precision under review and construction yield across all screened cases.

## 5. Actor environment contract

### Milestone D: build the public bundle and tools

For V1, initialize the actor with the selected test source, actual failure report, and repository access. Omit synthetic issue text initially; this defines a controlled test-conditioned task and avoids relying on generated-report quality. Add issue-report inputs later under a separately audited setting.

Do not export working observations, injecting diffs, private core/fact IDs, review statuses, or gold origin fields. Availability of source/probe queries must not reveal which symbols are annotated.

Implement:

```text
reset(case_id) -> entry context, failure observation, budgets
inspect(symbol_id) -> bounded source, source evidence ID, ordinary neighbors
probe(symbol_id, event, property) -> bounded buggy-side observations
record_claim(claim_or_relation) -> acknowledgement and public schema errors only
submit(origin, claims, relations) -> terminal acknowledgement
```

Use a common symbol convention such as `relative/file.py::Class.method`, matching the existing index where possible. Runtime invocation IDs are separate from source symbol IDs. A symbol may have many executions.

The actor creates its own claim IDs. `record_claim` must not return hidden correctness feedback. Private scoring happens after or outside actor interaction. Evidence references must identify source/observations actually issued in that episode; a guessed valid global cache ID does not count as acquired evidence.

Initial configurable budgets:

| Limit | Pilot default |
| --- | ---: |
| Actions per episode, including claim updates | 16 |
| Probe actions | 6 |
| Submitted claims | 6 |
| Source lines per inspection | 100 |
| Total observation tokens | 12,000 |
| Container timeout per test run | 120 seconds |

These are starting settings, not validated optima. Use the same limits across methods. Repeated cached queries still incur the declared action/context cost. Reset claims, evidence access, and budgets fully between rollouts.

**Acceptance:** an identical action history reproduces the same outputs; clean/private records are unreachable through every public tool; unsupported queries fail consistently; no label-dependent subset of properties is exposed.

## 6. Scoring contract

### Milestone E: validate terminal and partial scoring before training

Use two scoring levels:

1. Automatic scoring for reviewed predicate/relation templates.
2. Independent review for held-out diagnosis quality and novel alternatives.

Match source identities, roles, properties, predicates, relations, and evidence support. Do not score by function-name overlap alone. Permit predefined aliases and accepted alternative cores. Do not give the actor the hidden matching keys.

A practical pilot score is:

```text
O = origin correctness, in [0, 1]
D = supported connected-core coverage, in [0, 1]
C = fraction of submitted claims confirmed contradicted
R0 = 0.5 * O + 0.5 * D - 0.25 * C
```

Define `D` per accepted alternative, then take the best supported alternative; never require their union. For a concrete first implementation, compute fact and relation coverage separately and use their minimum, with an additional full-core success flag. A complete diagnostic success requires the correct origin and a fully supported connecting core, not just a high average score.

Unknown/unmatched claims earn no positive credit and are recorded as unresolved; they are not automatically counted in `C`. The claim cap prevents unlimited additions. Independently review unresolved outputs when reporting final precision. Structural errors such as nonexistent evidence IDs are distinguishable from genuinely novel explanations.

Compute a bounded progress potential `Phi_t` from supported facts/relations acquired and recorded by step t. Keep a persistent evidence-access ledger. Repetition adds no progress; retraction or invalidation can reduce current support. No free-form LLM “reasoning quality” score is needed for the first implementation.

Scorer validation cases must include correct origin/wrong mechanism, wrong origin/plausible mechanism, reversed propagation, stale invocation references, fabricated evidence, duplicate facts, valid alternate routes, and symptom-blocking fixes. Report false positives, false negatives, and unresolved rate before trusting the score as a training signal.

**Acceptance:** replayed hand-authored episodes receive the expected component scores and milestone timing; independent review finds the intended distinctions. Any acceptance threshold is a declared project decision, not proof of universal correctness.

## 7. RQ3 training implementation

### Milestone F: collect demonstrations and establish terminal baselines

Use the public environment to collect teacher episodes. Keep teacher access to private construction data out of this interaction stage. If a teacher previously saw private annotations, regenerate/replay the demonstration and verify that every observation and specific claim is supported by actor-permitted evidence.

Store each assistant action, tool observation, claim state, termination reason, and issued evidence IDs. Save exact token spans after applying the model chat template. Text offsets alone are insufficient when tokenization merges boundaries.

The rollout record should include:

```text
case_id, policy_version, environment_version, seed
turns[{action, observation_ids, assistant_token_span, claim_state_hash}]
termination_reason, final_submission
private_scores[{turn, potential, confirmed_errors}]
```

Create one common SFT initialization and retain it as a baseline. All RL conditions start from this checkpoint and submit the same structured diagnosis format.

For a practical first backend, adapt A²Agent's released rollout/progress/advantage/training separation. It already consumes per-turn information and trains using advantage-weighted likelihood. Keep that update family fixed across the RQ3 conditions and call it advantage-weighted RL; do not report it as a GRPO reproduction. Its existing location-mention labeler must be replaced by the witness scorer. [A²Agent training implementation](https://github.com/donian00/A2Agent)

Begin with one common sampling setup, eight rollouts per training case as a pilot choice, and a bounded number of online collect/update iterations. Recollect from the current policy; log policy version to expose off-policy drift. Tune the update size on development data. Check origin and diagnostic metrics after each short iteration instead of selecting by training reward.

### Milestone G: add the process comparison

Run four conditions:

| Run | Training signal |
| --- | --- |
| S0 | Common SFT only |
| R1 | Terminal origin score |
| R2 | Terminal `R0` including diagnosis |
| R3 | Same `R0` total, redistributed using verified progress |

For R3, use unit discount in bounded episodes, initial potential zero, and terminal shaping potential zero:

```text
nonterminal: r_t = lambda * (Phi_t - Phi_previous)
terminal:    r_T = R0 - lambda * Phi_previous
```

Start with `lambda = 0.25` as a development setting, then examine sensitivity if the pilot is promising. Verify `sum(rewards) == R0` for every termination mode, within numeric tolerance. Keep costs/penalties identical across R2 and R3.

Compute per-turn returns-to-go in both R2 and R3 and map them to assistant action spans. Use the same documented advantage estimation, clipping/weight bounds, normalization, token reduction, and batching. Tool observations receive no actor loss. Avoid claiming that a short action-history group is an exact equivalent state.

Ordinary scalar-per-completion GRPO cannot distinguish these runs after summing the redistributed reward. Its documented custom reward interface returns completion-level rewards; action-specific training needs additional integration. [TRL GRPO documentation](https://huggingface.co/docs/trl/grpo_trainer)

**Interpretation limit:** reward redistribution can act like a state-dependent baseline. With an exact value baseline, potential shaping can cancel in the advantage. In a practical advantage-weighted or clipped optimizer, learning dynamics can change, but equal reward totals do not guarantee identical updates or improved learning. R3 is a credit/optimization experiment, not a new causal correctness oracle or a new shaping theorem.

If R3 does not improve, prefer R2. If testing additive milestone bonuses afterward, compare them against a terminal-reweighting control so gains cannot be attributed only to a larger diagnostic reward.

## 8. Experiment data and evaluation

Scale in this order:

1. **10 technical cases:** prove version handling, observation recording, annotation, and replay.
2. **30–50 reviewed calibration cases:** validate witness/scorer behavior and annotation cost; do not report them as a large training result.
3. **A training pilot:** an initial planning target is hundreds of additional accepted cases, with disjoint development/evaluation groups. Measure construction yield before promising a fixed total.
4. **A learning curve:** increase accepted training cases and repeat decisive comparisons over three seeds if the pilot shows usable headroom.

Prefer repository-level holdouts and always group related mutations. If too few repositories remain, explicitly report within-repository/module generalization instead of implying unseen-repository performance. Audit reference test families and synthetic report provenance.

Primary metrics: origin accuracy/F1; independently reviewed fact and relation precision/recall; full-core and joint success; unresolved/contradicted claim rates; tool/context cost. Plot learning curves at matched actor updates and also report total annotation, verification, and rollout cost.

Before scaling RL, perform an RQ1 gate on the reviewed cases with a fixed repair backend: same target code, no diagnosis versus function route versus reviewed state-bearing core. Label private-reference diagnoses as oracle guidance. Keep predicted diagnoses separate. A small or uncertain pilot effect is a reason to gather evidence, not an automatic claim that the direction succeeds or fails.

For application evaluation, feed the **learned agent's** diagnosis into the same repair backend under identical budgets. Also inspect paired cases with the same predicted origin, while retaining the full-population result. Apply generated repair patches to the buggy version and validate with the intended test suite; passing one supplied test alone is not a complete repair verdict.

## 9. Essential checks to implement

These checks target mistakes that could invalidate the experiment:

- Version reconstruction: reverse the injecting change in an isolated snapshot; original files remain unchanged; imported source belongs to the chosen version.
- Failure classification: collection/setup errors, skips, and timeouts are not accepted as the intended regression.
- Trace semantics: normal `None` returns differ from exception unwinding; capture limits and ambiguous alignment are visible.
- Evidence provenance: inaccessible or unissued IDs cannot satisfy a milestone; clean-side records cannot be queried.
- Annotation: changing a carrier or relation direction can change correctness even when function names match.
- Credit: repeated facts receive no extra reward; every stop path settles the shaping potential; total reward equality holds.
- Training: token masks exclude observations; a short controlled update changes likelihood in the intended direction; a zero-step run reproduces the starting checkpoint evaluation.
- Split integrity: sibling mutations and demonstrations cannot cross train/evaluation boundaries.

Complete these before a full training run. A static file check or a declining scalar loss is not sufficient validation.

## 10. Build schedule and decision gates

Indicative schedule for one implementation owner with reviewer availability; label yield and environment repair can extend it.

| Stage | Approximate effort | Completion gate |
| --- | --- | --- |
| Contracts, inventory, version adapter | 2–3 working days | One pinned contrast reproduces correctly |
| Tracer, source index, 10-case pilot | 4–6 days | Supported observations and explicit rejection records |
| Witness review and scorer calibration | 5–10 days | Audited cores and inspected scorer error modes |
| Public cache, tools, replay checks | 3–5 days | Actor observations reproduce without private data |
| Teacher rollouts and common SFT | 3–5 days | Usable held-out tool interactions and scored diagnoses |
| R1/R2 training, then R3 integration | 5–10 days | Stable short runs and validated action-level credit |
| Independent diagnosis/APR comparisons | 4–7 days | Paired results, uncertainty, cost, and failure analysis |

The first week should produce executable contrast/trace artifacts on real cases. It should not depend on MCTS, a learned PRM, or a general-purpose dynamic slicer. The first major decision is whether witness quality and yield justify scaling; the next is whether terminal diagnostic training already provides the desired capability.

## 11. Planned CLI contract

The following commands specify the interface to implement. **They will not run until the proposed package exists.**

```bash
python -m scripts.diagnostic_path.cli inventory --config diagnostic_path/configs/pilot.json
python -m scripts.diagnostic_path.cli prepare --case-id case_0001 --config diagnostic_path/configs/pilot.json
python -m scripts.diagnostic_path.cli trace --case-id case_0001 --version both --config diagnostic_path/configs/pilot.json
python -m scripts.diagnostic_path.cli annotate --case-id case_0001 --config diagnostic_path/configs/pilot.json
python -m scripts.diagnostic_path.cli bundle --case-id case_0001 --config diagnostic_path/configs/pilot.json
python -m scripts.diagnostic_path.cli replay --case-id case_0001 --episode reviewed_episode.json
python -m scripts.diagnostic_path.cli rollout --policy <checkpoint> --split train --config diagnostic_path/configs/pilot.json
python -m scripts.diagnostic_path.cli export-training --condition terminal_diagnosis --config diagnostic_path/configs/pilot.json
python -m scripts.diagnostic_path.cli export-training --condition redistributed_diagnosis --config diagnostic_path/configs/pilot.json
python -m scripts.diagnostic_path.cli evaluate --policy <checkpoint> --split test --config diagnostic_path/configs/pilot.json
```

Required configuration keys: dataset/toolkit revisions; artifact root; container/profile settings; case filters; split assignment; repeat count/timeouts; observation projections and limits; tool budgets; teacher/student checkpoints; scorer version; terminal weights; shaping coefficient; advantage/update settings; seeds; evaluation backend.

**Immediate implementation order:** `schemas → smith_adapter/prepare → trace_probe → source_index/align → annotation/review → public environment → scoring → rollouts → training adapter → evaluation`. The first reviewable software milestone is a complete case package and replayed diagnosis for a real SWE-smith instance.
