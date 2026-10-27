"""Rescoring: a cell replays a decision file on Quantarium's engine (v0.6.1)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from quantarium.api import Api
from quantarium.decisions import FORMAT
from quantarium.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
STEADY = ROOT / "examples" / "scenarios" / "steady_bull"


class RescoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        shutil.copytree(STEADY, self.root / "scenarios" / "steady_bull")
        (self.root / "decisions").mkdir()
        self.api = Api(Workspace(self.root))

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def write(self, name: str, text: str) -> str:
        (self.root / "decisions" / name).write_text(text, encoding="utf-8")
        return f"decisions/{name}"

    def cell(self, key: str, mode: str = "simple", symbols=("AAA",), **extra) -> dict:
        record = self.api.command("cell-create", {"name": f"Replay {len(self.api.cells.all())}"})
        return self.api.command("cell-save", {"id": record["id"], "spec": {
            "mode": mode, "symbols": list(symbols), "initial_cash": 10000,
            "sleeves": [{"decisions": key}], **extra}})

    def replay(self, cell: dict) -> tuple[dict, list[dict]]:
        job = self.api.wait(self.api.submit("deploy-static", {"cells": [cell["id"]],
                                                               "data": "scenarios/steady_bull/prices.csv"})["id"])
        if job["status"] != "succeeded":
            return job, []
        with closing(sqlite3.connect(self.root / job["result"]["db"])) as db:
            signals = [(row[0], json.loads(row[1])) for row in db.execute(
                "SELECT date, payload_json FROM events WHERE kind='signal' AND cell_name=? ORDER BY sequence",
                (cell["name"],))]
        return job, signals

    def test_a_simple_cell_follows_its_csv_and_holds_between_decisions(self) -> None:
        key = self.write("simple.csv", "date,symbol,weight\n2030-01-07,AAA,0.5\n2030-01-10,AAA,1\n")
        cell = self.cell(key)
        self.assertEqual(self.api.cells.check(self.api.cells.get(cell["id"])), [])
        job, signals = self.replay(cell)
        self.assertEqual(job["status"], "succeeded", job["error"])
        by_day = {day: payload for day, payload in signals}
        self.assertEqual(by_day["2030-01-07"]["decisions"][0]["weights"], {"AAA": 0.5})
        self.assertIsNone(by_day["2030-01-08"]["decisions"][0]["weights"])
        self.assertEqual(by_day["2030-01-10"]["decisions"][0]["weights"], {"AAA": 1.0})

    def test_a_complex_cell_replays_weights_orders_and_notes(self) -> None:
        key = self.write("complex.json", json.dumps({
            "format": FORMAT, "source": {"system": "elsewhere"}, "symbols": ["AAA", "BBB"],
            "decisions": [{"date": "2030-01-07", "weights": {"AAA": 0.4, "BBB": 0.4}, "note": "split"},
                          {"date": "2030-01-08", "weights": None,
                           "orders": [{"symbol": "AAA", "side": "sell", "kind": "stop", "shares": 5, "price": 1.0}]}]}))
        job, signals = self.replay(self.cell(key, mode="complex", symbols=("AAA", "BBB")))
        self.assertEqual(job["status"], "succeeded", job["error"])
        first = dict(signals)["2030-01-07"]["decisions"][0]
        self.assertEqual((first["weights"], first["notes"]), ({"AAA": 0.4, "BBB": 0.4}, {"note": "split"}))

    def test_problems_are_reported_before_anything_runs(self) -> None:
        good = self.write("good.csv", "date,symbol,weight\n2030-01-07,AAA,0.5\n")
        bad = self.write("bad.csv", "date,symbol,weight\n2030-01-07,AAA,2\n")
        other = self.write("other.csv", "date,symbol,weight\n2030-01-07,ZZZ,0.5\n")
        (self.root / "data").mkdir()
        (self.root / "data" / "outside.csv").write_text("date,symbol,weight\n2030-01-07,AAA,0.5\n", encoding="utf-8")
        cases = {
            "invalid": self.cell(bad), "symbols": self.cell(other), "folder": self.cell("data/outside.csv"),
            "missing": self.cell("decisions/none.csv"), "rebalance": self.cell(good, rebalance_every_sessions=2),
        }
        for name, cell in cases.items():
            with self.subTest(name):
                self.assertTrue(self.api.cells.check(self.api.cells.get(cell["id"])))
        two = self.api.command("cell-save", {"id": self.cell(good)["id"], "spec": {
            "mode": "complex", "symbols": ["AAA"], "initial_cash": 10000,
            "sleeves": [{"decisions": good}, {"decisions": good}]}})
        self.assertTrue(self.api.cells.check(self.api.cells.get(two["id"])))

    def test_a_file_changed_after_setup_stops_the_run(self) -> None:
        from quantarium.referee import ReplayDecisions

        key = self.write("pinned.csv", "date,symbol,weight\n2030-01-07,AAA,0.5\n")
        entry, _ = self.api.cells.engine_cell(self.api.cells.get(self.cell(key)["id"]))
        self.write("pinned.csv", "date,symbol,weight\n2030-01-07,AAA,1\n")
        with self.assertRaises(ValueError):
            ReplayDecisions(**entry["strategies"][0]["parameters"])

    def test_the_workspace_lists_decision_files(self) -> None:
        self.write("good.csv", "date,symbol,weight\n2030-01-07,AAA,0.5\n")
        self.write("broken.json", "{")
        listed = {item["key"]: item for item in self.api.get("workspace", {})["decisions"]}
        self.assertEqual(listed["decisions/good.csv"]["decisions"], 1)
        self.assertIn("error", listed["decisions/broken.json"])

    def register(self, cell: dict, **extra) -> dict:
        return self.api.command("lab-register", {"idea": "replay", "name": "Replay", "cells": [cell["id"]],
                                                 "scenarios": ["scenarios/steady_bull"],
                                                 "pass_rule": {"min_mean_excess_return_pp": 0, "min_sessions": 20},
                                                 **extra})

    def run_lab(self, experiment: dict) -> dict:
        return self.api.wait(self.api.submit("lab-run", {"id": experiment["id"]})["id"])

    def test_the_lab_judges_a_rescored_cell_as_exploratory_only(self) -> None:
        # Fully invested from the first close: a file that would beat the cash-holding controls easily.
        key = self.write("lab.csv", "date,symbol,weight\n2030-01-07,AAA,1\n")
        experiment = self.register(self.cell(key))
        self.assertEqual(experiment["spec"]["decisions"], "after_the_fact")
        self.assertEqual(set(experiment["spec"]["decision_files"]), {key})
        job = self.run_lab(experiment)
        self.assertEqual(job["status"], "succeeded", job["error"])
        verdict = job["result"]["result"]["verdict"]
        self.assertEqual(verdict["status"], "unknown")
        self.assertTrue(any("hindsight" in caveat for caveat in verdict["caveats"]))
        self.assertIsNotNone(verdict["rows"][0]["excess_return_pp"])

    def test_a_decision_file_changed_after_registration_makes_it_stale(self) -> None:
        key = self.write("stale.csv", "date,symbol,weight\n2030-01-07,AAA,0.5\n")
        experiment = self.register(self.cell(key))
        self.write("stale.csv", "date,symbol,weight\n2030-01-07,AAA,1\n")
        self.assertEqual(self.run_lab(experiment)["status"], "failed")
        self.assertEqual(self.api.get(f"lab/{experiment['id']}", {})["status"], "stale")

    def test_decisions_cannot_be_disguised_and_idle_scenarios_are_named(self) -> None:
        cell = self.cell(self.write("late.csv", "date,symbol,weight\n2035-01-07,AAA,0.5\n"))
        with self.assertRaises(ValueError):
            self.register(cell, disguised_replay=True)
        job = self.run_lab(self.register(cell))
        self.assertEqual(job["status"], "succeeded", job["error"])
        caveats = job["result"]["result"]["verdict"]["caveats"]
        self.assertTrue(any("has no decisions during" in caveat for caveat in caveats), caveats)


if __name__ == "__main__":
    unittest.main()
