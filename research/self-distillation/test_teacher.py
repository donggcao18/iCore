"""Regression checks for hint isolation and the evidence trail."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from run_teacher import Navigation, Snapshot, ToolError, describe_api_failure, request_data, rollout, windows_conda_ca_bundle
from cases import Case


class APIErrorTest(unittest.TestCase):
    def test_openrouter_budget_error_reports_the_affordable_token_limit(self):
        exc = SimpleNamespace(status_code=402, body={"error": {
            "message": "You requested up to 4096 tokens, but can only afford 1,234. Private provider detail.",
            "metadata": {"limit_source": "openrouter_key_limit"},
        }})
        message = describe_api_failure(exc, base_url="https://openrouter.ai/api/v1",
                                       max_tokens=4096, completed=0, output=Path("test.jsonl"))
        self.assertIn("API key spending limit", message)
        self.assertIn("--max-tokens 1234", message)
        self.assertIn("No rollout was completed.", message)
        self.assertNotIn("Private provider detail", message)

    def test_openrouter_in_flight_budget_reports_retry_after(self):
        exc = SimpleNamespace(status_code=402, body={"metadata": {"limit_source": "openrouter_in_flight_budget"}},
                              response=SimpleNamespace(headers={"Retry-After": "12"}))
        message = describe_api_failure(exc, base_url="https://openrouter.ai/api/v1",
                                       max_tokens=4096, completed=1, output=Path("test.jsonl"))
        self.assertIn("Retry after 12 seconds.", message)
        self.assertIn("Saved 1 completed rollout(s) to test.jsonl.", message)


class CertificateTest(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows Conda directory layout")
    def test_missing_conda_bash_bundle_uses_windows_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            bundle = prefix / "Library" / "ssl" / "cacert.pem"
            bundle.parent.mkdir(parents=True)
            bundle.touch()
            legacy = (prefix / "ssl" / "cacert.pem").as_posix()
            for name in (legacy, f"/{legacy[0]}{legacy[2:]}"):
                with self.subTest(name=name), patch.object(sys, "prefix", directory), patch.object(
                    os, "environ", {"SSL_CERT_FILE": name},
                ):
                    self.assertEqual(windows_conda_ca_bundle(), bundle)

    def test_other_missing_bundle_has_a_specific_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            os, "environ", {"SSL_CERT_FILE": str(Path(directory) / "missing.pem")},
        ):
            with self.assertRaisesRegex(ValueError, "SSL_CERT_FILE points to a missing CA bundle"):
                windows_conda_ca_bundle()


class TeacherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        tree = Path(directory.name)

        def git(*args):
            return subprocess.check_output(["git", "-C", str(tree), *args], text=True).strip()

        git("init", "-q")
        (tree / "cli.py").write_text("from worker import compute\n\ndef entry():\n    return compute(sticky=True)\n"
                                    + "# filler\n" * 200,
                                    encoding="utf-8")
        (tree / "worker.py").write_text("def compute(sticky):\n    return 0 if sticky else 1\n", encoding="utf-8")
        git("add", "cli.py", "worker.py")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgSign=false",
            "commit", "-qm", "Buggy snapshot")
        commit = git("rev-parse", "HEAD")
        (tree / "worker.py").write_text("def compute(sticky):\n    return 1\n", encoding="utf-8")
        (tree / "private.py").write_text("answer = 'hidden'\n", encoding="utf-8")
        cls.case = Case("fixture", tree, commit, "sticky mode returns zero", ("worker.py::compute",), "cli.py::entry")
        cls.snapshot = Snapshot(cls.case)

    def test_checkout_edits_are_invisible(self):
        self.assertNotIn("private.py", self.snapshot.lines)
        self.assertIn("    return 0 if sticky else 1", self.snapshot.lines["worker.py"])

    def test_unseen_targets_and_keywords_are_rejected(self):
        nav = Navigation(self.snapshot, self.case.issue)
        for action in (
            {"tool": "search_dir", "search_term": "compute", "evidence": "issue"},
            {"tool": "search_dir", "search_term": "compute", "evidence": "private_training_hint"},
            {"tool": "search_file", "search_term": "sticky", "evidence": "issue", "file": "worker.py"},
            {"tool": "open", "path": "worker.py"},
            {"tool": "open", "path": "../worker.py"},
            {"tool": "submit", "ranked": ["worker.py::compute"], "diagnosis": "Known", "evidence": ["issue"]},
        ):
            with self.subTest(action=action), self.assertRaises(ToolError):
                nav.action(action, 1)

    def test_issue_to_root_trajectory(self):
        actions = iter([
            {"tool": "search_dir", "search_term": "sticky", "evidence": "issue"},
            {"tool": "open", "path": "cli.py"},
            {"tool": "search_file", "search_term": "compute", "evidence": "o2", "file": "worker.py"},
            {"tool": "open", "path": "worker.py", "line_number": 1},
            {"tool": "submit", "ranked": ["worker.py::compute"], "diagnosis": "The entry passes sticky=True. The function then returns 0.",
             "evidence": ["o2", "o4"]},
        ])

        def complete(messages):
            initial = json.loads(messages[1]["content"])
            self.assertEqual(initial["private_training_hint"]["root_candidates"], list(self.case.roots))
            self.assertNotIn("public_entry", initial)
            return "<think>Reasoning omitted</think>\n" + json.dumps(next(actions)), {}

        result = rollout(self.case, self.snapshot, "teacher", 5, complete)
        self.assertEqual(result["status"], "submitted")
        self.assertTrue(result["metrics"]["review_candidate"])
        self.assertEqual(result["root_reads"][0]["chain"], ["issue", "o1", "o2", "o3", "o4"])
        self.assertNotIn("Reasoning omitted", json.dumps(result))

    def test_matched_conditions_only_change_the_prescribed_fields(self):
        blind = request_data(self.case, "blind", 12)
        self.assertEqual(blind, {"issue": self.case.issue, "turns_remaining": 12})
        self.assertEqual(request_data(self.case, "entry-blind", 12), {**blind, "public_entry": self.case.entry})
        nav = Navigation(self.snapshot, self.case.issue, self.case.entry)
        result = nav.action({"tool": "open", "path": "cli.py"}, 1)
        self.assertIn(self.case.entry, nav.inspected[result["id"]])
        self.assertEqual(nav.chain(result["id"]), ["entry", "o1"])

    def test_window_commands_use_the_open_file(self):
        nav = Navigation(self.snapshot, "The sticky option in cli.py is ignored.")
        files = nav.action({"tool": "find_file", "file_name": "cli.py", "evidence": "issue"}, 1)
        self.assertEqual(files["files"], [{"path": "cli.py"}])
        first = nav.action({"tool": "open", "path": "cli.py"}, 2)
        self.assertEqual((first["start"], first["end"]), (1, 100))
        with self.assertRaises(ToolError):
            nav.action({"tool": "goto", "line_number": 101}, 3)
        second = nav.action({"tool": "scroll_down"}, 4)
        self.assertEqual((second["start"], second["end"]), (101, 200))
        centered = nav.action({"tool": "goto", "line_number": 125}, 5)
        self.assertTrue(centered["start"] <= 125 <= centered["end"])
        matches = nav.action({"tool": "search_file", "search_term": "sticky", "evidence": "issue"}, 6)
        self.assertEqual(matches["path"], "cli.py")
        self.assertEqual(matches["matches"][0]["line_number"], 4)
        up = nav.action({"tool": "scroll_up"}, 7)
        self.assertEqual(up["start"], 1)


if __name__ == "__main__":
    unittest.main()
