# Diagnostic-path agent run with supplied endpoints

This run follows the [active agent guide](DIAGNOSTIC_PATH_AGENT_GUIDE.md). It
changes the task from localizing a defect from a test to explaining the path
between a supplied entry and root-cause candidate.

Inputs are in [pylint_known_endpoints_inputs.jsonl](pylint_known_endpoints_inputs.jsonl):

- Prior root-cause candidate descriptions and locations, explicitly not gold.
- Existing oracle-selected function/option extracts, checked against each
  instance's buggy revision. Selection is privileged; fixed code is excluded.
- Test-derived entry resolution, expected behavior, and the test patch.

The new sub-agent starts without the parent conversation. It is instructed
not to read earlier paths, graphs, handoffs, or probes. It can inspect each
instance's own buggy repository and collect fresh evidence. This separates
the endpoint inputs from the intermediate path it is asked to construct;
it does not make the endpoints independent gold labels.

The output is [pylint_verified_known_endpoints_diagnostic_paths.jsonl](pylint_verified_known_endpoints_diagnostic_paths.jsonl).
The completed run contains six `probe_supported` records. All full regression
tests are `not_run`. A source-only followup closed 7080's checker-forwarding and
default-enablement gap; its final CLI behavior remains a source prediction.
Coverage, source/test spans and hashes, input provenance, evidence references,
route connectivity, and unchanged pinned checkouts passed integrity validation.
It preserves the earlier [test-only output](pylint_verified_diagnostic_paths.jsonl).
Each new record includes an entry-to-root route and an origin-to-observation
mechanism, with state/control carriers, source grounding, evidence scope,
an investigation log, and a compact generation handoff.

Fresh probe code/observations live under `known_endpoints_run/`. The available
environment lacks the historical Pylint test dependencies, so isolated probes
must not be described as complete regression-test executions. Explicit source
inference, synthetic boundaries, missing links, and endpoint conflicts remain
part of the output.

To prepare the supplied facts and inspect a completed run:

```bash
python -m scripts.prepare_pylint_known_endpoints
python -m scripts.validate_pylint_known_endpoint_paths
```

The preparer only packages endpoint inputs; it does not run an LLM or discover
paths. The current run uses a Codex sub-agent invoked from this chat. Repeating
the agent assignment requires giving a new agent the guide and input JSONL.
The validator checks coverage, source/test grounding, evidence references,
route connectivity, and clean pinned checkouts. It checks artifact integrity,
not independent causal correctness or downstream generation benefit.
