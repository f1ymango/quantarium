"""Declared information boundaries stay visible and pinned across daily runs."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from quantarium.config import _cells
from quantarium.engine import run_static
from quantarium.feed import _cell_sha256
from quantarium.journal import read_report
from quantarium.model import CellSpec, RunSpec, StrategySpec
from quantarium.timeline import read_timeline


class InformationBoundaryTests(unittest.TestCase):
    def test_invalid_boundary_is_rejected(self) -> None:
        cell = {"name": "c", "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
                "strategies": [{"entrypoint": "strategy:Hold"}], "information_end": "yesterday"}
        with self.assertRaisesRegex(ValueError, "information_end must be an ISO date"):
            _cells({"cells": [cell]})

    def test_static_run_marks_only_sessions_after_the_declared_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "strategy.py").write_text(
                "class Hold:\n    def on_bar(self, context):\n        return 0.0\n", encoding="utf-8")
            (root / "prices.csv").write_text(
                "date,symbol,open,high,low,close,volume\n"
                "2024-01-02,AAA,100,100,100,100,1000\n"
                "2024-01-03,AAA,101,101,101,101,1000\n"
                "2024-01-04,AAA,102,102,102,102,1000\n", encoding="utf-8")
            boundary = date(2024, 1, 3)
            cell = CellSpec("c", "simple", ("AAA",), 1000,
                            (StrategySpec("strategy:Hold", 1, {}),), information_end=boundary)
            database = root / "run.sqlite"
            run_id = run_static(RunSpec(str(root / "prices.csv"), str(database), 0, 0, (cell,)),
                                "{}", root)
            report = read_report(database, run_id)
            self.assertEqual(report["cells"][0]["information_end"], boundary.isoformat())
            self.assertEqual(report["cells"][0]["information_end_source"], "cell_config_unverified")
            timeline = read_timeline(database, "static", "c", run_id)
            valuations = [day["valuation"] for day in timeline["days"]]
            self.assertEqual([valuation["post_information_end"] for valuation in valuations],
                             [False, False, True])

    def test_feed_pins_new_holdings_and_information_boundary(self) -> None:
        original = CellSpec("c", "simple", ("AAA",), 1000,
                            (StrategySpec("strategy:Hold", 1, {}),))
        with_boundary = CellSpec("c", "simple", ("AAA",), 1000, original.strategies,
                                 information_end=date(2024, 1, 3))
        with_holdings = CellSpec("c", "simple", ("AAA",), 1000, original.strategies,
                                 initial_positions={"AAA": (2, 80.0)})
        self.assertNotEqual(_cell_sha256(original), _cell_sha256(with_boundary))
        self.assertNotEqual(_cell_sha256(original), _cell_sha256(with_holdings))


if __name__ == "__main__":
    unittest.main()
