"""Compact result summaries with caveats, and paging the analysis curve."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quantarium.api import Api
from quantarium.summary import _largest_session_share, summarize_analysis
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class SummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        root = Path(cls.temp.name)
        cls.api = Api(Workspace(root))
        spec = {"name": "m", "seed": 21, "start_date": "2030-01-07", "symbols": ["AAA"],
                "segments": [{"sessions": 90, "annual_drift": 0.45, "annual_volatility": 0.15},
                             {"sessions": 15, "annual_drift": -6.0, "annual_volatility": 0.4}]}
        cls.api.wait(cls.api.submit("scenario-generate", {"output": "scenarios/m", "spec": spec})["id"])
        (root / "strategies").mkdir()
        (root / "strategies" / "s.py").write_text(
            "class Hold:\n    def on_bar(self, context):\n        return 1.0\n", encoding="utf-8")
        cell = cls.api.command("cell-create", {"name": "Holder"})
        cls.api.command("cell-save", {"id": cell["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000,
            "sleeves": [{"script": "strategies/s.py", "class": "Hold"}]}})
        job = cls.api.wait(cls.api.submit("deploy-suite", {"cells": [cell["id"]],
                                                           "scenarios": ["scenarios/m"]})["id"])
        cls.config = job["result"]["config"]
        cls.run_id = cls.api.get("suite-report", {"config": cls.config})["matrix"][0]["run_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.api.close()
        cls.temp.cleanup()

    def analysis(self, **extra) -> dict:
        return self.api.get("analysis", {"db": "runs/suites.sqlite", "kind": "static",
                                         "id": self.run_id, **extra})

    def test_analysis_summary_drops_the_curve_and_states_caveats(self) -> None:
        summary = self.analysis(summary="1")
        self.assertNotIn("curve", summary)
        self.assertEqual(summary["curve_points"], 105)
        self.assertEqual(summary["costs"], {"slippage_bps": 0.0, "commission_per_order": 0.0})
        self.assertIn("No slippage or commission was modeled", summary["caveats"][0])
        cell = summary["cells"][0]
        self.assertEqual((cell["name"], cell["sessions"]), ("Holder", 105))
        self.assertEqual(cell["warnings"], ["Only 1 filled order; the result rests on very few decisions"])
        full = self.analysis()
        self.assertLess(len(str(summary)), len(str(full)) / 5)

    def test_suite_summary(self) -> None:
        summary = self.api.get("suite-report", {"config": self.config, "summary": "true"})
        self.assertNotIn("matrix", summary)
        cell = summary["cells"][0]
        self.assertEqual((cell["cell"], cell["scenarios"], cell["failed"], cell["worst_scenario"]),
                         ("Holder", 1, 0, "m"))
        self.assertTrue(any(caveat.startswith("Synthetic") for caveat in summary["caveats"]))
        self.assertTrue(any("Only one scenario" in caveat for caveat in summary["caveats"]))

    def test_curve_pages(self) -> None:
        page = self.analysis(curve_offset="100", curve_limit="3")
        self.assertEqual((len(page["curve"]), page["curve_total"], page["curve_offset"]), (3, 105, 100))
        self.assertEqual(page["curve"], self.analysis()["curve"][100:103])

    def test_short_histories_and_single_session_gains_are_flagged(self) -> None:
        curve = [{"cell": "A", "index_100": value} for value in (100, 101, 102, 130, 131)]
        self.assertAlmostEqual(_largest_session_share(curve), 28 / 31)
        analysis = {"kind": "feed", "experiment_id": "x", "status": "active", "curve": curve,
                    "cells": [{"name": "A", "status": "active", "sessions": 5, "orders_filled": 9}]}
        warnings = summarize_analysis(analysis, Path("unused.sqlite"))["cells"][0]["warnings"]
        self.assertEqual(warnings, ["Only 5 sessions; too short to judge", "One session produced 90% of the gain"])


if __name__ == "__main__":
    unittest.main()
