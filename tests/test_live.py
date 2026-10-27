"""Synchronized minute-bar simulation and durable Live Feed behavior."""

from __future__ import annotations

import json
import asyncio
import csv
import hashlib
import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from quantarium.analysis import read_analysis, write_curve_csv
from quantarium.config import load_live_config
from quantarium.live import (ConcurrentLiveRunnerError, LiveFeed,
                          parse_alpaca_minute_event, read_live_report, run_live_feed,
                          run_live_replay)
from quantarium.live_audit import audit_live_replay
from quantarium.model import CellSpec, LiveSpec, MinuteBar, StrategySpec
from quantarium.timeline import read_timeline, write_timeline_csv


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
START = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)


def minute(offset: int, symbol: str, price: float) -> MinuteBar:
    stamp = START + timedelta(minutes=offset)
    return MinuteBar(stamp, symbol, price, price + 0.5, price - 0.5, price, 1000)


class LiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        cells = (
            CellSpec("first", "simple", ("AAPL",), 1000,
                     (StrategySpec("strategies:BuyAndHold", 1, {}),)),
            CellSpec("second", "complex", ("AAPL", "MSFT"), 1000,
                     (StrategySpec("strategies:EqualWeight", 1, {}),)),
        )
        self.spec = LiveSpec(
            "minute_test", START, START + timedelta(minutes=5),
            "alpaca", "iex", str(Path(self.temp.name) / "live.sqlite"),
            0, 0, cells, history_limit=3,
        )
        self.raw = json.dumps({"test": "minute", "cells": [c.name for c in cells]})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def feed(self) -> LiveFeed:
        return LiveFeed(self.spec, self.raw, EXAMPLES)

    def advance(self, feed: LiveFeed, offset: int) -> None:
        observed = START + timedelta(minutes=offset, seconds=61)
        feed.accept(minute(offset, "AAPL", 10 + offset), observed)
        feed.accept(minute(offset, "MSFT", 20 + offset), observed)
        self.assertEqual(feed.flush(START + timedelta(minutes=offset, seconds=95)), 1)

    def test_shared_bounded_history_and_later_open_fill(self) -> None:
        feed = self.feed()
        try:
            self.assertIs(feed.runtimes["first"].history["AAPL"],
                          feed.runtimes["second"].history["AAPL"])
            for offset in range(5):
                self.advance(feed, offset)
            self.assertEqual(len(feed.history["AAPL"]), 3)
            self.assertEqual(feed.history["AAPL"][0].timestamp, START + 2 * timedelta(minutes=1))
            self.assertTrue(feed.finished)
            report = feed.report()
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["bars_cached"], 10)
            self.assertEqual(report["cells"][0]["bars_processed"], 5)
            with closing(sqlite3.connect(self.spec.database_path)) as connection:
                signal = json.loads(connection.execute(
                    "SELECT payload_json FROM live_events WHERE cell_name='first' "
                    "AND kind='signal' ORDER BY sequence LIMIT 1"
                ).fetchone()[0])
                order = connection.execute(
                    "SELECT timestamp, payload_json FROM live_events WHERE cell_name='first' "
                    "AND kind='order' ORDER BY sequence LIMIT 1"
                ).fetchone()
            self.assertEqual(signal["earliest_fill_at"], (START + 2 * timedelta(minutes=1)).isoformat())
            self.assertEqual(order[0], (START + 2 * timedelta(minutes=1)).isoformat())
            self.assertEqual(json.loads(order[1])["fill_price"], 12)
        finally:
            feed.close()

    def test_restart_restores_state_without_reloading_unbounded_history(self) -> None:
        first = self.feed()
        self.advance(first, 0)
        self.advance(first, 1)
        first.close()
        second = self.feed()
        try:
            self.assertEqual(len(second.history["AAPL"]), 2)
            self.assertEqual(second.runtimes["first"].bar_count, 2)
            for offset in range(2, 5):
                self.advance(second, offset)
            self.assertEqual(second.report()["bars_cached"], 10)
            self.assertEqual(len(second.history["AAPL"]), 3)
        finally:
            second.close()

    def test_pre_finalization_update_and_late_revision(self) -> None:
        feed = self.feed()
        try:
            observed = START + timedelta(seconds=61)
            feed.accept(minute(0, "AAPL", 10), observed)
            feed.accept(minute(0, "MSFT", 20), observed)
            feed.accept(minute(0, "AAPL", 11), START + timedelta(seconds=91))
            feed.flush(START + timedelta(seconds=95))
            self.assertEqual(feed.history["AAPL"][-1].close, 11)
            feed.accept(minute(0, "AAPL", 12), START + timedelta(minutes=3))
            self.assertEqual(feed.report()["revisions_observed"], 1)
            self.assertEqual(feed.history["AAPL"][-1].close, 11)
        finally:
            feed.close()

    def test_missing_symbol_halts_without_partial_minute(self) -> None:
        feed = self.feed()
        try:
            feed.accept(minute(0, "AAPL", 10), START + timedelta(seconds=61))
            with self.assertRaisesRegex(ValueError, "missing MSFT"):
                feed.flush(START + timedelta(seconds=95))
            self.assertEqual(feed.report()["status"], "halted")
            self.assertEqual(feed.report()["bars_cached"], 0)
        finally:
            feed.close()
        with self.assertRaisesRegex(ValueError, "halted"):
            self.feed()

    def test_stale_minute_halts_before_signaling(self) -> None:
        feed = self.feed()
        try:
            feed.accept(minute(0, "AAPL", 10), START + timedelta(seconds=61))
            feed.accept(minute(0, "MSFT", 20), START + timedelta(seconds=61))
            with self.assertRaisesRegex(ValueError, "too late"):
                feed.flush(START + timedelta(minutes=2))
            self.assertEqual(feed.report()["bars_cached"], 0)
        finally:
            feed.close()

    def test_stale_second_runner_cannot_halt_first_runner(self) -> None:
        first = self.feed()
        second = self.feed()
        try:
            self.advance(first, 0)
            with self.assertRaises(ConcurrentLiveRunnerError):
                second.flush(START + timedelta(seconds=95))
            self.assertEqual(first.report()["status"], "active")
            self.advance(first, 1)
        finally:
            second.close()
            first.close()

    def quiet_feed(self, limit: int) -> LiveFeed:
        self.spec = replace(self.spec, max_quiet_minutes=limit)
        return self.feed()

    def advance_only(self, feed: LiveFeed, offset: int, *symbols: str) -> int:
        observed = START + timedelta(minutes=offset, seconds=61)
        prices = {"AAPL": 10 + offset, "MSFT": 20 + offset}
        for symbol in symbols:
            feed.accept(minute(offset, symbol, prices[symbol]), observed)
        return feed.flush(START + timedelta(minutes=offset, seconds=95))

    def events(self, cell: str, kind: str) -> list[tuple[str, dict]]:
        with closing(sqlite3.connect(self.spec.database_path)) as connection:
            return [(stamp, json.loads(payload)) for stamp, payload in connection.execute(
                "SELECT timestamp, payload_json FROM live_events "
                "WHERE cell_name=? AND kind=? ORDER BY sequence", (cell, kind),
            )]

    def test_quiet_symbol_keeps_last_price_and_leaves_its_order_unfilled(self) -> None:
        feed = self.quiet_feed(2)
        try:
            self.advance(feed, 0)
            self.assertEqual(self.advance_only(feed, 1, "AAPL"), 1)
            self.assertEqual(self.advance_only(feed, 2, "AAPL"), 1)
            report = feed.report()
            self.assertEqual(report["status"], "active")
            self.assertEqual(report["minutes_processed"], 3)
            self.assertEqual(report["quiet_symbol_minutes"], 2)
            valuations = self.events("second", "valuation")
            self.assertNotIn("quiet_symbols", valuations[0][1])
            self.assertEqual(valuations[1][1]["quiet_symbols"], ["MSFT"])
            self.assertEqual(valuations[1][1]["prices"], {"AAPL": 11, "MSFT": 20})
            orders = {payload["symbol"]: payload for stamp, payload
                      in self.events("second", "order")
                      if stamp == (START + 2 * timedelta(minutes=1)).isoformat()}
            self.assertEqual(orders["AAPL"]["fill_price"], 12)
            self.assertEqual(orders["MSFT"]["filled_shares"], 0)
            self.assertEqual(orders["MSFT"]["unfilled_reason"], "no_trade_in_interval")
            self.assertEqual(feed.runtimes["second"].positions["MSFT"], 0)
            with self.assertRaisesRegex(ValueError, "more than 2 minutes.*MSFT"):
                self.advance_only(feed, 3, "AAPL")
            self.assertEqual(feed.report()["status"], "halted")
        finally:
            feed.close()

    def test_never_traded_symbol_is_absent_until_its_first_bar(self) -> None:
        feed = self.quiet_feed(1)
        try:
            self.assertEqual(self.advance_only(feed, 0, "AAPL"), 1)
            first = self.events("second", "valuation")[0][1]
            self.assertEqual(first["prices"], {"AAPL": 10})
            self.assertEqual(first["quiet_symbols"], ["MSFT"])
            signal = self.events("second", "signal")[0][1]
            self.assertEqual(signal["target_weights"], {"AAPL": 1.0})
            self.advance(feed, 1)
            self.advance(feed, 2)
            self.assertEqual(feed.report()["status"], "active")
        finally:
            feed.close()

    def test_order_for_unpriced_symbol_is_recorded_unfilled(self) -> None:
        strategy_root = Path(self.temp.name)
        (strategy_root / "wants_msft.py").write_text(
            "class WantsMsft:\n"
            "    def on_bar(self, context):\n"
            "        return {'MSFT': 0.5}\n",
            encoding="utf-8",
        )
        cell = CellSpec("wants", "complex", ("AAPL", "MSFT"), 1000,
                        (StrategySpec("wants_msft:WantsMsft", 1, {}),))
        self.spec = replace(self.spec, cells=(cell,), max_quiet_minutes=3)
        feed = LiveFeed(self.spec, self.raw, strategy_root)
        try:
            for offset in range(3):
                self.advance_only(feed, offset, "AAPL")
            orders = self.events("wants", "order")
            self.assertEqual(orders[0][1]["unfilled_reason"], "no_price_observed")
            self.assertIsNone(orders[0][1]["requested_shares"])
            self.assertEqual(feed.runtimes["wants"].cash, 1000)
        finally:
            feed.close()

    def test_quiet_run_survives_restart(self) -> None:
        feed = self.quiet_feed(1)
        self.advance(feed, 0)
        self.advance_only(feed, 1, "AAPL")
        feed.close()
        resumed = self.feed()
        try:
            self.assertEqual(resumed.runtimes["second"].bar_count, 2)
            with self.assertRaisesRegex(ValueError, "more than 1 minutes.*MSFT"):
                self.advance_only(resumed, 2, "AAPL")
        finally:
            resumed.close()

    def test_minute_with_no_bars_is_still_processed(self) -> None:
        feed = self.quiet_feed(1)
        try:
            self.advance(feed, 0)
            self.assertEqual(self.advance_only(feed, 1), 1)
            self.assertEqual(feed.last, START + timedelta(minutes=1))
            self.advance(feed, 2)
            self.assertEqual(feed.report()["minutes_processed"], 3)
        finally:
            feed.close()
        resumed = self.feed()
        resumed.close()

    def test_late_bar_for_quiet_minute_is_a_revision_not_a_crash(self) -> None:
        feed = self.quiet_feed(2)
        try:
            self.advance(feed, 0)
            self.advance_only(feed, 1, "AAPL")
            feed.accept(minute(1, "MSFT", 21), START + timedelta(minutes=2, seconds=50))
            self.assertEqual(feed.report()["revisions_observed"], 1)
            with closing(sqlite3.connect(self.spec.database_path)) as connection:
                original = connection.execute(
                    "SELECT original_sha256 FROM live_revisions").fetchone()[0]
            self.assertEqual(original, "")
            self.assertEqual(len(feed.history["MSFT"]), 1)
        finally:
            feed.close()

    def test_experiment_saved_before_minute_records_resumes(self) -> None:
        feed = self.feed()
        self.advance(feed, 0)
        self.advance(feed, 1)
        feed.close()
        with closing(sqlite3.connect(self.spec.database_path)) as connection:
            connection.execute("DROP TABLE live_minutes")
            connection.commit()
        resumed = self.feed()
        try:
            self.assertEqual(resumed.last, START + timedelta(minutes=1))
            self.advance(resumed, 2)
            self.assertEqual(resumed.report()["minutes_processed"], 3)
        finally:
            resumed.close()

    def test_restart_detects_deleted_bar_in_quiet_experiment(self) -> None:
        feed = self.quiet_feed(2)
        self.advance(feed, 0)
        self.advance_only(feed, 1, "AAPL")
        feed.close()
        with closing(sqlite3.connect(self.spec.database_path)) as connection:
            connection.execute("DELETE FROM live_bars WHERE symbol='MSFT'")
            connection.commit()
        with self.assertRaisesRegex(ValueError, "checkpoint and saved minute bars disagree"):
            self.feed()

    def test_strategy_failure_stops_only_its_cell_and_survives_restart(self) -> None:
        strategy_root = Path(self.temp.name)
        (strategy_root / "faulty_one.py").write_text(
            "class Crash:\n"
            "    def on_bar(self, context):\n"
            "        if context.timestamp.minute >= 31:\n"
            "            raise RuntimeError('intentional strategy failure')\n"
            "        return 1.0\n"
            "class Hold:\n"
            "    def on_bar(self, context):\n"
            "        return 1.0\n",
            encoding="utf-8",
        )
        cells = (
            CellSpec("broken", "simple", ("AAPL",), 1000,
                     (StrategySpec("faulty_one:Crash", 1, {}),)),
            CellSpec("healthy", "simple", ("AAPL",), 1000,
                     (StrategySpec("faulty_one:Hold", 1, {}),)),
        )
        spec = replace(self.spec, cells=cells, stop_at=START + timedelta(minutes=3))

        def advance_one(feed: LiveFeed, offset: int) -> None:
            observed = START + timedelta(minutes=offset, seconds=61)
            feed.accept(minute(offset, "AAPL", 10 + offset), observed)
            self.assertEqual(feed.flush(START + timedelta(minutes=offset, seconds=95)), 1)

        first = LiveFeed(spec, self.raw, strategy_root)
        try:
            advance_one(first, 0)
            self.assertEqual(first.active_cells, {"broken", "healthy"})
            self.assertEqual(first.report()["status"], "active")
        finally:
            first.close()
        resumed = LiveFeed(spec, self.raw, strategy_root)
        try:
            self.assertEqual(resumed.active_cells, {"broken", "healthy"})
            advance_one(resumed, 1)
            self.assertEqual(resumed.active_cells, {"healthy"})
            advance_one(resumed, 2)
            report = resumed.report()
            self.assertEqual(report["status"], "complete")
            by_name = {cell["name"]: cell for cell in report["cells"]}
            self.assertEqual(by_name["broken"]["status"], "failed")
            self.assertIn("intentional strategy failure", by_name["broken"]["error"])
            self.assertEqual(by_name["broken"]["bars_processed"], 1)
            self.assertEqual(by_name["healthy"]["bars_processed"], 3)
            self.assertEqual(report["bars_cached"], 3)
        finally:
            resumed.close()
        database = Path(spec.database_path)
        timeline = read_timeline(database, "live", "broken", spec.feed_id)
        self.assertIsNone(timeline["minutes"][1]["valuation"])
        self.assertEqual(timeline["minutes"][1]["other_events"][0]["kind"],
                         "cell_failed")
        export = Path(self.temp.name) / "broken.csv"
        write_timeline_csv(export, timeline)
        with export.open(encoding="utf-8", newline="") as source:
            row = list(csv.DictReader(source))[1]
        self.assertEqual(json.loads(row["other_events_json"])[0]["kind"],
                         "cell_failed")
        analysis = read_analysis(database, "live", spec.feed_id, benchmark="healthy")
        self.assertEqual(len(analysis["curve"]), 4)
        self.assertEqual({cell["name"]: cell["status"] for cell in analysis["cells"]},
                         {"broken": "failed", "healthy": "completed"})
        broken = next(cell for cell in analysis["cells"] if cell["name"] == "broken")
        self.assertIsNone(broken["excess_return_pp"])

    def test_all_live_cells_failing_halts_the_experiment(self) -> None:
        strategy_root = Path(self.temp.name)
        (strategy_root / "faulty_all.py").write_text(
            "class Crash:\n"
            "    def on_bar(self, context):\n"
            "        raise RuntimeError('intentional strategy failure')\n",
            encoding="utf-8",
        )
        cell = CellSpec("broken", "simple", ("AAPL",), 1000,
                        (StrategySpec("faulty_all:Crash", 1, {}),))
        spec = replace(self.spec, cells=(cell,))
        feed = LiveFeed(spec, self.raw, strategy_root)
        try:
            feed.accept(minute(0, "AAPL", 10), START + timedelta(seconds=61))
            self.assertEqual(feed.flush(START + timedelta(seconds=95)), 1)
            self.assertTrue(feed.finished)
            self.assertEqual(feed.report()["status"], "halted")
            self.assertEqual(feed.report()["cells"][0]["status"], "failed")
        finally:
            feed.close()

    def test_parser_rejects_invalid_volume_and_time(self) -> None:
        event = {"T": "b", "S": "AAPL", "o": 10, "h": 11, "l": 9,
                 "c": 10, "v": 100, "t": START.isoformat()}
        self.assertEqual(parse_alpaca_minute_event(event)[0].symbol, "AAPL")
        self.assertIsNone(parse_alpaca_minute_event({"T": "q"}))
        with self.assertRaisesRegex(ValueError, "whole UTC minute"):
            parse_alpaca_minute_event(event | {"t": "2024-01-02T14:30:01Z"})
        with self.assertRaisesRegex(ValueError, "invalid"):
            parse_alpaca_minute_event(event | {"v": 1.5})

    def test_report_is_read_only(self) -> None:
        feed = self.feed()
        self.advance(feed, 0)
        before = feed.report()
        feed.close()
        self.assertEqual(read_live_report(Path(self.spec.database_path), self.spec.feed_id), before)

    def test_restart_rejects_missing_saved_market_bar(self) -> None:
        feed = self.feed()
        self.advance(feed, 0)
        feed.close()
        with closing(sqlite3.connect(self.spec.database_path)) as connection:
            connection.execute("DELETE FROM live_bars WHERE symbol='MSFT'")
            connection.commit()
        with self.assertRaisesRegex(ValueError, "checkpoint and saved minute bars disagree"):
            self.feed()

    def test_config_requires_a_declared_session_and_finalization_delay(self) -> None:
        config = json.loads((EXAMPLES / "live_experiment.json").read_text(encoding="utf-8"))
        path = Path(self.temp.name) / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        parsed, _ = load_live_config(path)
        self.assertEqual(parsed.history_limit, 390)
        config["max_quiet_minutes"] = -1
        path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "max_quiet_minutes"):
            load_live_config(path)
        config["max_quiet_minutes"] = 4
        path.write_text(json.dumps(config), encoding="utf-8")
        self.assertEqual(load_live_config(path)[0].max_quiet_minutes, 4)
        del config["max_quiet_minutes"]
        config["finalization_delay_seconds"] = 60
        path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "finalization_delay_seconds"):
            load_live_config(path)
        config["finalization_delay_seconds"] = 95
        config["stop_at"] = "2026-10-21T20:00:00Z"
        path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "one declared regular New York session"):
            load_live_config(path)

    def test_analysis_and_filtered_timeline_use_minute_timestamps(self) -> None:
        feed = self.feed()
        try:
            for offset in range(5):
                self.advance(feed, offset)
        finally:
            feed.close()
        database = Path(self.spec.database_path)
        analysis = read_analysis(database, "live", self.spec.feed_id, benchmark="first")
        self.assertEqual(analysis["status"], "complete")
        self.assertEqual(len(analysis["curve"]), 10)
        self.assertEqual(analysis["cells"][0]["minutes"], 5)
        self.assertEqual(analysis["cells"][0]["excess_return_pp"], 0)
        self.assertIn("minute_return_pct", analysis["curve"][0])
        curve_path = Path(self.temp.name) / "curve.csv"
        write_curve_csv(curve_path, analysis)
        with curve_path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(rows[0]["timestamp"], START.isoformat())
        timeline = read_timeline(database, "live", "first", self.spec.feed_id,
                                 start=START + 2 * timedelta(minutes=1),
                                 through=START + 3 * timedelta(minutes=1))
        self.assertEqual(len(timeline["minutes"]), 2)
        self.assertEqual(timeline["minutes"][0]["timestamp"],
                         (START + 2 * timedelta(minutes=1)).isoformat())
        self.assertEqual(timeline["minutes"][0]["orders"][0]["side"], "buy")
        timeline_path = Path(self.temp.name) / "timeline.csv"
        write_timeline_csv(timeline_path, timeline)
        with timeline_path.open(encoding="utf-8", newline="") as source:
            timeline_rows = list(csv.DictReader(source))
        self.assertEqual(len(timeline_rows), 2)
        self.assertEqual(timeline_rows[0]["timestamp"], timeline["minutes"][0]["timestamp"])

    def test_one_socket_authenticates_and_fans_out_complete_minute(self) -> None:
        self.spec = LiveSpec(
            self.spec.feed_id, START, START + timedelta(minutes=1),
            self.spec.provider, self.spec.market_feed, self.spec.database_path,
            0, 0, self.spec.cells, history_limit=3,
        )

        class Socket:
            def __init__(self):
                self.frames = iter([
                    [{"T": "success", "msg": "connected"}],
                    [{"T": "success", "msg": "authenticated"}],
                    [{"T": "subscription", "bars": ["AAPL", "MSFT"],
                      "updatedBars": ["AAPL", "MSFT"]}],
                    [{"T": "b", "S": symbol, "t": START.isoformat(),
                      "o": price, "h": price + 0.5, "l": price - 0.5,
                      "c": price, "v": 1000}
                     for symbol, price in (("AAPL", 10), ("MSFT", 20))],
                ])
                self.sent = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def recv(self):
                return json.dumps(next(self.frames))

            async def send(self, message):
                self.sent.append(json.loads(message))

        socket = Socket()
        calls = []
        moments = iter([START - timedelta(seconds=10), START + timedelta(seconds=95)])
        capture = Path(self.temp.name) / "captured.jsonl"

        def connector(url, **options):
            calls.append((url, options))
            return socket

        with patch.dict("os.environ", {"APCA_API_KEY_ID": "key", "APCA_API_SECRET_KEY": "secret"}):
            result = asyncio.run(run_live_feed(
                self.spec, self.raw, EXAMPLES, connector=connector,
                clock=lambda: next(moments), capture_path=capture,
            ))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["bars_cached"], 2)
        self.assertEqual(calls[0][0], "wss://stream.data.alpaca.markets/v2/iex")
        self.assertEqual(socket.sent[1]["bars"], ["AAPL", "MSFT"])
        self.assertEqual(socket.sent[1]["updatedBars"], ["AAPL", "MSFT"])
        self.assertEqual(json.loads(capture.read_text(encoding="utf-8").splitlines()[0])
                         ["format"], "quantarium-live-capture-v1")
        replay_spec = replace(self.spec, feed_id="captured_replay",
                              database_path=str(Path(self.temp.name) / "replay.sqlite"))
        replayed = run_live_replay(replay_spec, self.raw, EXAMPLES, capture)
        self.assertEqual(replayed["status"], "complete")
        self.assertEqual(replayed["bars_cached"], result["bars_cached"])
        self.assertEqual([cell["equity"] for cell in replayed["cells"]],
                         [cell["equity"] for cell in result["cells"]])
        audit = audit_live_replay(Path(self.spec.database_path), self.spec.feed_id,
                                  capture, Path(replay_spec.database_path),
                                  replay_spec.feed_id)
        self.assertTrue(audit["matched"])
        self.assertEqual(audit["rows_compared"]["bars"], 2)
        with closing(sqlite3.connect(replay_spec.database_path)) as connection:
            connection.execute(
                "UPDATE live_events SET payload_json=? WHERE feed_id=? "
                "AND cell_name='first' AND kind='valuation'",
                ('{"tampered":true}', replay_spec.feed_id),
            )
            connection.commit()
        changed = audit_live_replay(Path(self.spec.database_path), self.spec.feed_id,
                                    capture, Path(replay_spec.database_path),
                                    replay_spec.feed_id)
        self.assertFalse(changed["matched"])
        self.assertEqual(changed["mismatches"][0]["section"], "events")

    def test_recorded_clock_uses_actual_tick_and_can_end_incomplete(self) -> None:
        one_minute = replace(self.spec, stop_at=START + timedelta(minutes=1))
        frames = Path(self.temp.name) / "clock.jsonl"
        header = {
            "format": "quantarium-live-capture-v1", "source_feed_id": "source",
            "start_at": one_minute.start_at.isoformat(),
            "stop_at": one_minute.stop_at.isoformat(),
            "market_feed": "iex", "symbols": ["AAPL", "MSFT"],
            "finalization_delay_seconds": 95,
        }
        bars = [{"T": "b", "S": symbol, "t": START.isoformat(),
                 "o": price, "h": price + 0.5, "l": price - 0.5,
                 "c": price, "v": 1000}
                for symbol, price in (("AAPL", 10), ("MSFT", 20))]
        frames.write_text("\n".join((
            json.dumps(header),
            json.dumps({"received_at": "2024-01-02T14:31:01Z", "items": bars}),
            json.dumps({"received_at": "2024-01-02T14:31:40Z", "items": []}),
        )), encoding="utf-8")
        report = run_live_replay(one_minute, self.raw, EXAMPLES, frames)
        self.assertEqual(report["status"], "complete")
        with closing(sqlite3.connect(one_minute.database_path)) as connection:
            signal = json.loads(connection.execute(
                "SELECT payload_json FROM live_events WHERE cell_name='first' "
                "AND kind='signal'"
            ).fetchone()[0])
        self.assertEqual(signal["available_at"],
                         (START + timedelta(seconds=100)).isoformat())

        incomplete = replace(one_minute, feed_id="incomplete_capture",
                             database_path=str(Path(self.temp.name) / "incomplete.sqlite"))
        partial = Path(self.temp.name) / "partial.jsonl"
        partial.write_text("\n".join(frames.read_text(encoding="utf-8").splitlines()[:2]),
                           encoding="utf-8")
        self.assertEqual(run_live_replay(incomplete, self.raw, EXAMPLES, partial)["status"],
                         "active")

    def test_replay_sample_uses_live_engine_and_pins_input(self) -> None:
        spec, raw = load_live_config(EXAMPLES / "live_replay_experiment.json")
        spec = replace(spec, database_path=str(Path(self.temp.name) / "replay.sqlite"))
        frames = EXAMPLES / "live_minute_frames.jsonl"
        report = run_live_replay(spec, raw, EXAMPLES, frames)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["input_kind"], "replay")
        self.assertEqual(report["input_sha256"], hashlib.sha256(frames.read_bytes()).hexdigest())
        self.assertEqual(report["bars_cached"], 8)
        self.assertEqual(report["revisions_observed"], 1)
        self.assertEqual([cell["bars_processed"] for cell in report["cells"]], [4, 4])
        self.assertGreater(report["cells"][1]["equity"], 1000)
        self.assertEqual(read_live_report(Path(spec.database_path), spec.feed_id), report)
        timeline = read_timeline(Path(spec.database_path), "live", "live_single",
                                 spec.feed_id)
        self.assertEqual(timeline["minutes"][2]["orders"][0]["fill_price"], 12)

    def test_replay_restart_rejects_changed_capture(self) -> None:
        frames = EXAMPLES / "live_minute_frames.jsonl"
        digest = hashlib.sha256(frames.read_bytes()).hexdigest()
        replay_spec = replace(self.spec, input_kind="replay", input_sha256=digest)
        feed = LiveFeed(replay_spec, self.raw, EXAMPLES)
        try:
            self.advance(feed, 0)
        finally:
            feed.close()
        altered = Path(self.temp.name) / "altered.jsonl"
        altered.write_bytes(frames.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "input changed"):
            run_live_replay(self.spec, self.raw, EXAMPLES, altered)

    def test_replay_invalidates_if_capture_changes_during_read(self) -> None:
        source = EXAMPLES / "live_minute_frames.jsonl"
        frames = Path(self.temp.name) / "changing.jsonl"
        frames.write_bytes(source.read_bytes())
        spec, raw = load_live_config(EXAMPLES / "live_replay_experiment.json")
        spec = replace(spec, database_path=str(Path(self.temp.name) / "changing.sqlite"))
        original_parser = parse_alpaca_minute_event
        changed = False

        def mutate_after_first_bar(item):
            nonlocal changed
            if not changed:
                with frames.open("a", encoding="utf-8") as target:
                    target.write("\n")
                changed = True
            return original_parser(item)

        with patch("quantarium.live.parse_alpaca_minute_event",
                   side_effect=mutate_after_first_bar):
            with self.assertRaisesRegex(ValueError, "changed while being read"):
                run_live_replay(spec, raw, EXAMPLES, frames)
        self.assertEqual(read_live_report(Path(spec.database_path), spec.feed_id)["status"],
                         "halted")

    def test_replay_rejects_out_of_order_receive_times(self) -> None:
        frames = Path(self.temp.name) / "reordered.jsonl"
        frames.write_text("\n".join((
            json.dumps({"received_at": "2024-01-02T14:31:01Z", "items": []}),
            json.dumps({"received_at": "2024-01-02T14:31:00Z", "items": []}),
        )), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "out of order on line 2"):
            run_live_replay(self.spec, self.raw, EXAMPLES, frames)

    def test_replay_missing_symbol_halts_at_due_time(self) -> None:
        frames = Path(self.temp.name) / "missing.jsonl"
        frames.write_text("\n".join((
            json.dumps({"received_at": "2024-01-02T14:31:01Z", "items": [{
                "T": "b", "S": "AAPL", "t": START.isoformat(),
                "o": 10, "h": 10.5, "l": 9.5, "c": 10, "v": 1000,
            }]}),
            json.dumps({"received_at": "2024-01-02T14:32:01Z", "items": []}),
        )), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing MSFT"):
            run_live_replay(self.spec, self.raw, EXAMPLES, frames)
        self.assertEqual(read_live_report(Path(self.spec.database_path),
                                          self.spec.feed_id)["status"], "halted")

    @unittest.skipUnless(importlib.util.find_spec("websockets") is not None,
                         "optional websockets package is not installed")
    def test_optional_client_connects_to_local_protocol_server(self) -> None:
        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve

        self.spec = LiveSpec(
            self.spec.feed_id, START, START + timedelta(minutes=1),
            self.spec.provider, self.spec.market_feed, self.spec.database_path,
            0, 0, self.spec.cells, history_limit=3,
        )
        observed = []

        async def handler(socket):
            await socket.send('[{"T":"success","msg":"connected"}]')
            observed.append(json.loads(await socket.recv()))
            await socket.send('[{"T":"success","msg":"authenticated"}]')
            observed.append(json.loads(await socket.recv()))
            await socket.send(json.dumps([{
                "T": "subscription", "bars": ["AAPL", "MSFT"],
                "updatedBars": ["AAPL", "MSFT"],
            }]))
            await socket.send(json.dumps([
                {"T": "b", "S": symbol, "t": START.isoformat(),
                 "o": price, "h": price + 0.5, "l": price - 0.5,
                 "c": price, "v": 1000}
                for symbol, price in (("AAPL", 10), ("MSFT", 20))
            ]))
            await socket.wait_closed()

        async def probe():
            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                moments = iter([START - timedelta(seconds=10),
                                START + timedelta(seconds=95)])
                connector = lambda _url, **options: connect(
                    f"ws://127.0.0.1:{port}", **options
                )
                return await run_live_feed(self.spec, self.raw, EXAMPLES,
                                           connector=connector,
                                           clock=lambda: next(moments))

        with patch.dict("os.environ", {"APCA_API_KEY_ID": "key", "APCA_API_SECRET_KEY": "secret"}):
            result = asyncio.run(probe())
        self.assertEqual(result["status"], "complete")
        self.assertEqual(observed[0]["action"], "auth")
        self.assertEqual(observed[1]["bars"], ["AAPL", "MSFT"])


if __name__ == "__main__":
    unittest.main()
