"""v0.9.2: the Lab on imported research data: survivorship, selection, and point-in-time indexes."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from quantarium.api import Api
from quantarium.userdata import import_user_data
from quantarium.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
FICTION = ROOT / "examples" / "data" / "fictional_index"
SYMBOLS = ["ALFA", "BRVO", "CHRL", "DLTA", "ECHO", "FXTR"]


class LabDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        (self.root / "strategies").mkdir()
        shutil.copy(ROOT / "examples/strategies.py", self.root / "strategies/strategies.py")
        import_user_data(FICTION, self.root / "scenarios" / "fiction", name="fiction")
        # The same prices, declared as survivors only.
        survivors = self.root / "survivors_data"
        shutil.copytree(FICTION, survivors)
        source = json.loads((survivors / "source.json").read_text(encoding="utf-8"))
        (survivors / "source.json").write_text(json.dumps({**source, "survivorship": "survivors_only"}), encoding="utf-8")
        import_user_data(survivors, self.root / "scenarios" / "survivors", name="survivors")
        self.api = Api(Workspace(self.root))

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def cell(self, name: str, **extra) -> str:
        record = self.api.command("cell-create", {"name": name})
        self.api.command("cell-save", {"id": record["id"], "spec": {
            "mode": "complex", "symbols": SYMBOLS, "initial_cash": 100_000,
            "sleeves": [{"script": "strategies/strategies.py", "class": "RecentLeader", "allocation": 1}], **extra}})
        return record["id"]

    def verdict(self, cell_id: str, scenario: str, idea: str) -> dict:
        registered = self.api.command("lab-register", {
            "idea": idea, "name": idea, "cells": [cell_id], "scenarios": [f"scenarios/{scenario}"],
            "pass_rule": {"min_mean_excess_return_pp": 0, "min_sessions": 60}})
        job = self.api.wait(self.api.submit("lab-run", {"id": registered["id"]})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])
        return job["result"]["result"]["verdict"]

    def test_an_index_cell_on_survivorship_free_data_has_neither_caveat(self) -> None:
        verdict = self.verdict(self.cell("Index leader", index="FIC5"), "fiction", "index_leader")
        text = " ".join(verdict["caveats"])
        self.assertNotIn("not declared free of survivorship bias", text)
        self.assertIn("declared free of survivorship bias; Quantarium records that declaration but cannot verify it", text)
        self.assertNotIn("fixed list", text)
        self.assertEqual(verdict["rows"][0]["survivorship"], "survivorship_free")
        self.assertEqual(verdict["scope"]["index"], "FIC5")
        self.assertEqual(verdict["rows"][0]["status"], "completed")

    def test_a_fixed_list_on_survivors_only_data_carries_both_caveats(self) -> None:
        verdict = self.verdict(self.cell("Fixed leader"), "survivors", "fixed_leader")
        text = " ".join(verdict["caveats"])
        self.assertIn("survivors is real-price data not declared free of survivorship bias", text)
        self.assertIn("fixed list of 6 symbols", text)
        self.assertEqual(verdict["rows"][0]["survivorship"], "survivors_only")

    def test_synthetic_markets_carry_no_data_caveat(self) -> None:
        shutil.copytree(ROOT / "examples/scenarios/steady_bull", self.root / "scenarios/steady_bull")
        record = self.api.command("cell-create", {"name": "Synthetic"})
        self.api.command("cell-save", {"id": record["id"], "spec": {
            "mode": "complex", "symbols": ["AAA", "BBB"], "initial_cash": 10_000,
            "sleeves": [{"script": "strategies/strategies.py", "class": "EqualWeight", "allocation": 1}]}})
        verdict = self.verdict(record["id"], "steady_bull", "synthetic")
        text = " ".join(verdict["caveats"])
        self.assertNotIn("not declared free of survivorship bias", text)
        self.assertNotIn("fixed list", text)
        self.assertIsNone(verdict["rows"][0]["survivorship"])


if __name__ == "__main__":
    unittest.main()
