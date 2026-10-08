# Production-code oracle retrieval

This oracle observes the developer's production patch to select code objects.
The base variant supplies their historical buggy source. The separate patched
variant supplies developer-fixed source, including newly added objects. Both
use hindsight selection; the base JSON contains no fixed implementations.

```sh
python -m scripts.code_retrieval.extract_oracle --dataset lite --repo pylint-dev/pylint
python -m scripts.code_retrieval.extract_oracle --dataset lite --repo pytest-dev/pytest
```

For normalized SWT Verified rows, use `--dataset swt-verified` after running
`python -m scripts.export_swt_verified`.
Both Git-style and plain unified production diffs are supported, including
Verified diffs that omit their final newline. Patch source/context whitespace
is preserved when applying them to temporary historical snapshots. Git's
`--recount` handles Verified diffs whose hunk counts include trimmed trailing
context lines; no added or deleted code is changed.

Outputs are stored in `retrieval_results/code/oracle/<dataset>/<repo>/`, where
dataset is `lite` or `swt-bench-verified` and repo is the short name:

| File | Contents |
| --- | --- |
| `code_retrieval_oracle_base.json` | Affected existing objects using buggy source |
| `code_retrieval_oracle_patched.json` | Changed objects using developer-fixed source |
| `oracle_code_manifest.json` | Commit, old/new spans, change kinds, selection mappings, and parse diagnostics |

The code files use the existing generator format:
`{instance_id: {symbol_id: {obj_name, node_type, path, parent, code_start_line,
code_end_line, code_content, ...}}}`. Paths are relative to the target repository;
snippets are embedded and describe the indicated revision. Normal generator
formatting uses those snippets without reading the live checkout. Do not use
the formatter's optional `all_content=True` mode for historical oracle context.

Changed lines map to their smallest enclosing AST definition in both versions,
including insertion-only changes, decorators, and module/class variables.
Added helpers can select an existing caller in the changed files or an existing
enclosing class with lower confidence. Base objects are deduplicated; objects
without a base mapping have no invented old implementation. Deleted objects
appear only in base context. Renames retain the old path for base snippets.
Unparseable changed Python files contribute explicit whole-file context with
diagnostics; non-Python files are listed in the manifest.

Selected class methods automatically bring enclosing class context from the
same revision. Classes of at most 200 lines and 12,000 characters are included
in full. Larger classes use a `class_outline`: class decorators and inheritance,
class-body statements (including attributes and docstrings), method signatures
and decorators, constructor/factory implementations, and directly referenced
local helper implementations. Omitted method bodies are explicitly replaced
with `...` comments; `source_spans` records the original source fragments.
Inherited implementations outside the indexed patch files are not expanded.

Class context is deduplicated across selected methods. Existing full-class or
whole-file documents are reused when they already contain the enclosing class.
Nested classes also retain their enclosing class structure. Documents record
`class_context_ids`, `context_for`, and `enclosing_class_context` evidence; the
manifest records `class_context` and each change's `supporting_base` and
`supporting_patched` documents. Supporting classes are not added to the patch's
changed-object list or to test-oracle targets. Base context always uses original
buggy source; patched context uses the developer-fixed revision.

The extractor reads only patch-named files, applies the patch in a temporary
directory, and never checks out or resets the clone. It requires no LLM or target
project installation. Partial clones may download missing historical blobs.
Use `--repo-dir`, `--csv`, or `--output-dir` for custom paths. `--instance-id`
can be repeated; reruns preserve other instances. There is no code-object cap.

To store code and retrieve augmented existing tests together:

```sh
python -m scripts.test_retrieval.augment_oracle --dataset lite --repo pylint-dev/pylint
```

Code is saved before full test-graph analysis, so it remains available if test
retrieval fails. `--code-output-dir` overrides code output independently of
`--output-dir` for tests. The original test-patch oracle outputs remain available.

For BRT generation, use the base code file as `--context_code_path`, and either
`related_tests_oracle_base.json` or, after successful augmentation,
`related_tests_oracle_base_augmented.json` as `--context_test_path`. The launcher
`generate_brt_oracle_pylint.sh` now defaults to base oracle code plus the original
base test oracle. Set `CODE` or `ORACLE` to compare other retrieval variants.
Use a distinct `EXP` whenever changing context because saved prompts are reused.
See [ORACLE_PYLINT_GUIDE.md](ORACLE_PYLINT_GUIDE.md) for the full generator command.
