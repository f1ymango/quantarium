"""The engine cross-check: the comparison, and, when a reference environment is set up, the cases (v0.8.0)."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from quantarium.legacy import env

ROOT = Path(__file__).resolve().parents[1]
# The driver is a tool, not part of the quantarium package; load it under its own name.
_SPEC = importlib.util.spec_from_file_location("quantarium_crosscheck", ROOT / "tools" / "crosscheck" / "run.py")
crosscheck = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(crosscheck)

REFERENCE = env("REFERENCE_PYTHON")  # QUANTARIUM_REFERENCE_PYTHON, or the older BARLOOM_ name


def result(equity, fills) -> dict:
    return {"equity": [list(item) for item in equity], "fills": [list(item) for item in fills]}


class CompareTests(unittest.TestCase):
    EQUITY = [("2030-01-07", 10_000.0), ("2030-01-08", 10_050.0)]
    FILLS = [("2030-01-08", "AAA", "buy", 100, 50.0)]

    def test_the_same_run_matches(self) -> None:
        verdict = crosscheck.compare(result(self.EQUITY, self.FILLS), result(self.EQUITY, self.FILLS))
        self.assertTrue(verdict["match"])

    def test_differences_are_named(self) -> None:
        cases = {
            "fills only in Quantarium": result(self.EQUITY, []),
            "fill price": result(self.EQUITY, [("2030-01-08", "AAA", "buy", 100, 50.01)]),
            "equity": result([("2030-01-07", 10_000.0), ("2030-01-08", 10_051.0)], self.FILLS),
            "different number of days": result(self.EQUITY[:1], self.FILLS),
        }
        for kind, theirs in cases.items():
            with self.subTest(kind):
                verdict = crosscheck.compare(result(self.EQUITY, self.FILLS), theirs)
                self.assertFalse(verdict["match"])
                self.assertIn(kind, [item["kind"] for item in verdict["differences"]])

    def test_rounding_is_not_a_difference(self) -> None:
        nudged = [(day, value * (1 + 1e-12)) for day, value in self.EQUITY]
        self.assertTrue(crosscheck.compare(result(self.EQUITY, self.FILLS), result(nudged, self.FILLS))["match"])

    def test_the_cases_cover_every_example_strategy_and_scenario(self) -> None:
        cases = crosscheck.cases()
        self.assertEqual(len(cases), 68)
        self.assertEqual({case["strategy"] for case in cases}, {"BuyAndHold", "TrendFollower", "EqualWeight",
                                                                 "RecentLeader", "StopGuard", "DipBuyer"})
        self.assertTrue(any(case.get("volume_cap") for case in cases))
        self.assertTrue(any(case.get("initial_positions") for case in cases))
        self.assertTrue(any(case.get("rebalance_every", 1) > 1 for case in cases))

    def test_quantarium_side_runs_without_the_reference(self) -> None:
        for name in ("steady_bull TrendFollower with costs", "bull_with_crash StopGuard",
                     "steady_bull EqualWeight starting holdings"):
            case = next(item for item in crosscheck.cases() if item["name"] == name)
            with self.subTest(name), tempfile.TemporaryDirectory(dir=ROOT / "runs" / "test-temp") as temp:
                ours = crosscheck.run_quantarium(case, Path(temp))
            self.assertEqual(len(ours["equity"]), 252)
            self.assertTrue(ours["fills"])


@unittest.skipUnless(REFERENCE, "Set QUANTARIUM_REFERENCE_PYTHON to a Python with backtrader 1.9.78.123 to run the cross-check")
class ReferenceTests(unittest.TestCase):
    def test_quantarium_matches_backtrader(self) -> None:
        for name in ("bull_with_crash EqualWeight with costs", "sideways_chop RecentLeader no costs",
                     "recession_bear StopGuard", "sideways_chop DipBuyer", "steady_bull RecentLeader volume cap",
                     "bull_with_crash EqualWeight starting holdings", "steady_bull EqualWeight every 5 sessions"):
            case = next(item for item in crosscheck.cases() if item["name"] == name)
            with self.subTest(name), tempfile.TemporaryDirectory(dir=ROOT / "runs" / "test-temp") as temp:
                folder = Path(temp)
                verdict = crosscheck.compare(crosscheck.run_quantarium(case, folder),
                                             crosscheck.run_reference(case, folder, REFERENCE))
                self.assertTrue(verdict["match"], verdict["differences"])


if __name__ == "__main__":
    unittest.main()
