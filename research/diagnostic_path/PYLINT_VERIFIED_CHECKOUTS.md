# Pylint Verified checkouts for diagnostic agents

Run from the iCoRe root in a shell with Python 3.9+ and Git available:

```bash
python -m scripts.clone_pylint_verified --dry-run
python -m scripts.clone_pylint_verified
```

The default input is the existing normalized
`data/swt-bench-verified/oracle_input_pylint_pytest.csv`. Its six Pylint rows
match the requested folders. This is the workspace's prepared **SWT-bench
Verified** input, with production fixes restored from SWE-bench Verified as
documented in [the oracle guide](../ORACLE_CODE_GUIDE.md). To use a different
normalized SWE-bench Verified CSV, pass `--csv /path/to/file.csv`; it must have
`repo`, `instance_id`, and buggy `base_commit` fields. Raw SWT patch fields use
a different convention; this cloning script does not interpret patch fields.

The command creates:

```text
repos/
  pylint/                       # existing checkout: untouched
  pylint-dev__pylint-4970/
  pylint-dev__pylint-6386/
  pylint-dev__pylint-6903/
  pylint-dev__pylint-7080/
  pylint-dev__pylint-7277/
  pylint-dev__pylint-8898/
```

Each new folder is an independent Git clone on branch `diagnostic-path` at
that instance's exact buggy base commit. The script does not create or require
the top-level `pylint/` clone. It applies neither the production fix nor the
reference-test patch, and does not install historical dependencies. Network
access is required for GitHub and for fetching missing blobs from partial clones.

Select one case or another destination:

```bash
python -m scripts.clone_pylint_verified --instance-id pylint-dev__pylint-4970
python -m scripts.clone_pylint_verified --output-dir /path/to/agent-repos
```

`--instance-id` can be repeated. Rerunning preserves existing local edits if
the checkout's HEAD and origin still match. A mismatching or incomplete folder
causes an error; the script never resets or deletes it. A checkout previously
modified by an agent is not guaranteed to be pristine merely because its HEAD
matches: inspect `git status` before reproducing the reference contrast.

Case metadata is recorded in `.git/icore-instance.json` within each new clone.
Give the agent its instance ID, checkout path, allowed metadata and normalized
`test_patch` only, and the [diagnostic agent guide](DIAGNOSTIC_PATH_AGENT_GUIDE.md).
The active investigation excludes production patches, issue text, and fixed
versions. Use a second buggy copy only for isolated test/probe execution.
Cloning prepares source only; it does not verify the bug or prepare its runtime
environment. Combined pilot output is [the six-instance JSONL](pylint_verified_diagnostic_paths.jsonl),
with scope and reproducibility notes in [the evidence README](pylint_verified/README.md).
