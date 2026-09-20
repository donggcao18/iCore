# Turn one SWE-bench Lite instance into a bug reproduction test problem

This walkthrough uses one real downloaded instance: **`astropy__astropy-12907`**. Read it as one worked example rather than trying to read the full CSV.

This folder contains only this guide and the inspection data:

- [test.csv](test.csv): 300 test instances.
- [dev.csv](dev.csv): 23 development instances.

Source: [princeton-nlp/SWE-bench_Lite](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite), revision `6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2`. Both exports preserve all 12 original columns. Every field was verified against the downloaded Parquet data after CSV conversion. Files use UTF-8 with a byte-order mark and preserve embedded newlines.

The objective is to produce **a new test that fails because of the reported bug and passes after the known fix**. That is a bug reproduction test, or **BRT**.

The example test below is an illustrative candidate. Its historical buggy/fixed execution has not been performed in this workspace, so the execution results in this guide are expectations, not measured results.

## 1. Why the CSV looks strange

One dataset row contains an entire issue description, a production-code patch, a reference-test patch, and lists of test names. These values contain quotes, commas, and many line breaks. CSV must quote those values, so a single record can span many lines in a text editor.

The conversion preserved those values. The CSV is useful for filtering and loading records, but a Markdown document is easier for understanding one bug.

For this example, find `astropy__astropy-12907` in `test.csv` and read its `problem_statement` cell. The explanation below summarizes that report.

## 2. What this instance gives us

| Item | Actual value or role |
|---|---|
| Instance | `astropy__astropy-12907` |
| Project | `astropy/astropy` |
| Buggy commit | `d16bfe05a744909de4b27f5875fe0d4ed41ce607` |
| Version metadata | `4.3` |
| Bug report | Nested compound models produce an incorrect separability matrix |
| Known fix | A production-code change supplied in the dataset's `patch` column |
| Reference tests | Existing benchmark test changes supplied in `test_patch` |

The dataset row does not contain the whole Astropy codebase. The commit tells us which historical Astropy source to obtain. Installing today's Astropy and running a test is not the same experiment.

For a BRT task, divide the instance into two groups:

| Give to the test generator | Keep for evaluation |
|---|---|
| Bug report: `problem_statement` | Known production fix: `patch` |
| Project identity and buggy commit | Reference-test patch: `test_patch` |
| Relevant production code from the buggy revision | Reference test labels: `FAIL_TO_PASS`, `PASS_TO_PASS` |
| Existing tests from that same buggy revision | Results of executing the candidate |

When building a generation prompt, select only the input fields above instead of sending the entire CSV row. Keep the known fix and reference tests available to the evaluator but outside the generator's retrieval context.

## 3. Understand the bug in plain language

Astropy can combine mathematical models. In this report, `&` combines model components side by side.

The function `separability_matrix()` reports which inputs affect which outputs. A `True` entry means an output depends on that input; `False` means it does not.

Consider two independent linear models:

```python
from astropy.modeling import models as m
from astropy.modeling.separable import separability_matrix

linear_pair = m.Linear1D(10) & m.Linear1D(5)
```

Their dependency matrix should be:

```text
             input 1   input 2
output 1       True     False
output 2       False    True
```

Each output depends only on its own input.

Now place this pair beside a two-input sky-projection model:

```python
nested = m.Pix2Sky_TAN() & linear_pair
actual = separability_matrix(nested)
```

The bug report says the nested form produces the following matrix. Compare it with the expected matrix:

```text
Reported buggy result                 Expected result

True  True  False False                True  True  False False
True  True  False False                True  True  False False
False False True  True                 False False True  False
False False True  True                 False False False True
```

The important difference is the **bottom-right 2 × 2 block**. The buggy result incorrectly makes both linear outputs depend on both linear inputs. Nesting the models should not introduce that dependency.

This gives us a precise test target: construct the nested model and assert the expected dependency matrix.

## 4. Turn the report into a BRT generation prompt

Here is a concrete task you could give to an LLM, together with the original bug report and retrieved buggy-version code/tests:

> Write one pytest test for the reported nested CompoundModel separability bug in Astropy. Construct two independent Linear1D models, combine their pair with Pix2Sky_TAN, and assert the correct separability matrix. The test must call the real Astropy implementation. It should fail naturally when the result contains the incorrect cross-dependencies. Do not change production code, deliberately raise an exception, or mark the test as expected to fail. Follow the existing project's test conventions.

The expected behavior comes from the report. Do not include the dataset's known fix or reference-test patch in this generation prompt.

In iCoRe, retrieval adds useful context to this prompt: relevant production functions and existing tests from `base_commit`. Those examples help the generator choose imports, fixtures, and test style.

## 5. What the output should look like

The output is a Python test, such as this illustrative candidate:

```python
import numpy as np
from astropy.modeling import models
from astropy.modeling.separable import separability_matrix


def test_nested_compound_model_keeps_linear_components_independent():
    linear_pair = models.Linear1D(10) & models.Linear1D(5)
    nested = models.Pix2Sky_TAN() & linear_pair

    expected = np.array([
        [True, True, False, False],
        [True, True, False, False],
        [False, False, True, False],
        [False, False, False, True],
    ])

    np.testing.assert_array_equal(separability_matrix(nested), expected)
```

Copy the candidate above into the target project's test suite when you are ready to evaluate it.

Each part has a purpose:

| Test code | Why it exists |
|---|---|
| Construct `linear_pair` | Create the independent components described in the issue |
| Construct `nested` | Trigger the nesting pattern associated with the bug |
| Define `expected` | Express the correct behavior described in the report |
| Call `separability_matrix` | Exercise the real project implementation |
| Assert array equality | Fail if the incorrect dependencies appear |

The assertion must check the **correct** matrix. Asserting the buggy matrix would make the test pass before the fix and potentially fail afterward, which is the opposite of our objective.

## 6. Where does this test go?

Put the candidate into the **Astropy test suite**, for example:

```text
Astropy checkout/
  astropy/
    modeling/
      separable.py
      tests/
        test_separable.py
        test_generated_reproduction.py   <-- put the candidate here
```

The candidate does not belong in iCoRe's production modules. iCoRe is the tool generating and evaluating tests; Astropy is the project being tested.

Using a standalone new file is convenient for this example. Other cases may need insertion into an existing test class or file to reuse fixtures. iCoRe's injection helpers select a destination based on retrieved reference tests and adapt the generated code to that location.

## 7. What does the dataset's diff mean?

The instance's known fix is in its `patch` cell in `test.csv`. Its changed lines are:

```diff
-        cright[-right.shape[0]:, -right.shape[1]:] = 1
+        cright[-right.shape[0]:, -right.shape[1]:] = right
```

The leading `-` means remove the old line. The leading `+` means add the new line. The spaces after the marker are Python indentation, not disposable formatting.

The old line fills the block with ones. The new line copies the existing right-hand matrix, preserving its independent components.

The complete patch also includes headers:

```diff
diff --git a/astropy/modeling/separable.py b/astropy/modeling/separable.py
--- a/astropy/modeling/separable.py
+++ b/astropy/modeling/separable.py
@@ -242,7 +242,7 @@ def _cstack(left, right):
```

These headers identify the changed file and a block of old/new source lines. Git uses surrounding unchanged lines to locate the edit. You apply the complete patch file with Git; do not paste the headers or the `+`/`-` markers into Python code.

For this BRT experiment:

- **Your candidate test** detects the bug.
- **The dataset's production fix** creates the fixed code used to evaluate that candidate.
- **The dataset's reference-test patch** contains benchmark reference changes; it is not the candidate you are trying to generate.

We explain the fix here for learning. A controlled generation experiment should keep it hidden from the generator.

## 8. Run the same test on two versions

Prepare two disposable Astropy checkouts with appropriate historical dependencies:

```text
Checkout A: base_commit + your candidate test
Checkout B: base_commit + known production fix + the same candidate test
```

Do not apply the reference-test patch to this basic generated-test comparison. It is a separate artifact that can be used in a reference evaluation.

The source revision, dependencies, and imported checkout must be correct. In particular, the two environments must not accidentally import the same installed Astropy directory.

Once the environments and checkouts are prepared, save the complete `patch` cell to a UTF-8 file named `gold_fix.patch`, preserving its line breaks. Section 10 shows how to extract that value with Python. Apply the known fix only in checkout B. These are Bash examples; set the patch path to the actual absolute path on your machine:

```bash
# Run from the root of the fixed Astropy checkout.
git apply --check /absolute/path/to/gold_fix.patch
git apply /absolute/path/to/gold_fix.patch
```

The first command checks whether the diff applies. The second changes the working-tree source. Neither command runs the tests or creates a commit.

Put the same candidate into `astropy/modeling/tests/test_generated_reproduction.py` in both checkouts. Then, from each checkout in its correctly prepared environment:

```bash
python -c "import astropy; print(astropy.__file__)"
python -m pytest astropy/modeling/tests/test_generated_reproduction.py -q
```

Check the imported path, collected test count, assertion output, and final status. Installing the historical environments is required before these commands establish anything about the bug. Both checkouts must start at the `base_commit` shown in Section 2; checkout B receives the production fix before execution.

### What results count as success?

| Buggy checkout | Fixed checkout | What it tells us |
|---|---|---|
| Fails on the matrix assertion | Passes | Successful BRT for this behavior |
| Passes | Passes | Candidate did not expose the bug |
| Fails | Fails | Inspect assertions, imports, fixtures, and environment; reproduction is not established |
| Passes | Fails | Candidate may assert buggy behavior or an unrelated requirement |
| Import/setup error or no tests collected | Any result | Invalid or incomplete test execution |

For this candidate, the expected useful failure is an array mismatch involving the incorrect cross-dependencies. A missing NumPy installation is not evidence of this bug.

## 9. How to use the reference-test columns

For this instance, `FAIL_TO_PASS` lists two reference test identifiers:

```text
astropy/modeling/tests/test_separable.py::test_separable[compound_model6-result6]
astropy/modeling/tests/test_separable.py::test_separable[compound_model9-result9]
```

Those names identify benchmark tests. They do not contain the test implementation, and some reference cases depend on the benchmark's `test_patch` being applied.

Your generated test can have a different name and implementation. It succeeds by exposing the reported behavior on the buggy version and passing on the fixed version, not by matching one of these names.

`PASS_TO_PASS` lists reference tests expected to remain successful. It helps evaluate whether a production repair preserves existing behavior. It does not tell the generator what Python test to write.

## 10. Load this instance as a BRT task in Python

Run the following from the iCoRe repository root. Loading the existing CSV requires no Hugging Face connection:

```python
import csv
from pathlib import Path

csv.field_size_limit(16 * 1024 * 1024)
with Path('data/swe-bench-lite/test.csv').open(
    encoding='utf-8-sig', newline=''
) as source:
    instance = next(
        row for row in csv.DictReader(source)
        if row['instance_id'] == 'astropy__astropy-12907'
    )

# Hand this to the generation stage, alongside buggy-revision context.
task = {key: instance[key] for key in (
    'instance_id', 'repo', 'base_commit', 'version', 'problem_statement'
)}
print(task['problem_statement'])

# Keep this in the evaluator, outside the generation prompt.
known_fix = instance['patch']

# Optional: export it when preparing the fixed checkout.
# with Path('gold_fix.patch').open('w', encoding='utf-8', newline='') as out:
#     out.write(known_fix)
```

The task/evaluation relationship is:

```text
task.problem_statement
    + retrieved buggy source
    + retrieved existing buggy tests
        -> generator
        -> candidate Python test

candidate test + buggy checkout
candidate test + fixed checkout
        -> evaluator
        -> confirmed reproduction or unsuccessful candidate
```

Downloading the CSV does not automatically redirect iCoRe's current `load_dataset(...)` calls. Use `csv.DictReader` as shown above when integrating local data. The full row remains suitable for the evaluator because it retains `patch`, `repo`, `base_commit`, and the other original fields. No additional loader script or dependency is required to inspect the CSV.

## 11. Map this example to iCoRe's stages

| Stage | What happens for this instance |
|---|---|
| Production-code retrieval | Use the report to locate relevant separability/model code at the buggy commit |
| Initial test retrieval | Find existing Astropy tests that demonstrate model construction and matrix assertions |
| Draft generation | Ask the LLM for a candidate using the issue and retrieved context |
| Iterative retrieval | Use the draft's text and call structure to find better reference tests |
| Final generation | Produce one or more test candidates |
| Injection | Place each candidate in a suitable Astropy test file/class |
| Two-version evaluation | Run the candidate before and after the known production fix |
| Ranking and export | Select a candidate and export its test changes as a patch |

The exported BRT patch should contain **your generated test changes**. It should not bundle the dataset's production fix, because the evaluator needs to run the test against both versions independently.

## 12. Repeat the process for another instance

For any new record, answer these questions in order:

1. **Where is the bug?** Identify the project and `base_commit`.
2. **What input triggers it?** Extract the minimal setup from `problem_statement`.
3. **What should happen?** Identify the expected value, behavior, or exception.
4. **What test expresses that expectation?** Call the real implementation and assert correct behavior.
5. **Where should the test live?** Follow the target project's fixtures and test conventions.
6. **Does it fail for the reported reason?** Execute against the buggy checkout and inspect the failure.
7. **Does the same test pass after the known fix?** Apply only the production fix in the fixed checkout and rerun.

That is how a SWE-bench Lite record becomes a BRT problem: the issue and buggy repository define the input; a new test is the output; the known production fix enables the fail-to-pass check.
