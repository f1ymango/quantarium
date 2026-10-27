"""Offline results dashboard generation and safe data embedding."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from quantarium.dashboard import _sample_curve, write_dashboard


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class DashboardTests(unittest.TestCase):
    def test_sampling_keeps_endpoints_and_extrema(self) -> None:
        points = [{"index_100": 100 + index / 100, "date": str(index)}
                  for index in range(1200)]
        points[613]["index_100"] = 45
        points[614]["index_100"] = 185
        sampled = _sample_curve(points)
        self.assertLessEqual(len(sampled), 500)
        self.assertIs(sampled[0], points[0])
        self.assertIs(sampled[-1], points[-1])
        self.assertIn(points[613], sampled)
        self.assertIn(points[614], sampled)

    def test_snapshot_is_self_contained_and_escapes_embedded_data(self) -> None:
        hostile_name = 'demo__QUANTARIUM_JS____QUANTARIUM_DATA__</script><script>alert("x")</script>'
        analysis = {
            "kind": "live", "experiment_id": hostile_name,
            "status": "complete", "benchmark": None,
            "cells": [{
                "name": "cell", "mode": "simple", "initial_cash": 1000,
                "minutes": 1, "last_timestamp": "2024-01-02T14:30:00+00:00",
                "total_return_pct": 0, "max_drawdown_pct": 0,
                "orders_filled": 0, "traded_notional": 0,
                "status": "active", "error": None,
            }],
            "curve": [{"cell": "cell", "timestamp": "2024-01-02T14:30:00+00:00",
                       "equity": 1000, "index_100": 100,
                       "minute_return_pct": 0, "drawdown_pct": 0}],
        }
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            output = Path(directory) / "dashboard.html"
            result = write_dashboard(output, analysis)
            html = output.read_text(encoding="utf-8")
        self.assertEqual(result["cells"], 1)
        self.assertEqual(result["chart_rows"], 1)
        self.assertIn("<svg", html)
        self.assertIn("function renderChart", html)
        self.assertNotIn(hostile_name, html)
        self.assertNotIn("https://", html)
        embedded = re.search(
            r'<script id="quantarium-data" type="application/json">(.*?)</script>',
            html, re.DOTALL,
        )
        self.assertIsNotNone(embedded)
        self.assertEqual(json.loads(embedded.group(1))["experiment_id"], hostile_name)


if __name__ == "__main__":
    unittest.main()
