"""Live deciders: outside programs run session by session (v0.6.4)."""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from quantarium import deciders
from quantarium.api import Api
from quantarium.decisions import load_decisions
from quantarium.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
STEADY = ROOT / "examples" / "scenarios" / "steady_bull"
FAKE = Path(__file__).with_name("fake_decider.py")


class DeciderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        shutil.copytree(STEADY, self.root / "scenarios" / "steady_bull")
        self.api = Api(Workspace(self.root))

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def register(self, name: str, *arguments: str, **extra) -> dict:
        return self.api.command("decider-create", {"name": name, "command": [sys.executable, str(FAKE), *arguments],
                                                   **extra})

    def cell(self, decider: str) -> dict:
        record = self.api.command("cell-create", {"name": f"Decider {decider}"})
        return self.api.command("cell-save", {"id": record["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000, "sleeves": [{"decider": decider}]}})

    def deploy(self, cell: dict) -> dict:
        return self.api.wait(self.api.submit("deploy-suite", {"cells": [cell["id"]],
                                                               "scenarios": ["scenarios/steady_bull"]})["id"])

    def test_registration_is_checked(self) -> None:
        self.register("ok", "trend")
        for params in ({"name": "../x", "command": [sys.executable]}, {"name": "ok", "command": [sys.executable]},
                       {"name": "empty", "command": []}, {"name": "missing", "command": ["no-such-program-xyz"]},
                       {"name": "env", "command": [sys.executable], "env": ["BAD NAME"]},
                       {"name": "cutoff", "command": [sys.executable], "training_cutoff": "soon"}):
            with self.subTest(params["name"]), self.assertRaises(ValueError):
                self.api.command("decider-create", params)
        self.assertEqual([item["name"] for item in self.api.get("deciders", {})], ["ok"])

    def test_a_decider_runs_session_by_session_and_leaves_a_tape(self) -> None:
        self.register("trend", "trend")
        cell = self.cell("trend")
        self.assertEqual(self.api.cells.check(self.api.cells.get(cell["id"])), [])
        job = self.deploy(cell)
        self.assertEqual(job["status"], "succeeded", job["error"])
        tapes = list((self.root / "decisions" / "recorded").glob("trend-*.jsonl"))
        self.assertEqual(len(tapes), 1)
        recorded = load_decisions(tapes[0])
        self.assertGreater(len(recorded.decisions), 50)
        self.assertEqual(recorded.source["system"], "decider trend")
        self.assertIn("decisions/recorded/" + tapes[0].name, [item["key"] for item in self.api.get("workspace", {})["decisions"]])

    def cell_errors(self) -> str:
        errors = []
        for database in self.root.rglob("*.sqlite"):
            with closing(sqlite3.connect(database)) as db:
                errors += [row[0] for row in db.execute("SELECT error FROM cells WHERE error IS NOT NULL")]
        return " | ".join(errors)

    def test_a_misbehaving_decider_stops_its_cell_with_a_reason(self) -> None:
        self.register("garbage", "garbage")
        self.assertEqual(self.deploy(self.cell("garbage"))["status"], "succeeded")
        self.assertIn("other than JSON", self.cell_errors())
        self.register("slow", "slow")
        with mock.patch.object(deciders, "SESSION_TIMEOUT", 1.0):
            self.deploy(self.cell("slow"))
        self.assertIn("took longer", self.cell_errors())

    def test_an_endless_reply_is_refused_without_reading_it_into_memory(self) -> None:
        import tracemalloc

        self.register("flood", "flood")
        tracemalloc.start()
        try:
            self.deploy(self.cell("flood"))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertIn("more than", self.cell_errors())
        self.assertLess(peak, 20 * 1024 * 1024)  # the line was 40 million characters

    def test_a_decider_sees_only_the_variables_named_for_it(self) -> None:
        with mock.patch.dict(os.environ, {"DECIDER_KEY": "given", "OTHER_SECRET": "kept", "ANTHROPIC_API_KEY": "kept"}):
            self.register("env", "env", "DECIDER_KEY,OTHER_SECRET,ANTHROPIC_API_KEY", env=["DECIDER_KEY"])
            job = self.deploy(self.cell("env"))
        self.assertEqual(job["status"], "succeeded", job["error"])
        tape = next((self.root / "decisions" / "recorded").glob("env-*.jsonl"))
        notes = {entry.note for entry in load_decisions(tape).decisions.values()}
        self.assertEqual(notes, {"seen:DECIDER_KEY"})

    def test_the_examples_work(self) -> None:
        self.api.command("decider-create", {"name": "example", "command": [
            sys.executable, str(ROOT / "examples" / "deciders" / "moving_average.py"), "10"]})
        job = self.deploy(self.cell("example"))
        self.assertEqual(job["status"], "succeeded", job["error"])
        self.assertEqual(self.cell_errors(), "")
        self.assertEqual(len(load_decisions(ROOT / "examples" / "decisions" / "steady_bull_weekly.csv").decisions), 47)

    def test_the_lab_runs_a_decider_live_and_pins_it(self) -> None:
        self.register("trend", "trend")
        experiment = self.api.command("lab-register", {
            "idea": "decider", "name": "Decider", "cells": [self.cell("trend")["id"]],
            "scenarios": ["scenarios/steady_bull"], "pass_rule": {"min_mean_excess_return_pp": 0, "min_sessions": 20}})
        self.assertEqual(experiment["spec"]["decisions"], "live")
        job = self.api.wait(self.api.submit("lab-run", {"id": experiment["id"]})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])
        verdict = job["result"]["result"]["verdict"]
        # A synthetic market is one no model can remember, so the verdict is not capped.
        self.assertIn(verdict["status"], {"supported_within_tested_scope", "not_supported"})
        self.assertTrue(any("outside program" in caveat for caveat in verdict["caveats"]))
        # Registering a changed decider under the same name makes a registration stale.
        second = self.api.command("lab-register", {
            "idea": "decider2", "name": "Decider", "cells": [self.cell("trend")["id"]],
            "scenarios": ["scenarios/steady_bull"], "pass_rule": {"min_mean_excess_return_pp": 0, "min_sessions": 20}})
        self.api.command("decider-delete", {"name": "trend"})
        self.register("trend", "garbage")
        self.assertEqual(self.api.wait(self.api.submit("lab-run", {"id": second["id"]})["id"])["status"], "failed")


if __name__ == "__main__":
    unittest.main()
