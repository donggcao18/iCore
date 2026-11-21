# Pylint diagnostic path construction

The runner reads the six Pylint cases from the [normalized input CSV](../../data/swt-bench-verified/oracle_input_pylint_pytest.csv).
It takes entry selectors from `FAIL_TO_PASS` and uses the supplied root-cause
candidates from the [earlier endpoint input](../diagnostic_path/pylint_known_endpoints_inputs.jsonl).
The [agent guide](DIAGNOSTIC_PATH.md) and the current case's endpoints go into
the prompt. Codex searches the source and returns one path per entry test.

Run from the iCoRe root with Git, Python 3.9+, and a signed-in Codex CLI on
`PATH`. The runner uses `repos/<instance_id>` when present and falls back to
`repos/pylint`. To fetch the per-instance clones if needed, run
`python -m scripts.clone_pylint_verified`.

```sh
# Check all six base trees, patches, and entry functions without calling Codex.
python research/E2R/run_pylint.py --prepare-only

# Construct one diagnostic path.
python research/E2R/run_pylint.py --instance-id pylint-dev__pylint-4970

# Construct paths for all six cases.
python research/E2R/run_pylint.py
```

Use `--endpoints` to supply a different root-cause file in the same JSONL
format. Each record needs `instance_id`, `base_commit`, and
`given_root_cause` with `description`, `provenance`, and `locations`.

The runner exports each exact base tree from the local clone into a temporary
directory under `repos/`, applies `test_patch` with `git apply`, and checks
that each entry function exists. The export has a fresh Git directory for patch
application and contains only the source tree for that revision. Each export
is removed when the case finishes. Historical test dependencies are optional
for tracing and are not installed by the runner. On Windows, Git materializes
tracked symlinks as files containing their targets.

Results go to `research/E2R/runs/<run>/<instance_id>/test-001/`. Each directory
contains `input.json` with the endpoints, base revision, and test patch hash.
A Codex run also saves `path.json`, `codex-events.jsonl`, and
`codex-stderr.log`. A failure is reported in the instance's `error.log`.
The path follows the [output schema](path.schema.json) and records the
evidence for each step. A connected path may be based on source inspection.

## Prepare pytest cases

Use the local clone at `repos/pytest` and the same normalized CSV. The
preparer exports the tracked Python source at each base revision, applies its
test patch, and resolves every `FAIL_TO_PASS` selector. It selects base-side
root-cause candidates using the reference production patch. The diagnostic
source stays at the buggy revision with the test patch applied.

```sh
python research/E2R/prepare_pytest.py
```

Use `--instance-id pytest-dev__pytest-7324` for a single instance. The runner
prints the directory containing `inputs.jsonl`, per-test `input.json` files,
and source excerpts. Follow the same [agent guide](DIAGNOSTIC_PATH.md) to save
`path.json` beside each input. This source preparation does not require the
historical pytest runtime or the non-Python files from the partial clone.
