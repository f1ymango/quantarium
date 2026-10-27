"""Incremental feed, provider, and checkpoint behavior."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from quantarium.config import load_feed_config
import quantarium.engine as engine_module
from quantarium.engine import CellRuntime
from quantarium.feed import read_feed_report, run_feed_batch
from quantarium.model import Bar, CellSpec, FeedSpec, StrategySpec
from quantarium.provider import AlpacaDailyProvider


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
NOW = datetime(2024, 1, 6, 12, tzinfo=timezone.utc)


def bar(day: date, symbol: str, price: float) -> Bar:
    return Bar(day, symbol, price, price + 1, price - 1, price, 1000)


class FakeProvider:
    def __init__(self, tape: dict[date, dict[str, Bar]], sessions: set[date] | None = None):
        self.tape = tape
        self.calendar = set(tape) if sessions is None else sessions
        self.calls: list[tuple[set[str], date, date]] = []

    def sessions(self, start: date, through: date) -> set[date]:
        return {day for day in self.calendar if start <= day <= through}

    def actions(self, symbols: set[str], start: date, through: date) -> list[dict]:
        return []

    def fetch(self, symbols: set[str], start: date, through: date) -> dict[date, dict[str, Bar]]:
        self.calls.append((symbols, start, through))
        return {
            day: {symbol: value for symbol, value in bars.items() if symbol in symbols}
            for day, bars in self.tape.items() if start <= day <= through
        }


class FeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        template, raw = load_feed_config(EXAMPLES / "feed_experiment.json")
        self.spec = FeedSpec(
            "feed_test", date(2024, 1, 2), template.provider, template.market_feed,
            str(Path(self.temp.name) / "feed.sqlite"), template.slippage_bps,
            template.commission_per_order, template.cells,
        )
        self.raw = raw
        self.tape = {
            date(2024, 1, 2): {"AAPL": bar(date(2024, 1, 2), "AAPL", 10),
                               "MSFT": bar(date(2024, 1, 2), "MSFT", 20)},
            date(2024, 1, 3): {"AAPL": bar(date(2024, 1, 3), "AAPL", 11),
                               "MSFT": bar(date(2024, 1, 3), "MSFT", 21)},
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_batch(self, provider: FakeProvider, through: date) -> dict:
        return run_feed_batch(self.spec, self.raw, EXAMPLES, provider, through=through, now=NOW)

    def test_restart_executes_pending_order_once_and_report_is_read_only(self) -> None:
        provider = FakeProvider(self.tape)
        first = self.run_batch(provider, date(2024, 1, 2))
        self.assertEqual(first["days_processed_this_call"], 1)
        self.assertEqual(first["bars_cached"], 2)
        second = self.run_batch(provider, date(2024, 1, 3))
        self.assertEqual(second["days_processed_this_call"], 1)
        self.assertEqual(provider.calls[-1][1], date(2024, 1, 2))
        self.assertEqual(self.run_batch(provider, date(2024, 1, 3))["days_processed_this_call"], 0)
        self.assertEqual(read_feed_report(Path(self.spec.database_path), self.spec.feed_id),
                         {key: value for key, value in second.items() if key != "days_processed_this_call"})
        with closing(sqlite3.connect(self.spec.database_path)) as connection:
            orders = connection.execute(
                "SELECT date, payload_json FROM feed_events WHERE feed_id=? AND "
                "cell_name='single_stock' AND kind='order'", (self.spec.feed_id,),
            ).fetchall()
            self.assertEqual(len(orders), 1)
            self.assertEqual(orders[0][0], "2024-01-03")
            self.assertEqual(json.loads(orders[0][1])["filled_shares"], 908)

    def test_overlapping_cells_share_restored_market_history(self) -> None:
        provider = FakeProvider(self.tape)
        self.run_batch(provider, date(2024, 1, 2))
        observed: dict[str, list[Bar]] = {}
        original = CellRuntime.restore_checkpoint

        def record_history(runtime, saved, prior_history):
            observed[runtime.cell.name] = prior_history["AAPL"]
            return original(runtime, saved, prior_history)

        with patch.object(CellRuntime, "restore_checkpoint", record_history):
            self.run_batch(provider, date(2024, 1, 3))
        self.assertIs(observed["single_stock"], observed["equal_weight"])
        self.assertEqual([bar.date for bar in observed["single_stock"]],
                         [date(2024, 1, 2), date(2024, 1, 3)])

    def test_revised_overlap_is_logged_without_rewriting_first_seen_bar(self) -> None:
        provider = FakeProvider(self.tape)
        self.run_batch(provider, date(2024, 1, 3))
        provider.tape[date(2024, 1, 3)]["AAPL"] = bar(date(2024, 1, 3), "AAPL", 12)
        result = self.run_batch(provider, date(2024, 1, 3))
        self.assertEqual(result["days_processed_this_call"], 0)
        self.assertEqual(result["revisions_observed"], 1)
        with closing(sqlite3.connect(self.spec.database_path)) as connection:
            saved = connection.execute(
                "SELECT close FROM feed_bars WHERE feed_id=? AND date='2024-01-03' AND symbol='AAPL'",
                (self.spec.feed_id,),
            ).fetchone()[0]
            self.assertEqual(saved, 11)

    def test_incomplete_day_and_future_date_are_rejected(self) -> None:
        incomplete = FakeProvider({date(2024, 1, 2): {"AAPL": self.tape[date(2024, 1, 2)]["AAPL"]}})
        with self.assertRaisesRegex(ValueError, "Incomplete feed"):
            self.run_batch(incomplete, date(2024, 1, 2))
        self.assertEqual(read_feed_report(Path(self.spec.database_path), self.spec.feed_id)["bars_cached"], 0)
        with self.assertRaisesRegex(ValueError, "only dates through"):
            self.run_batch(FakeProvider(self.tape), date(2024, 1, 6))

    def test_missing_complete_session_stops_before_any_day_is_committed(self) -> None:
        provider = FakeProvider(
            {date(2024, 1, 2): self.tape[date(2024, 1, 2)]},
            sessions={date(2024, 1, 2), date(2024, 1, 3)},
        )
        with self.assertRaisesRegex(ValueError, "Missing complete market sessions: 2024-01-03"):
            self.run_batch(provider, date(2024, 1, 3))
        report = read_feed_report(Path(self.spec.database_path), self.spec.feed_id)
        self.assertEqual(report["bars_cached"], 0)
        self.assertTrue(all(cell["last_date"] is None for cell in report["cells"]))

    def test_closed_date_is_skipped_and_non_session_bar_is_rejected(self) -> None:
        provider = FakeProvider(self.tape)
        result = self.run_batch(provider, date(2024, 1, 4))
        self.assertEqual(result["days_processed_this_call"], 2)
        self.assertEqual(result["bars_cached"], 4)
        extra = dict(self.tape)
        extra[date(2024, 1, 4)] = {
            "AAPL": bar(date(2024, 1, 4), "AAPL", 12),
            "MSFT": bar(date(2024, 1, 4), "MSFT", 22),
        }
        provider = FakeProvider(extra, sessions={date(2024, 1, 2), date(2024, 1, 3)})
        with self.assertRaisesRegex(ValueError, "non-session dates"):
            self.run_batch(provider, date(2024, 1, 4))

    def test_failed_cell_stops_alone_and_is_not_retried(self) -> None:
        self.tape[date(2024, 1, 4)] = {"AAPL": bar(date(2024, 1, 4), "AAPL", 12),
                                       "MSFT": bar(date(2024, 1, 4), "MSFT", 22)}
        provider = FakeProvider(self.tape)
        original = CellRuntime.process_day

        def fail_second_cell(runtime, day, bars, journal, next_session_available):
            if runtime.cell.name == "equal_weight" and day == date(2024, 1, 3):
                raise RuntimeError("injected failure")
            return original(runtime, day, bars, journal, next_session_available)

        with patch.object(CellRuntime, "process_day", fail_second_cell):
            report = self.run_batch(provider, date(2024, 1, 3))
        cells = {cell["name"]: cell for cell in report["cells"]}
        self.assertEqual(report["bars_cached"], 4)
        self.assertEqual(cells["single_stock"]["status"], "active")
        self.assertEqual(cells["single_stock"]["last_date"], "2024-01-03")
        self.assertEqual(cells["equal_weight"]["status"], "failed")
        self.assertEqual(cells["equal_weight"]["error"], "RuntimeError: injected failure")
        self.assertEqual(cells["equal_weight"]["last_date"], "2024-01-02")
        with closing(sqlite3.connect(self.spec.database_path)) as connection:
            kinds = [row for row in connection.execute(
                "SELECT date, kind FROM feed_events WHERE cell_name='equal_weight' "
                "AND date='2024-01-03' ORDER BY sequence"
            )]
        # The failing day's partial order and valuation were discarded.
        self.assertEqual(kinds, [("2024-01-03", "cell_failed")])
        after = self.run_batch(provider, date(2024, 1, 4))
        cells = {cell["name"]: cell for cell in after["cells"]}
        self.assertEqual(after["days_processed_this_call"], 1)
        self.assertEqual(cells["single_stock"]["last_date"], "2024-01-04")
        self.assertEqual(cells["equal_weight"]["last_date"], "2024-01-02")

    def test_config_drift_refuses_resume(self) -> None:
        provider = FakeProvider(self.tape)
        self.run_batch(provider, date(2024, 1, 2))
        changed = json.loads(self.raw)
        changed["slippage_bps"] = 6
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            run_feed_batch(self.spec, json.dumps(changed), EXAMPLES, provider,
                           through=date(2024, 1, 3), now=NOW)

    def test_strategy_source_drift_refuses_resume(self) -> None:
        provider = FakeProvider(self.tape)
        self.run_batch(provider, date(2024, 1, 2))
        load_strategy = engine_module._load_strategy

        def changed_source(*args):
            strategy, _ = load_strategy(*args)
            return strategy, "0" * 64

        with patch("quantarium.engine._load_strategy", changed_source):
            with self.assertRaisesRegex(ValueError, "Strategy source changed"):
                self.run_batch(provider, date(2024, 1, 3))

    def test_stateful_strategy_resumes_from_snapshot(self) -> None:
        cell = CellSpec("alternating", "simple", ("AAPL",), 10000,
                        (StrategySpec("strategies:AlternatingDays", 1, {}),))
        self.spec = FeedSpec(
            self.spec.feed_id, self.spec.start_date, self.spec.provider,
            self.spec.market_feed, self.spec.database_path, 0, 0, (cell,),
        )
        provider = FakeProvider(self.tape)
        self.run_batch(provider, date(2024, 1, 2))
        self.run_batch(provider, date(2024, 1, 3))
        with closing(sqlite3.connect(self.spec.database_path)) as connection:
            state = json.loads(connection.execute(
                "SELECT state_json FROM feed_cells WHERE feed_id=? AND name='alternating'",
                (self.spec.feed_id,),
            ).fetchone()[0])
        self.assertEqual(state["strategy_states"], [2])
        self.assertEqual(state["pending"], {"AAPL": 0.0})

    def test_alpaca_pagination_and_new_york_daily_date(self) -> None:
        calls = []

        def get_json(url, headers):
            query = parse_qs(urlparse(url).query)
            calls.append(query)
            self.assertEqual(query["end"], ["2024-01-04"])
            self.assertEqual(headers["APCA-API-KEY-ID"], "test-key")
            if "page_token" not in query:
                return {"bars": {"AAPL": [{"t": "2024-01-02T05:00:00Z", "o": 10,
                                            "h": 11, "l": 9, "c": 10, "v": 1000}]},
                        "next_page_token": "next"}
            return {"bars": {"MSFT": [{"t": "2024-01-03T05:00:00Z", "o": 20,
                                        "h": 21, "l": 19, "c": 20, "v": 1000}]},
                    "next_page_token": None}

        provider = AlpacaDailyProvider("test-key", "test-secret", feed="iex", get_json=get_json)
        got = provider.fetch({"AAPL", "MSFT"}, date(2024, 1, 2), date(2024, 1, 3))
        self.assertEqual(set(got), {date(2024, 1, 2), date(2024, 1, 3)})
        self.assertEqual(len(calls), 2)

    def test_alpaca_calendar_uses_paper_endpoint_and_rejects_duplicates(self) -> None:
        calls = []

        def get_json(url, headers):
            calls.append((url, headers))
            return [{"date": "2024-01-02", "open": "09:30", "close": "16:00"},
                    {"date": "2024-01-03", "open": "09:30", "close": "13:00"}]

        provider = AlpacaDailyProvider("test-key", "test-secret", get_json=get_json)
        sessions = provider.sessions(date(2024, 1, 2), date(2024, 1, 3))
        self.assertEqual(sessions, {date(2024, 1, 2), date(2024, 1, 3)})
        self.assertEqual(urlparse(calls[0][0]).netloc, "paper-api.alpaca.markets")
        self.assertEqual(parse_qs(urlparse(calls[0][0]).query)["date_type"], ["TRADING"])
        self.assertEqual(calls[0][1]["APCA-API-KEY-ID"], "test-key")
        duplicate = AlpacaDailyProvider(
            "test-key", "test-secret",
            get_json=lambda _url, _headers: [{"date": "2024-01-02"}, {"date": "2024-01-02"}],
        )
        with self.assertRaisesRegex(ValueError, "duplicate session"):
            duplicate.sessions(date(2024, 1, 2), date(2024, 1, 3))
        with self.assertRaisesRegex(ValueError, "calendar coverage"):
            provider.sessions(date(2030, 1, 2), date(2030, 1, 3))


if __name__ == "__main__":
    unittest.main()
