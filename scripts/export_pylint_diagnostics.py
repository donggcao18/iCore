"""Export the six manually constructed, test-patch-only Pylint diagnoses.

This packages curated source analyses and reproducible probe evidence. It is
not a general automatic diagnosis algorithm. Run probe_pylint_diagnostics first.
Only allowed metadata/test_patch fields are used from the normalized CSV.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
from pathlib import Path

from scripts.probe_pylint_diagnostics import CSV, OUT, ROOT


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source(key, path, symbol, start, end):
    return dict(id=key, path=path, symbol=symbol, start_line=start, end_line=end)


def node(key, role, evidence, condition, proposition):
    return dict(id=key, role=role, source_ref=evidence, condition=condition,
                proposition=proposition, evidence_refs=[evidence])


def edge(a, b, carrier, observed=False):
    return dict(source=a, target=b, carrier=carrier,
                support="observed_in_isolated_probe" if observed else "source_supported")


CASES = {
    "4970": {
        "tests": [("tests/checkers/unittest_similar.py", "test_set_duplicate_lines_to_zero",
                   "regression", "SystemExit.code == 0 and captured stdout == ''")],
        "entry": "cli", "entry_resolution": "The test imports similar from pylint.checkers and invokes similar.Run directly; SIMILAR1/SIMILAR2 resolve to tests/input/similar1 and similar2.",
        "entry_runtime_scope": "observed_in_isolated_probe",
        "sources": [
            source("cli", "pylint/checkers/similar.py", "Run", 887, 929),
            source("init", "pylint/checkers/similar.py", "Similar.__init__", 356, 369),
            source("run", "pylint/checkers/similar.py", "Similar.run", 391, 393),
            source("iter", "pylint/checkers/similar.py", "Similar._iter_sims", 530, 536),
            source("find", "pylint/checkers/similar.py", "Similar._find_common", 474, 479),
            source("hash", "pylint/checkers/similar.py", "hash_lineset", 239, 264),
            source("display", "pylint/checkers/similar.py", "Similar._display_sims", 434, 439),
            source("report", "pylint/checkers/similar.py", "Similar._get_similarity_report", 441, 459),
            source("aliases", "tests/checkers/unittest_similar.py", "similar import and fixture paths", 23, 35),
        ],
        "trigger": {"argv": ["--duplicates=0", "tests/input/similar1", "tests/input/similar2"], "condition": "Zero is required by the supplied test to disable standalone duplicate reporting, rather than just select an empty matching window."},
        "expected": "Successful exit with completely empty stdout, not a zero-duplicates summary.",
        "actual": "The extracted standalone pipeline exits 0 but writes 'TOTAL lines=62 duplicates=0 percent=0.00\\n\\n'.",
        "roots": ["cli", "run"],
        "origin": "Missing zero-means-disabled decision before running and displaying the standalone Similar pipeline; min_lines=0 is passed through as an ordinary numeric threshold.",
        "origin_uncertainty": "The test/source establish the missing disable behavior, not a unique implementation location. Run and Similar.run are candidate guard boundaries; the unconditional report is the visible output site.",
        "nodes": [
            node("n1", "origin_candidate", "cli", "--duplicates=0 with two filenames", "Run parses integer zero and continues through Similar construction, stream loading, and sim.run instead of disabling the operation."),
            node("n2", "propagation", "init", "Similar constructed with min_lines=0", "The zero threshold is stored unchanged in self.min_lines."),
            node("n3", "propagation", "hash", "_find_common passes the zero threshold to hash_lineset", "range(0) creates no shifted iterators; zip with no inputs contributes no matching chunks for these fixtures."),
            node("n4", "consumption", "run", "Similarity list is empty", "Similar.run still invokes _display_sims on the computed empty list."),
            node("n5", "observation", "report", "There are 62 fixture lines even though no duplicates are found", "The report always appends a TOTAL summary and _display_sims prints it, contradicting the empty-output assertion."),
        ],
        "edges": [edge("n1", "n2", "constructor min_lines argument", True), edge("n2", "n3", "self.min_lines passed via _iter_sims/_find_common", True), edge("n3", "n4", "empty similarity result returned from computation", True), edge("n4", "n5", "unconditional display/report on an empty result", True)],
        "navigation": ["cli", "init", "run", "iter", "find", "hash", "display", "report"],
        "summary": "Zero is parsed as a matching threshold rather than a disable command. The zero-sized hash window yields no matches, but the standalone execution still produces the unconditional TOTAL report, so exit status succeeds while the required empty stdout does not.",
        "brt": "Invoke the standalone similar.Run with --duplicates=0 and two nonempty fixture files; capture SystemExit and stdout separately. Assert exit 0 and exactly empty output. A check for 'duplicates=0' alone misses this bug.",
        "limits": ["No full pytest module execution or fixed-version contrast. The extracted pipeline excludes the astroid/Pylint integration not exercised when ignore flags are false.", "A downstream suppression could also rescue output; it would not prove a unique origin site."],
    },
    "6386": {
        "tests": [("tests/config/test_config.py", "test_short_verbose", "regression", "Captured stderr contains 'Using config file'")],
        "entry": "entry", "entry_resolution": "tests/config/test_config.py imports Run from pylint.lint, re-exported from pylint.lint.run.Run; construction enters Run.__init__ with EMPTY_MODULE and -v, exit=False.",
        "entry_runtime_scope": "source_verified_only",
        "sources": [
            source("entry", "pylint/lint/run.py", "Run.__init__", 92, 137),
            source("pre", "pylint/config/utils.py", "_preprocess_options", 214, 249),
            source("table", "pylint/config/utils.py", "PREPROCESSABLE_OPTIONS and _set_verbose_mode", 187, 211),
            source("config", "pylint/config/config_initialization.py", "_config_initialization", 20, 41),
            source("parser", "pylint/config/config_file_parser.py", "_ConfigurationFileParser.parse_config_file", 79, 103),
            source("parser_init", "pylint/config/config_file_parser.py", "_ConfigurationFileParser.__init__", 26, 31),
            source("short", "pylint/lint/base_options.py", "verbose option registration", 538, 547),
            source("noop", "pylint/config/callback_actions.py", "_DoNothingAction", 41, 55),
            source("aliases", "tests/config/test_config.py", "Run import and EMPTY_MODULE", 13, 19),
        ],
        "trigger": {"argv": ["tests/regrtest_data/empty.py", "-v"], "exit": False, "setup": "Run from an environment with a discoverable valid configuration file, as required by this test's expected 'Using config file' message.", "control": "--verbose is handled by the preprocessor and enables the early message."},
        "expected": "The short -v flag enables the same early verbose configuration announcement as --verbose.",
        "actual": "Extracted preprocessing leaves -v in the remaining args and run.verbose=False, so the connected configuration parser emits no announcement; --verbose sets it True and emits 'Using config file ...'.",
        "roots": ["pre", "table"],
        "origin": "Early option preprocessing excludes all arguments not starting with '--', while only --verbose is mapped to the callback that sets Run.verbose. The registered -v alias therefore misses the pre-configuration side effect.",
        "origin_uncertainty": "This diagnoses the missing short-alias preprocessing behavior; no developer repair location is inferred. Full Run under the historical test configuration was not executed.",
        "nodes": [
            node("n1", "prerequisite", "entry", "Run invoked with -v", "Run initializes its own verbose field to False before preprocessing."),
            node("n2", "origin_candidate", "pre", "The argument is '-v', not a '--' option", "The preprocessor appends -v to processed_args and continues without invoking _set_verbose_mode."),
            node("n3", "propagation", "entry", "No verbose callback ran", "Run passes self.verbose=False into _config_initialization before ordinary command-line parsing."),
            node("n4", "propagation", "config", "verbose_mode=False", "_config_initialization constructs _ConfigurationFileParser with the false mode."),
            node("n5", "observation", "parser", "A valid config file is found", "The parser's 'Using config file' print is guarded by self.verbose_mode and is skipped."),
        ],
        "edges": [edge("n1", "n2", "args and initial Run.verbose"), edge("n2", "n3", "unchanged Run.verbose field"), edge("n3", "n4", "verbose_mode parameter"), edge("n4", "n5", "parser.verbose_mode guard; extracted parser observation corroborates the guard")],
        "navigation": ["entry", "pre", "table", "config", "parser_init", "parser", "short", "noop"],
        "summary": "-v is registered as a normal CLI alias but bypasses the separate early preprocessor. Run.verbose stays False when configuration is read, so the parser suppresses the announcement. Later handling of the no-op verbose option cannot retroactively print it.",
        "brt": "Use the public Run constructor with a valid config context and -v, capture stderr, and require the early config announcement. Compare --verbose as a control; exit=False avoids interpreting normal Run termination as the failure.",
        "limits": ["The probe connects extracted preprocessing and parsing with the source-defined field; it does not execute Run, plugin loading, or the patched test.", "With no configuration file, verbose mode prints a different message; preserve the test's config-discovery prerequisite."],
    },
    "6903": {
        "tests": [("tests/test_pylint_runners.py", "test_pylint_run_jobs_equal_zero_dont_crash_with_cpu_fraction", "regression", "Run completes through SystemExit.code == 0 instead of an uncaught pool-construction ValueError")],
        "entry": "entry", "entry_resolution": "The added test imports pylint.lint.Run and invokes it with --jobs=0 and GenericTestReporter; builtins.open and pylint.lint.run.Path are patched to expose a small cgroup CPU share.",
        "entry_runtime_scope": "source_verified_only",
        "sources": [
            source("entry", "pylint/lint/run.py", "Run.__init__", 171, 198),
            source("query", "pylint/lint/run.py", "_query_cpu", 34, 61),
            source("count", "pylint/lint/run.py", "_cpu_count", 64, 80),
            source("check", "pylint/lint/pylinter.py", "PyLinter.check", 648, 659),
            source("pool", "pylint/lint/parallel.py", "check_parallel", 125, 142),
        ],
        "trigger": {"argv": ["tests/test_pylint_runners.py", "--jobs=0"], "cgroup": {"cpu.cfs_quota_us": "-1 if read", "cpu.shares": "2", "cpu.shares_is_file": True}, "condition": "Multiprocessing available and a positive host CPU count. Fractional shares enter integer conversion."},
        "expected": "Automatic job selection must allow the lint run to complete; the supplied test expects successful SystemExit, not a pool exception.",
        "actual": "The extracted helpers return CPU share 0 and jobs 0. Real multiprocessing.Pool(0) raises ValueError('Number of processes must be at least 1'). The full CLI failure is source-supported, not executed.",
        "roots": ["query", "count"],
        "origin": "int(cpu_shares / 1024) truncates a positive fractional allocation to zero, and _cpu_count returns that zero as a worker count without a positive lower bound.",
        "origin_uncertainty": "Both the cgroup-to-count conversion and the worker-count contract are relevant boundaries. The quota/period branch can also round down, but the test here targets shares=2; no new claim about all cgroup variants is made.",
        "nodes": [
            node("n1", "origin_candidate", "query", "Shares file exists with cpu_shares=2; quota branch does not apply", "int(2 / 1024) returns 0, converting a positive allocation into an unusable worker count."),
            node("n2", "propagation", "count", "Host CPU count is positive and cpu_share is not None", "min(cpu_share, cpu_count) preserves zero; None-only fallback does not reject it."),
            node("n3", "propagation", "entry", "--jobs=0 requests automatic sizing", "Run overwrites linter.config.jobs with the returned zero."),
            node("n4", "consumption", "check", "jobs is 0, so jobs == 1 is false", "PyLinter.check selects check_parallel and forwards jobs=0."),
            node("n5", "observation", "pool", "check_parallel receives zero jobs", "multiprocessing.Pool is constructed with 0 and raises ValueError before the expected successful exit."),
        ],
        "edges": [edge("n1", "n2", "_query_cpu return value", True), edge("n2", "n3", "_cpu_count return assigned to config.jobs"), edge("n3", "n4", "config.jobs branching and argument"), edge("n4", "n5", "jobs passed as Pool process count; Pool(0) separately observed")],
        "navigation": ["entry", "count", "query", "check", "pool"],
        "summary": "The test's fractional CPU shares round down to zero; automatic CPU sizing preserves zero, stores it as jobs, and selects the parallel path. Pool construction requires at least one process, so the run fails before its normal successful exit.",
        "brt": "Exercise Run with --jobs=0 under mocked cgroup shares=2, preserve a valid lint target/reporter, and assert normal successful exit. A helper-only return-value assertion is narrower than the regression witness.",
        "limits": ["The actual test's patched Path leaves quota existence platform-dependent; the probe deterministically uses the same shares branch with quota absent.", "No full Pylint Run or parallel linting occurred; the terminal pool error was checked separately with the real standard library."],
    },
    "7080": {
        "tests": [("tests/test_self.py", "TestRunTC.test_ignore_path_recursive_current_dir", "regression", "self._runtest(..., code=0) succeeds while recursively checking '.' with anchored --ignore-paths")],
        "entry": "entry", "entry_resolution": "The test calls TestRunTC._runtest -> _run_pylint -> the imported testutils._Run wrapper -> pylint.lint.Run.__init__; PyLinter.check performs recursive discovery.",
        "entry_runtime_scope": "source_verified_only",
        "sources": [
            source("wrapper", "tests/test_self.py", "TestRunTC._runtest and _run_pylint", 125, 156),
            source("alias", "tests/test_self.py", "testutils._Run import alias", 32, 40),
            source("test_run", "pylint/testutils/_run.py", "_Run.__init__", 28, 45),
            source("entry", "pylint/lint/run.py", "Run.__init__", 197, 208),
            source("discover", "pylint/lint/pylinter.py", "PyLinter._discover_files", 585, 619),
            source("check", "pylint/lint/pylinter.py", "PyLinter.check", 621, 635),
            source("iterate", "pylint/lint/pylinter.py", "PyLinter._iterate_file_descrs and _expand_files", 759, 779),
            source("ignore", "pylint/lint/expand_modules.py", "_is_in_ignore_list_re and _is_ignored_file", 44, 60),
            source("expand", "pylint/lint/expand_modules.py", "expand_modules pre-normalization ignore check", 76, 81),
            source("normalize", "pylint/lint/expand_modules.py", "expand_modules later normalization", 110, 110),
            source("fixture", "tests/regrtest_data/directory/ignored_subdirectory/failing.py", "fixture", 1, 1),
        ],
        "trigger": {"cwd": "tests/regrtest_data/directory", "argv": [".", "--recursive=y", "--ignore-paths=^ignored_subdirectory/.*"], "setup": "Remove regrtest_data from sys.path as in the test, then restore sys.path/cwd after the run.", "platform": "The concrete probe models POSIX traversal/path syntax."},
        "expected": "Files below ignored_subdirectory are excluded and the supplied runner helper observes exit code 0.",
        "actual": "The isolated POSIX discovery emits './ignored_subdirectory/failing.py'. _is_ignored_file returns False on that raw path and True on its normalized form. Final diagnostic messages and nonzero CLI status were not executed.",
        "roots": ["ignore", "expand"],
        "origin": "Ignore-path matching consumes an unnormalized path containing a leading './'. The anchored regex expects 'ignored_subdirectory/...', so the ignore check rejects the intended match before later normalization occurs.",
        "origin_uncertainty": "The source locates the normalization/matching-order problem. Directory-level matching alone is not sufficient: the regex includes '/', so the directory name itself need not match even when normalized; file-level filtering is important.",
        "nodes": [
            node("n1", "prerequisite", "discover", "Recursive discovery starts at '.'", "The walked root and yielded file path retain the './' prefix under POSIX traversal."),
            node("n2", "origin_candidate", "ignore", "Ignore regex is anchored at ^ignored_subdirectory/", "_is_ignored_file passes the raw element to re.match without normalization, so './ignored_subdirectory/failing.py' is not ignored."),
            node("n3", "propagation", "expand", "Discovered file reaches module expansion", "expand_modules applies the same ignore predicate before its later filepath normalization, retaining the unwanted file."),
            node("n4", "consumption", "iterate", "File was retained by expansion", "The ignored file remains eligible for lint analysis; later normalization cannot undo the earlier missed filter."),
            node("n5", "predicted_observation", "wrapper", "Extra file affects lint outcome", "The test helper's expected exit-code-0 assertion can fail; full diagnostic/exit behavior remains unexecuted."),
        ],
        "edges": [edge("n1", "n2", "raw POSIX file path; discovery and raw-file matcher checked separately"), edge("n2", "n3", "same unnormalized path evaluated by pre-expansion ignore check"), edge("n3", "n4", "retained module descriptor"), edge("n4", "n5", "additional lint input and resulting status; terminal result not observed")],
        "navigation": ["wrapper", "alias", "test_run", "entry", "check", "discover", "iterate", "expand", "ignore", "normalize"],
        "summary": "Recursive '.' traversal supplies paths with a leading './'. Ignore matching runs on those raw paths, so the anchored pattern misses the intended files. Module expansion normalizes only after filtering, leaving the ignored file available for analysis; the final exit-code consequence still needs full-test execution.",
        "brt": "Preserve the test's cwd and sys.path setup, recursively lint '.', and assert successful status with the anchored ignore rule. Include an ignored file that would otherwise affect lint status; a regex-only probe does not verify the full runner behavior.",
        "limits": ["The probe uses a synthetic POSIX walk and actual extracted discovery/matcher functions on a Windows host; it does not reproduce full module expansion or lint exit status.", "The candidate mechanism is supported locally, but the exact final diagnostics and code require the historical runtime and supplied fixture suite."],
    },
    "7277": {
        "tests": [("tests/test_self.py", "TestRunTC.test_modify_sys_path", "mixed_regression_and_controls", "Remove leading '', '.', or cwd; preserve leading '/do_not_remove' and all following paths")],
        "entry": "entry", "entry_resolution": "tests/test_self.py imports modify_sys_path directly from pylint; the static method invokes that function with isolated sys.path, mocked os.getcwd, and controlled PYTHONPATH.",
        "entry_runtime_scope": "observed_in_isolated_probe",
        "sources": [
            source("entry", "pylint/__init__.py", "modify_sys_path", 80, 105),
            source("origin", "pylint/__init__.py", "modify_sys_path unconditional pop", 99, 105),
            source("aliases", "tests/test_self.py", "modify_sys_path import", 29, 33),
        ],
        "trigger": {"sys_path_first_entries": ["", ".", "/do_not_remove"], "PYTHONPATH": "unset for the added subcases", "cwd": "/tmp/pytest-of-root/pytest-0/test_do_not_import_files_from_0", "control": "Leading '' and '.' are correctly removed already; unrelated first entry must remain."},
        "expected": "A leading unrelated path is preserved, whereas entries representing the current directory are removed.",
        "actual": "The extracted function removes the first element in all subcases. The '/do_not_remove' branch loses that path; '', '.', and cwd controls match expectations.",
        "roots": ["origin"],
        "origin": "modify_sys_path unconditionally pops sys.path[0], assuming the first entry always represents the working directory instead of checking its value.",
        "origin_uncertainty": "The failing added branch directly isolates this operation. PYTHONPATH colon handling is existing surrounding behavior and was not exhaustively rechecked by the probe.",
        "nodes": [
            node("n1", "trigger", "entry", "sys.path begins with '/do_not_remove'; PYTHONPATH is unset", "The first path is unrelated to cwd and must be retained under the supplied assertion."),
            node("n2", "origin_candidate", "origin", "Function begins execution", "sys.path.pop(0) removes the unrelated path before any cwd/value comparison."),
            node("n3", "observation", "entry", "No PYTHONPATH colon branch applies", "The shortened list returns to the test and fails equality with the original paths."),
        ],
        "edges": [edge("n1", "n2", "mutable sys.path list", True), edge("n2", "n3", "in-place removal preserved on return", True)],
        "navigation": ["aliases", "entry", "origin"],
        "summary": "The function assumes the first import path is always the current directory and removes it unconditionally. The new '/do_not_remove' subcase violates that assumption and loses a legitimate path; the added '' and '.' subcases are passing controls.",
        "brt": "Isolate sys.path and PYTHONPATH, mock cwd, then call modify_sys_path with an unrelated leading entry and assert the entire list is unchanged. Include '', '.', and cwd removal controls; restore global state after each case.",
        "limits": ["The original pytest test function was not executed; its added state transitions were exercised using the unmodified extracted production function and isolated fake sys/os objects.", "This is a short direct mechanism; no intermediate functions should be invented to make the path longer."],
    },
    "8898": {
        "tests": [("tests/config/test_config.py", "test_csv_regex_comma_in_quantifier", "parameterized_regression_and_controls", "Compiled patterns equal [re.compile(regex) for regex in expected] for all four CSV_REGEX_COMMA_CASES"),
                  ("tests/config/test_config.py", "test_csv_regex_error", "modified_error_regression", "stderr reports the intact malformed regex token '(foo{1,}' before the list-separating comma")],
        "entry": "entry", "entry_resolution": "The nested _template_run calls imported pylint.lint.Run, then reads r.linter.config.bad_names_rgxs. The supplied --bad-names-rgx spelling is an argparse abbreviation of registered --bad-names-rgxs.",
        "entry_runtime_scope": "source_verified_only",
        "sources": [
            source("entry", "pylint/lint/run.py", "Run.__init__", 145, 165),
            source("config", "pylint/config/config_initialization.py", "_config_initialization command-line parse", 76, 83),
            source("parse", "pylint/config/arguments_manager.py", "_ArgumentsManager._parse_command_line_configuration", 224, 234),
            source("option", "pylint/checkers/base/name_checker/checker.py", "NameChecker bad-names-rgxs option", 228, 237),
            source("type", "pylint/config/argument.py", "_TYPE_TRANSFORMERS regexp_csv entry", 136, 148),
            source("regex_csv", "pylint/config/argument.py", "_regexp_csv_transfomer", 114, 119),
            source("csv", "pylint/config/argument.py", "_csv_transformer", 51, 53),
            source("check_csv", "pylint/utils/utils.py", "_check_csv", 250, 253),
            source("split", "pylint/utils/utils.py", "_splitstrip", 212, 232),
            source("compile", "pylint/config/argument.py", "_regex_transformer", 105, 111),
            source("register", "pylint/config/arguments_manager.py", "_ArgumentsManager._add_parser_option", 126, 140),
            source("assign_type", "pylint/config/argument.py", "_StoreArgument type assignment", 247, 254),
        ],
        "trigger": {"bad_names_rgxs": "foo, bar{1,3}", "expected_patterns": ["foo", "bar{1,3}"], "controls": ["foo", "foo,bar", "foo, bar"], "error_case": "(foo{1,}, foo{1,3}})", "test_parameterization": "Four input/expected pairs from CSV_REGEX_COMMA_CASES; three controls and one quantifier regression."},
        "expected": "Commas inside regex quantifiers remain in their regex token; only list-separating commas split patterns. The modified error message preserves the intact first malformed regex token.",
        "actual": "The extracted conversion pipeline compiles ['foo', 'bar{1', '3}'] instead of ['foo', 'bar{1,3}']. All three ordinary CSV controls pass. The error case reports '(foo{1' rather than '(foo{1,}'.",
        "roots": ["regex_csv"],
        "origin": "The regexp_csv converter applies generic CSV splitting to regex syntax. _splitstrip uses string.split(',') without quantifier awareness, destroying commas that belong inside a pattern.",
        "origin_uncertainty": "_splitstrip is correct for ordinary CSV users; the semantic mismatch is the regex-specific caller's choice of generic splitting. This analysis does not infer that the generic helper should be changed globally.",
        "nodes": [
            node("n1", "prerequisite", "option", "CLI option bad-names-rgx abbreviates bad-names-rgxs", "The option's regexp_csv type selects the regex-list conversion callback during configuration parsing."),
            node("n2", "origin_candidate", "regex_csv", "The regex list contains a quantifier comma", "The regex-specific converter delegates tokenization to _csv_transformer rather than preserving regex-internal commas."),
            node("n3", "propagation", "split", "Generic CSV path reaches _check_csv -> _splitstrip", "string.split(',') turns 'foo, bar{1,3}' into 'foo', 'bar{1', and '3}'."),
            node("n4", "consumption", "compile", "The resulting fragments are individually compilable", "re.compile accepts each fragment, so the invalid tokenization can silently become three patterns instead of two."),
            node("n5", "observation", "parse", "Parsed patterns are stored in the configuration namespace", "The list read by the test differs from the expected compiled patterns; a malformed grouped input also produces a truncated-token error."),
        ],
        "edges": [edge("n1", "n2", "registered argument type callback; selection source-supported"), edge("n2", "n3", "raw regex-list string through _csv_transformer/_check_csv", True), edge("n3", "n4", "incorrectly split pattern strings", True), edge("n4", "n5", "compiled list/error; isolated argparse stores the same pattern list, but full manager parsing was not executed")],
        "navigation": ["entry", "config", "parse", "option", "register", "assign_type", "type", "regex_csv", "csv", "check_csv", "split", "compile"],
        "summary": "The regex-list option uses a generic comma splitter. A quantifier's comma is treated as a list separator, and its fragments can compile successfully, silently corrupting the stored pattern list. Grouped malformed input instead reports an error for a prematurely truncated token.",
        "brt": "Construct Run with --bad-names-rgx=foo, bar{1,3} and a valid empty module, then compare config.bad_names_rgxs against two expected compiled regexes. Keep ordinary CSV controls and the modified malformed-regex message case; a test expecting every quantifier split to raise would miss the silent corruption.",
        "limits": ["The converter/argparse boundary was executed with actual extracted functions and actual option abbreviation, but full Run/plugin option registration was source-checked only.", "The test patch covers quantifiers, not every possible escaped comma, character class, grouping, or regex grammar case. No general regex-list parser correctness claim is made."],
    },
}


def patched_tests(root, patch):
    """Apply a test-only unified diff in memory, checking all old context."""
    sections = re.split(r"(?=^diff --git )", patch, flags=re.M)
    result = {}
    for section in sections:
        if not section.strip():
            continue
        lines = section.splitlines()
        new_header = next(line for line in lines if line.startswith("+++ b/"))
        rel = new_header[len("+++ b/"):]
        if not rel.startswith("tests/") or ".." in Path(rel).parts:
            raise ValueError(f"Non-test/unsafe diff path: {rel}")
        base = (root / rel).read_text(encoding="utf-8").splitlines()
        output = []
        position = 0
        in_hunk = False
        for line in lines:
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", line)
            if match:
                start = int(match.group(1)) - 1
                assert start >= position
                output.extend(base[position:start])
                position = start
                in_hunk = True
                continue
            if not in_hunk or line.startswith("\\ No newline"):
                continue
            if line.startswith((" ", "-")):
                assert base[position] == line[1:], (rel, position + 1)
                if line.startswith(" "):
                    output.append(base[position])
                position += 1
            elif line.startswith("+"):
                output.append(line[1:])
            else:
                raise ValueError(f"Unexpected test diff line: {line!r}")
        output.extend(base[position:])
        result[rel] = "\n".join(output) + "\n"
    return result


def test_span(text, qualified):
    body = ast.parse(text).body
    for part in qualified.split("."):
        item = next(n for n in body if getattr(n, "name", None) == part)
        body = item.body
    start = min([item.lineno, *[d.lineno for d in item.decorator_list]])
    return start, item.end_lineno


def build(row):
    instance = row["instance_id"]
    issue = instance.rsplit("-", 1)[1]
    curated = CASES[issue]
    root = ROOT / "repos" / instance
    tests = patched_tests(root, row["test_patch"])
    test_cases = []
    for rel, qualified, role, expectation in curated["tests"]:
        start, end = test_span(tests[rel], qualified)
        test_cases.append({"selector": rel + "::" + qualified.replace(".", "::"),
                           "selector_scope": "Whole test function, including all parameterized cases when applicable; generated pytest parameter IDs were not collected",
                           "function": qualified, "role": role,
                           "source": {"path": rel, "revision_kind": "base_plus_test_patch_in_memory",
                                      "start_line": start, "end_line": end,
                                      "reconstructed_file_sha256": sha(tests[rel])},
                           "expected_behavior": expectation})
    sources = []
    for item in curated["sources"]:
        evidence = dict(item)
        content = (root / item["path"]).read_text(encoding="utf-8")
        lines = content.splitlines()
        assert 1 <= item["start_line"] <= item["end_line"] <= len(lines)
        evidence.update(revision=row["base_commit"], file_text_sha256=sha(content),
                        excerpt="\n".join(lines[item["start_line"] - 1:item["end_line"]]))
        sources.append(evidence)
    if curated["entry"] == "entry" and issue != "7277":
        rel = "pylint/lint/__init__.py"
        text = (root / rel).read_text(encoding="utf-8")
        line = next(i for i, value in enumerate(text.splitlines(), 1) if "from pylint.lint.run import Run" in value)
        sources.append({**source("lint_export", rel, "Run re-export", line, line),
                        "revision": row["base_commit"], "file_text_sha256": sha(text),
                        "excerpt": text.splitlines()[line - 1]})
    probe_path = OUT / f"{instance}.probe.json"
    probe_text = probe_path.read_text(encoding="utf-8")
    probe = json.loads(probe_text)
    assert probe["base_commit"] == row["base_commit"]
    assert probe["test_patch_sha256"] == sha(row["test_patch"])
    assert probe["production_patch_read"] is False
    for rel, expected_hash in probe["probe_input_file_sha256"].items():
        assert hashlib.sha256((root / rel).read_bytes()).hexdigest() == expected_hash, rel
    nodes = curated["nodes"]
    by_node = {n["id"]: n for n in nodes}
    for relation in curated["edges"]:
        refs = [by_node[relation["source"]]["source_ref"], by_node[relation["target"]]["source_ref"]]
        if relation["support"] == "observed_in_isolated_probe":
            refs.append("runtime_probe")
        relation["evidence_refs"] = refs
        relation["scope_note"] = "Support is limited to the declared source/probe scope; this edge is not full-test or fix-validated."
    entry = next(s for s in sources if s["id"] == curated["entry"])
    entry_text = (root / entry["path"]).read_text(encoding="utf-8")
    entry_start, entry_end = test_span(entry_text, entry["symbol"])
    return {
        "schema_version": "1.0", "instance_id": instance, "repo": row["repo"],
        "base_commit": row["base_commit"], "source_dataset": row["source_dataset"],
        "dataset_csv": str(CSV.relative_to(ROOT)).replace("\\", "/"),
        "checkout": f"repos/{instance}",
        "construction_method": "Manually curated per-instance source navigation; reproducible unmodified-AST function probes; deterministic packaging, not automated path inference",
        "input_policy": {"mode": "test_patch_only", "production_patch_read": False,
                         "issue_text_read": False, "fixed_version_read": False,
                         "sibling_implementations_used_as_clues": False,
                         "allowed_fields": ["instance_id", "repo", "base_commit", "test_patch", "source_dataset"],
                         "test_patch_sha256": sha(row["test_patch"])},
        "test_cases": test_cases,
        "production_entry_points": [{"symbol": entry["symbol"], "source_ref": entry["id"],
                                     "path": entry["path"], "start_line": entry_start,
                                     "end_line": entry_end, "verification": "source_verified",
                                     "runtime_scope": curated["entry_runtime_scope"],
                                     "resolution": curated["entry_resolution"]}],
        "trigger": curated["trigger"],
        "observation": {"expected_from_test_patch": curated["expected"],
                        "actual_and_scope": curated["actual"], "evidence_ref": "runtime_probe",
                        "complete_patched_test_executed": False},
        "navigation_path": [{"step": i + 1, "source_ref": key,
                             "symbol": next(s["symbol"] for s in sources if s["id"] == key)}
                            for i, key in enumerate(curated["navigation"])],
        "navigation_semantics": "Source-inspection route, not a runtime stack or claim that every adjacent item is a direct call; the test entry is recorded separately",
        "root_cause": {"status": "candidate", "source_refs": curated["roots"],
                       "transition_or_omission": curated["origin"],
                       "justification": curated["summary"], "uncertainty": curated["origin_uncertainty"]},
        "diagnostic_nodes": nodes, "diagnostic_edges": curated["edges"],
        "mechanism_summary": curated["summary"], "source_evidence": sources,
        "runtime_evidence": {"id": "runtime_probe", "artifact": probe_path.relative_to(ROOT).as_posix(),
                             "artifact_text_sha256": sha(probe_text),
                             "command": "python -m scripts.probe_pylint_diagnostics",
                             "probe_script": "scripts/probe_pylint_diagnostics.py",
                             "probe_script_text_sha256": sha((ROOT / "scripts/probe_pylint_diagnostics.py").read_text(encoding="utf-8")),
                             "runtime": probe["runtime"], "scope": probe["probe"]["scope"],
                             "observations": probe["probe"],
                             "full_test_not_run_reason": "Available Python lacks pytest, astroid, platformdirs, isort, and mccabe; no historical environment was installed in this task."},
        "generation_handoff": {"policy": "oracle_test_conditioned", "task": "BRT",
                               "entry": curated["entry_resolution"], "trigger": curated["trigger"],
                               "mechanism": curated["summary"], "expected_behavior": curated["expected"],
                               "test_design": curated["brt"],
                               "evidence_limit": "Source-supported candidate with isolated-probe evidence; full regression test/fixed-version contrast not run. Test-derived facts are privileged for blind BRT discovery."},
        "limitations": curated["limits"], "diagnosis_status": "probe_supported_candidate",
        "full_test_status": "not_run", "review_status": "self_reviewed",
    }


def main():
    with CSV.open(encoding="utf-8", newline="") as handle:
        rows = [{k: r[k] for k in ("instance_id", "repo", "base_commit", "test_patch", "source_dataset")}
                for r in csv.DictReader(handle) if r["repo"] == "pylint-dev/pylint"]
    assert {r["instance_id"].rsplit("-", 1)[1] for r in rows} == set(CASES)
    records = [build(row) for row in rows]
    output = ROOT / "diagnostic_path/pylint_verified_diagnostic_paths.jsonl"
    output.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    print(f"Wrote {len(records)} records: {output}")


if __name__ == "__main__":
    main()
