"""Pre-registration, controls, used data, and the Lab ledger."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from quantarium.api import Api
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
FULLY_INVESTED = "class Candidate:\n    def on_bar(self, context):\n        return 1.0\n"
BROKEN = "class Candidate:\n    def on_bar(self, context):\n        raise RuntimeError('broken on purpose')\n"


class LabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        (self.root / "strategies").mkdir()
        shutil.copy(ROOT / "examples/strategies.py", self.root / "strategies/strategies.py")
        shutil.copytree(ROOT / "examples/scenarios/steady_bull", self.root / "scenarios/steady_bull")
        self.api = Api(Workspace(self.root))
        record = self.api.command("cell-create", {"name": "Candidate"})
        self.cell = self.api.command("cell-save", {"id": record["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000,
            "sleeves": [{"script": "strategies/strategies.py", "class": "TrendFollower",
                         "allocation": 1, "parameters": {"window": 10}}],
        }})

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def params(self, idea: str = "trend") -> dict:
        return {"idea": idea, "name": "Trend vs simple controls", "cells": [self.cell["id"]],
                "scenarios": ["scenarios/steady_bull"],
                "pass_rule": {"min_mean_excess_return_pp": 0, "min_sessions": 60}}

    def run_lab(self, experiment_id: str) -> dict:
        job = self.api.wait(self.api.submit("lab-run", {"id": experiment_id})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])
        return job["result"]

    def test_registration_freezes_inputs_and_runs_two_controls(self) -> None:
        registered = self.api.command("lab-register", self.params())
        self.assertEqual(registered["status"], "registered")
        self.assertEqual(registered["attempt_number"], 1)
        self.assertIsNone(registered["result"])
        result = self.run_lab(registered["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["result"]["report"]["matrix"]), 3)
        self.assertEqual(len(result["result"]["verdict"]["rows"]), 1)
        self.assertIn(result["result"]["verdict"]["status"],
                      {"supported_within_tested_scope", "not_supported"})
        self.assertEqual(self.api.get("lab/" + registered["id"], {})["id"], registered["id"])
        self.assertEqual(len(self.api.get("lab", {})), 1)

    def test_zero_extra_costs_leave_the_snapshot_as_before_and_set_costs_are_pinned_and_charged(self) -> None:
        zero = {**self.params(), "commission_per_share": 0, "commission_minimum": 0, "slippage_range_fraction": 0}
        self.assertEqual(self.api.lab.validate(zero)["fingerprint"], self.api.lab.validate(self.params())["fingerprint"])
        self.assertNotIn("costs", self.api.lab._snapshot(zero))
        plain = self.run_lab(self.api.command("lab-register", self.params("plain"))["id"])
        costly = {**self.params("costly"), "commission_minimum": 25, "slippage_range_fraction": 0.5}
        self.assertEqual(self.api.lab._snapshot(costly)["costs"],
                         {"commission_minimum": 25.0, "slippage_range_fraction": 0.5})
        charged = self.run_lab(self.api.command("lab-register", costly)["id"])
        verdict = charged["result"]["verdict"]
        self.assertEqual(verdict["scope"]["costs"], {"commission_minimum": 25.0, "slippage_range_fraction": 0.5})
        self.assertLess(verdict["rows"][0]["candidate_return_pct"],
                        plain["result"]["verdict"]["rows"][0]["candidate_return_pct"])

    def test_reusing_data_for_one_idea_is_exploratory(self) -> None:
        first = self.api.command("lab-register", self.params())
        second = self.api.command("lab-register", self.params())
        self.run_lab(first["id"])
        later = self.run_lab(second["id"])
        self.assertEqual(later["attempt_number"], 2)
        self.assertEqual(later["result"]["verdict"]["status"], "unknown")
        self.assertTrue(later["result"]["verdict"]["used_data"])
        self.assertTrue(later["reused_families"])

    def test_changed_script_makes_registration_stale_without_consuming_data(self) -> None:
        registered = self.api.command("lab-register", self.params())
        script = self.root / "strategies/strategies.py"
        script.write_text(script.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        job = self.api.wait(self.api.submit("lab-run", {"id": registered["id"]})["id"])
        self.assertEqual(job["status"], "failed")
        stored = self.api.get("lab/" + registered["id"], {})
        self.assertEqual(stored["status"], "stale")
        self.assertFalse(stored["consumed"])

    def test_changed_scenario_makes_registration_stale_without_consuming_data(self) -> None:
        registered = self.api.command("lab-register", self.params())
        prices = self.root / "scenarios/steady_bull/prices.csv"
        prices.write_text(prices.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        job = self.api.wait(self.api.submit("lab-run", {"id": registered["id"]})["id"])
        self.assertEqual(job["status"], "failed")
        stored = self.api.get("lab/" + registered["id"], {})
        self.assertEqual(stored["status"], "stale")
        self.assertFalse(stored["consumed"])

    def test_deleted_candidate_makes_registration_stale(self) -> None:
        registered = self.api.command("lab-register", self.params())
        self.api.command("cell-delete", {"ids": [self.cell["id"]]})
        job = self.api.wait(self.api.submit("lab-run", {"id": registered["id"]})["id"])
        self.assertEqual(job["status"], "failed")
        stored = self.api.get("lab/" + registered["id"], {})
        self.assertEqual(stored["status"], "stale")
        self.assertFalse(stored["consumed"])

    def test_unverified_information_boundary_cannot_earn_a_verdict(self) -> None:
        spec = dict(self.cell["spec"], information_end="2020-01-01")
        self.api.command("cell-save", {"id": self.cell["id"], "spec": spec})
        registered = self.api.command("lab-register", self.params())
        result = self.run_lab(registered["id"])
        verdict = result["result"]["verdict"]
        self.assertEqual(verdict["status"], "unknown")
        self.assertEqual(verdict["scope"]["information_boundary_status"], "self_declared_unverified")

    def test_holdout_cannot_be_registered_in_lab(self) -> None:
        self.api.command("scenario-holdout", {"key": "scenarios/steady_bull", "holdout": True})
        with self.assertRaisesRegex(ValueError, "Holdout scenarios"):
            self.api.command("lab-register", self.params())


    def use_script(self, source: str) -> None:
        (self.root / "strategies" / "candidate.py").write_text(source, encoding="utf-8")
        spec = dict(self.cell["spec"], sleeves=[{"script": "strategies/candidate.py", "class": "Candidate",
                                                 "allocation": 1, "parameters": {}}])
        self.cell = self.api.command("cell-save", {"id": self.cell["id"], "spec": spec})

    def test_a_copy_of_a_control_is_not_supported(self) -> None:
        # Fully invested every day is exactly what buy-and-hold does on one symbol.
        self.use_script(FULLY_INVESTED)
        verdict = self.run_lab(self.api.command("lab-register", self.params())["id"])["result"]["verdict"]
        self.assertEqual(verdict["rows"][0]["excess_return_pp"], 0)
        self.assertEqual(verdict["status"], "not_supported")
        self.assertTrue(any("matched its best control" in caveat for caveat in verdict["caveats"]))

    def test_a_cell_that_asks_a_model_is_judged_only_on_synthetic_markets(self) -> None:
        def verdict(kind: str) -> dict:
            spec = {"candidate": {"name": "Asker", "symbols": ["AAA"], "ask": {"model": "m"}},
                    "scenarios": [{"name": "s", "kind": kind, "start_date": "2030-01-07",
                                   "end_date": "2030-06-28", "family": "f"}],
                    "pass_rule": {"min_mean_excess_return_pp": 0, "max_drawdown_pct": None, "min_sessions": 20},
                    "slippage_bps": 0, "commission_per_order": 0}
            row = {"scenario": "s", "total_return_pct": 12.0, "max_drawdown_pct": 5.0, "sessions": 120,
                   "status": "completed"}
            report = {"caveats": [], "matrix": [{**row, "cell": "Asker"},
                                                {**row, "cell": "lab_buy_hold", "total_return_pct": 5.0},
                                                {**row, "cell": "lab_equal_weight", "total_return_pct": 4.0}]}
            return self.api.lab._verdict(spec, report, [])

        for kind in ("historical", "transformed"):
            with self.subTest(kind=kind):
                result = verdict(kind)
                self.assertEqual(result["status"], "unknown")
                self.assertTrue(any("may remember" in caveat for caveat in result["caveats"]))
        self.assertEqual(verdict("synthetic")["status"], "supported_within_tested_scope")

    def test_a_failed_candidate_says_why(self) -> None:
        self.use_script(BROKEN)
        verdict = self.run_lab(self.api.command("lab-register", self.params())["id"])["result"]["verdict"]
        self.assertEqual(verdict["status"], "unknown")
        self.assertIn("broken on purpose", verdict["rows"][0]["error"])
        self.assertTrue(any("broken on purpose" in caveat for caveat in verdict["caveats"]))


if __name__ == "__main__":
    unittest.main()
