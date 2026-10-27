"""Evidence reports: one self-contained HTML page per Lab experiment, and checking it (v0.7.1)."""

from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quantarium.__main__ import main
from quantarium.api import Api
from quantarium.evidence import render, verify
from quantarium.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class EvidenceReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        (self.root / "strategies").mkdir()
        shutil.copy(ROOT / "examples/strategies.py", self.root / "strategies/strategies.py")
        shutil.copytree(ROOT / "examples/scenarios/steady_bull", self.root / "scenarios/steady_bull")
        self.api = Api(Workspace(self.root))
        record = self.api.command("cell-create", {"name": "Trend <b>bold</b>"})
        self.api.command("cell-save", {"id": record["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000,
            "sleeves": [{"script": "strategies/strategies.py", "class": "TrendFollower", "parameters": {"window": 10}}]}})
        experiment = self.api.command("lab-register", {
            "idea": "trend", "name": "Trend </script><script>alert(1)</script>", "cells": [record["id"]],
            "scenarios": ["scenarios/steady_bull"], "pass_rule": {"min_mean_excess_return_pp": 0, "min_sessions": 20}})
        job = self.api.wait(self.api.submit("lab-run", {"id": experiment["id"]})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])
        self.id = experiment["id"]

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def page(self) -> str:
        return self.api.get(f"lab-report/{self.id}", {})["html"]

    def test_the_page_is_self_contained_and_renders_every_value_as_text(self) -> None:
        page = self.page()
        self.assertNotRegex(page, r"(?i)(src|href)\s*=|https?://|@import|url\(")
        self.assertEqual(page.count("<script"), 1)
        self.assertIn('<script type="application/json" id="quantarium-record"', page)
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<b>bold</b>", page)
        self.assertIn("&lt;/script&gt;", page)
        for heading in ("Candidate against its controls", "Allowing for the number of attempts",
                        "What was frozen at registration", "Limits"):
            self.assertIn(heading, page)

    def test_a_shared_report_hides_this_computers_paths_and_still_verifies(self) -> None:
        # Decision files, inboxes, deciders, and model access keep absolute paths in the record.
        ledger = self.root / ".quantarium" / "lab.json"
        items = json.loads(ledger.read_text(encoding="utf-8"))
        parameters = items[0]["spec"]["candidate"]["strategies"][0].setdefault("parameters", {})
        parameters["decisions"] = str(self.root / "decisions" / "mine.json")
        parameters["elsewhere"] = str(Path.home() / "Documents" / "notes.txt")
        ledger.write_text(json.dumps(items), encoding="utf-8")
        page = self.page()
        for secret in (str(self.root), str(self.root).replace("\\", "\\\\"), self.root.as_posix(),
                       str(Path.home()).replace("\\", "\\\\"), Path.home().name):
            self.assertNotIn(secret, page)
        self.assertIn("<workspace>", page.replace("\\u003c", "<").replace("\\u003e", ">"))
        checked = verify(page, self.root)
        self.assertTrue(checked["internally_consistent"] and checked["matches_ledger"], checked)

    def test_verify_checks_the_page_and_the_workspace(self) -> None:
        page = self.page()
        alone = verify(page)
        self.assertTrue(alone["internally_consistent"])
        here = verify(page, self.root)
        self.assertTrue(here["internally_consistent"] and here["in_ledger"] and here["matches_ledger"]
                        and here["files_unchanged"], here)
        # Editing a number in the embedded record breaks it.
        forged = re.sub(r'"excess_return_pp":(-?[0-9.e+-]+)', '"excess_return_pp":99', page, count=1)
        self.assertFalse(verify(forged)["internally_consistent"])
        # A changed script shows up as changed.
        (self.root / "strategies/strategies.py").write_text("# changed\n", encoding="utf-8")
        self.assertEqual(verify(page, self.root)["changed_files"], ["strategies/strategies.py"])
        with self.assertRaises(ValueError):
            verify("<html>nothing here</html>")

    def test_the_command_line(self) -> None:
        output = io.StringIO()
        with mock.patch("sys.argv", ["quantarium", "lab", "report", self.id, "--workspace", str(self.root)]), \
                redirect_stdout(output):
            main()
        written = Path(json.loads(output.getvalue())["report"])
        self.assertTrue(written.is_file())
        output = io.StringIO()
        with mock.patch("sys.argv", ["quantarium", "lab", "verify-report", str(written), "--workspace", str(self.root)]), \
                redirect_stdout(output):
            main()
        self.assertTrue(json.loads(output.getvalue())["matches_ledger"])

    def test_a_report_renders_without_a_result(self) -> None:
        record = self.api.lab.get(self.id)
        self.assertIn("Not run", render({**record, "result": None, "status": "registered"}))


if __name__ == "__main__":
    unittest.main()
