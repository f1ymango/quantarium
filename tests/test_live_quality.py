"""Live capture diagnostics distinguish assessed gaps from future minutes."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from quantarium.config import load_live_config
from quantarium.live_quality import inspect_live_capture


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class LiveQualityTests(unittest.TestCase):
    def test_sample_reports_complete_coverage_and_two_update_timings(self) -> None:
        spec, _ = load_live_config(EXAMPLES / "live_replay_experiment.json")
        report = inspect_live_capture(spec, EXAMPLES / "live_minute_frames.jsonl")
        self.assertEqual(report["clock"], "ideal_deadlines")
        self.assertEqual(report["expected_symbol_minutes"], 8)
        self.assertEqual(report["on_time_symbol_minutes"], 8)
        self.assertEqual(report["missing_symbol_minutes"], 0)
        self.assertEqual(report["by_symbol"]["AAPL"]["updates_before_finalization"], 1)
        self.assertEqual(report["by_symbol"]["AAPL"]["updates_after_finalization"], 1)

    def test_partial_recorded_capture_assesses_only_reached_deadline(self) -> None:
        spec, _ = load_live_config(EXAMPLES / "live_replay_experiment.json")
        spec = replace(spec, stop_at=spec.start_at + 2 * timedelta(minutes=1))
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            frames = Path(directory) / "partial.jsonl"
            header = {
                "format": "quantarium-live-capture-v1", "source_feed_id": "source",
                "start_at": spec.start_at.isoformat(),
                "stop_at": spec.stop_at.isoformat(),
                "market_feed": spec.market_feed,
                "symbols": ["AAPL", "MSFT"],
                "finalization_delay_seconds": spec.finalization_delay_seconds,
            }
            aapl = {"T": "b", "S": "AAPL", "t": spec.start_at.isoformat(),
                    "o": 10, "h": 10.5, "l": 9.5, "c": 10, "v": 1000}
            msft = {"T": "u", "S": "MSFT", "t": spec.start_at.isoformat(),
                    "o": 20, "h": 20.5, "l": 19.5, "c": 20, "v": 1000}
            lines = [
                header,
                {"received_at": "2024-01-02T14:31:01Z", "items": [aapl]},
                {"received_at": "2024-01-02T14:31:40Z", "items": []},
                {"received_at": "2024-01-02T14:31:50Z", "items": [msft]},
            ]
            frames.write_text("\n".join(json.dumps(line) for line in lines),
                              encoding="utf-8")
            report = inspect_live_capture(spec, frames)
        self.assertEqual(report["clock"], "recorded")
        self.assertEqual(report["assessed_minutes"], 1)
        self.assertEqual(report["by_symbol"]["MSFT"]["longest_quiet_run"], 1)
        self.assertEqual(report["by_symbol"]["AAPL"]["longest_quiet_run"], 0)
        self.assertEqual(report["longest_quiet_run_minutes"], 1)
        self.assertFalse(report["window_complete"])
        self.assertEqual(report["missing_symbol_minutes"], 1)
        self.assertEqual(report["late_only_symbol_minutes"], 1)
        self.assertEqual(report["issues"][0]["symbol"], "MSFT")

    def test_recorded_frame_is_accepted_before_its_clock_flush(self) -> None:
        spec, _ = load_live_config(EXAMPLES / "live_replay_experiment.json")
        spec = replace(spec, stop_at=spec.start_at + timedelta(minutes=1))
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            frames = Path(directory) / "late_tick.jsonl"
            header = {
                "format": "quantarium-live-capture-v1", "source_feed_id": "source",
                "start_at": spec.start_at.isoformat(),
                "stop_at": spec.stop_at.isoformat(),
                "market_feed": spec.market_feed,
                "symbols": ["AAPL", "MSFT"],
                "finalization_delay_seconds": spec.finalization_delay_seconds,
            }
            bars = [{"T": "b", "S": symbol, "t": spec.start_at.isoformat(),
                     "o": price, "h": price + 0.5, "l": price - 0.5,
                     "c": price, "v": 1000}
                    for symbol, price in (("AAPL", 10), ("MSFT", 20))]
            frames.write_text("\n".join(json.dumps(line) for line in (
                header,
                {"received_at": "2024-01-02T14:31:38Z", "items": bars},
                {"received_at": "2024-01-02T14:31:40Z", "items": []},
            )), encoding="utf-8")
            report = inspect_live_capture(spec, frames)
        self.assertEqual(report["on_time_symbol_minutes"], 2)
        self.assertEqual(report["missing_symbol_minutes"], 0)


if __name__ == "__main__":
    unittest.main()
