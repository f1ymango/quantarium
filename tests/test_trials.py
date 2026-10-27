"""Trial-aware statistics: probabilistic and deflated Sharpe ratios, and overfitting (v0.7.0)."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from quantarium.api import Api
from quantarium.lab import Lab, _cell_names
from quantarium.trials import (deflated_sharpe, expected_maximum_sharpe, moments, overfitting_probability,
                            probabilistic_sharpe)
from quantarium.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


def noise(seed: int, n: int = 400, drift: float = 0.0) -> list[float]:
    generator = random.Random(seed)
    return [drift + generator.gauss(0, 0.01) for _ in range(n)]


class StatisticsTests(unittest.TestCase):
    def test_known_values(self) -> None:
        # The best of 100 attempts without skill: about 2.53 standard deviations, as the literature gives.
        self.assertAlmostEqual(expected_maximum_sharpe(100, 1.0), 2.53, places=2)
        self.assertEqual(expected_maximum_sharpe(1, 1.0), 0.0)
        # A Sharpe ratio of zero is as likely above zero as below.
        self.assertAlmostEqual(probabilistic_sharpe(0.0, 252, 0.0, 3.0), 0.5)
        # 0.1 a session over 252 normal sessions: z = 0.1 * sqrt(251) / sqrt(1 + 0.005).
        self.assertAlmostEqual(probabilistic_sharpe(0.1, 252, 0.0, 3.0), 0.9430, places=3)
        stats = moments([0.01, -0.02, 0.03, 0.0, 0.01])
        self.assertAlmostEqual(stats["mean"], 0.006)

    def test_more_attempts_make_the_bar_higher(self) -> None:
        returns = noise(1, drift=0.001)
        one = deflated_sharpe(returns, 1, [])
        many = deflated_sharpe(returns, 50, [])
        self.assertAlmostEqual(one["dsr"], one["psr"])
        self.assertLess(many["dsr"], one["dsr"])
        self.assertEqual((one["variance_source"], many["trials"]), ("sampling error", 50))
        self.assertIsNone(deflated_sharpe([0.0] * 30, 1, []))

    def test_overfitting_is_detected_among_lucky_attempts_and_not_with_a_real_edge(self) -> None:
        days = [f"2030-{1 + index // 28:02d}-{1 + index % 28:02d}" for index in range(320)]
        lucky = {f"attempt {seed}": list(zip(days, noise(seed, 320))) for seed in range(8)}
        self.assertGreaterEqual(overfitting_probability(lucky)["pbo"], 0.3)
        real = dict(lucky)
        real["edge"] = list(zip(days, noise(99, 320, drift=0.004)))
        self.assertLess(overfitting_probability(real)["pbo"], 0.1)
        self.assertIsNone(overfitting_probability(dict(list(lucky.items())[:3]))["pbo"])


class GateTests(unittest.TestCase):
    """A candidate that beats its controls is only supported when its deflated Sharpe ratio is high enough."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.api = Api(Workspace(Path(self.temp.name)))
        self.lab: Lab = self.api.lab

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def verdict(self, dsr: float) -> dict:
        candidate = {"name": "Candidate", "symbols": ["AAA"], "strategies": []}
        names = _cell_names(candidate)
        spec = {"candidate": candidate, "slippage_bps": 0, "commission_per_order": 0, "idea": "gate", "pass_rule": {"min_mean_excess_return_pp": 0, "min_sessions": 20,
                                                     "max_drawdown_pct": None},
                "scenarios": [{"name": "s", "kind": "synthetic", "lineage": "synthetic", "family": "f",
                               "start_date": "2030-01-07", "end_date": "2030-12-31", "prices_sha256": "0" * 64}]}
        row = {"scenario": "s", "total_return_pct": 20.0, "max_drawdown_pct": 5.0, "sessions": 250,
               "status": "completed"}
        matrix = [{**row, "cell": "Candidate"}] + [{**row, "cell": name, "total_return_pct": 10.0}
                                                   for name in names["controls"].values()]
        statistics = {"rows": [{"scenario": "s", "dsr": dsr, "psr": 0.99}], "attempts": 12,
                      "pbo": {"pbo": None}, "dsr_threshold": 0.95}
        return self.lab._verdict(spec, {"matrix": matrix, "caveats": []}, [], statistics)

    def test_the_gate(self) -> None:
        self.assertEqual(self.verdict(0.99)["status"], "supported_within_tested_scope")
        weak = self.verdict(0.6)
        self.assertEqual(weak["status"], "inconclusive")
        self.assertTrue(any("deflated Sharpe ratio" in caveat for caveat in weak["caveats"]))


class LabStatisticsTests(unittest.TestCase):
    def test_a_lab_run_reports_statistics_and_counts_attempts(self) -> None:
        import shutil

        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            root = Path(temp)
            (root / "strategies").mkdir()
            shutil.copy(ROOT / "examples/strategies.py", root / "strategies/strategies.py")
            shutil.copytree(ROOT / "examples/scenarios/steady_bull", root / "scenarios/steady_bull")
            api = Api(Workspace(root))
            try:
                record = api.command("cell-create", {"name": "Trend"})
                api.command("cell-save", {"id": record["id"], "spec": {
                    "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000,
                    "sleeves": [{"script": "strategies/strategies.py", "class": "TrendFollower",
                                 "parameters": {"window": 10}}]}})
                params = {"idea": "trend", "name": "Trend", "cells": [record["id"]],
                          "scenarios": ["scenarios/steady_bull"],
                          "pass_rule": {"min_mean_excess_return_pp": 0, "min_sessions": 20}}
                results = []
                for _ in range(2):
                    experiment = api.command("lab-register", params)
                    job = api.wait(api.submit("lab-run", {"id": experiment["id"]})["id"])
                    self.assertEqual(job["status"], "succeeded", job["error"])
                    results.append(job["result"]["result"]["verdict"]["statistics"])
            finally:
                api.close()
        first, second = results
        self.assertEqual((first["attempts"], second["attempts"]), (1, 2))
        row = first["rows"][0]
        self.assertGreater(row["sessions"], 100)
        self.assertTrue(0 <= row["psr"] <= 1 and 0 <= row["dsr"] <= 1)
        self.assertEqual(second["rows"][0]["variance_source"], "attempts")
        self.assertIsNone(second["pbo"]["pbo"])


if __name__ == "__main__":
    unittest.main()
