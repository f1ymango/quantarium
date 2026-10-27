"""Rebalance cadence and portfolio target constraints."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from quantarium.audit import audit_replay
from quantarium.config import load_config
from quantarium.engine import run_static
from quantarium.feed import run_feed_batch
from quantarium.journal import read_report
from quantarium.model import Bar, CellSpec, FeedSpec, RunSpec, StrategySpec


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
DAYS = [date(2024, 1, day) for day in (2, 3, 4, 5)]


def _cell(interval: int = 2) -> CellSpec:
    return CellSpec("paced", "simple", ("AAA",), 1000,
                    (StrategySpec("strategies:AlternatingDays", 1, {}),), interval)


class PolicyTests(unittest.TestCase):
    def test_static_interval_skips_strategy_and_delays_sell(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            folder = Path(temp)
            prices = folder / "prices.csv"
            prices.write_text(
                "date,symbol,open,high,low,close,volume\n" +
                "".join(f"{day},AAA,100,100,100,100,1000\n" for day in DAYS),
                encoding="utf-8",
            )
            database = folder / "static.sqlite"
            spec = RunSpec(str(prices), str(database), 0, 0, (_cell(),))
            run_id = run_static(spec, "{}", EXAMPLES)
            with closing(sqlite3.connect(database)) as connection:
                events = connection.execute(
                    "SELECT date, kind, payload_json FROM events WHERE run_id=? "
                    "AND cell_name='paced' ORDER BY sequence", (run_id,),
                ).fetchall()
            self.assertEqual([day for day, kind, _ in events if kind == "signal"],
                             ["2024-01-02", "2024-01-04"])
            self.assertEqual([day for day, kind, _ in events if kind == "rebalance_skipped"],
                             ["2024-01-03", "2024-01-05"])
            self.assertEqual([(day, json.loads(payload)["side"]) for day, kind, payload
                              in events if kind == "order"],
                             [("2024-01-03", "buy"), ("2024-01-05", "sell")])
            self.assertEqual(len([1 for _, kind, _ in events if kind == "valuation"]), 4)

    def test_feed_interval_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            database = Path(temp) / "feed.sqlite"
            spec = FeedSpec("paced_feed", DAYS[0], "alpaca", "iex", str(database),
                            0, 0, (_cell(),))

            class Provider:
                def actions(self, symbols, start, through):
                    return []

                def sessions(self, start, through):
                    return {day for day in DAYS if start <= day <= through}

                def fetch(self, symbols, start, through):
                    return {day: {"AAA": Bar(day, "AAA", 100, 100, 100, 100, 1000)}
                            for day in DAYS if start <= day <= through}

            provider = Provider()
            for through in DAYS:
                run_feed_batch(spec, "{}", EXAMPLES, provider, through=through,
                               now=datetime(2024, 1, 7, tzinfo=timezone.utc))
            with closing(sqlite3.connect(database)) as connection:
                events = connection.execute(
                    "SELECT date, kind FROM feed_events WHERE feed_id='paced_feed' "
                    "AND cell_name='paced' ORDER BY sequence"
                ).fetchall()
                state = json.loads(connection.execute(
                    "SELECT state_json FROM feed_cells WHERE feed_id='paced_feed' AND name='paced'"
                ).fetchone()[0])
            self.assertEqual([day for day, kind in events if kind == "signal"],
                             ["2024-01-02", "2024-01-04"])
            self.assertEqual([day for day, kind in events if kind == "order"],
                             ["2024-01-03", "2024-01-05"])
            self.assertEqual(state["strategy_states"], [2])

    def test_constraints_check_combined_target_weights(self) -> None:
        strategies = (StrategySpec("strategies:EqualWeight", 0.5, {}),
                      StrategySpec("strategies:EqualWeight", 0.5, {}))
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            for name, cap, reserve, error in (
                ("cap", 0.4, 0, "max_position_weight"),
                ("reserve", 1, 0.1, "min_cash_weight"),
            ):
                with self.subTest(name=name):
                    cell = CellSpec(name, "complex", ("AAA", "BBB"), 1000,
                                    strategies, 1, cap, reserve)
                    spec = RunSpec(str(EXAMPLES / "sample_daily.csv"),
                                   str(Path(temp) / f"{name}.sqlite"), 0, 0, (cell,))
                    run_id = run_static(spec, "{}", EXAMPLES)
                    report = read_report(Path(spec.database_path), run_id)
                    self.assertEqual(report["status"], "completed")
                    self.assertEqual(report["cells"][0]["status"], "failed")
                    self.assertRegex(report["cells"][0]["error"], f"ValueError: .*{error}")
                    self.assertEqual(report["cells"][0]["final_equity"], 1000)

    def test_config_rejects_invalid_interval(self) -> None:
        source = json.loads((EXAMPLES / "experiment.json").read_text(encoding="utf-8"))
        source["cells"][0]["rebalance_every_sessions"] = 0
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "positive integer"):
                load_config(path)

    def test_previous_volume_cap_matches_feed_and_static(self) -> None:
        cell = CellSpec("limited", "simple", ("AAA",), 1000,
                        (StrategySpec("strategies:BuyAndHold", 1, {}),),
                        max_previous_volume_participation=0.1)
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            folder = Path(temp)
            prices = folder / "prices.csv"
            prices.write_text(
                "date,symbol,open,high,low,close,volume\n" +
                "".join(f"{day},AAA,10,10,10,10,{0 if day == DAYS[0] else 10}\n"
                        for day in DAYS),
                encoding="utf-8",
            )
            static_db = folder / "static.sqlite"
            static_id = run_static(RunSpec(str(prices), str(static_db), 0, 0, (cell,)),
                                   "{}", EXAMPLES)
            with closing(sqlite3.connect(static_db)) as connection:
                orders = [json.loads(body) for (body,) in connection.execute(
                    "SELECT payload_json FROM events WHERE run_id=? AND kind='order' "
                    "ORDER BY sequence", (static_id,),
                )]
            self.assertEqual([order["filled_shares"] for order in orders], [0, 1, 1])
            self.assertEqual([order["previous_volume_cap_shares"] for order in orders],
                             [0, 1, 1])

            class Provider:
                def actions(self, symbols, start, through):
                    return []

                def sessions(self, start, through):
                    return {day for day in DAYS if start <= day <= through}

                def fetch(self, symbols, start, through):
                    return {day: {"AAA": Bar(day, "AAA", 10, 10, 10, 10,
                                              0 if day == DAYS[0] else 10)}
                            for day in DAYS if start <= day <= through}

            feed_db = folder / "feed.sqlite"
            feed_spec = FeedSpec("limited_feed", DAYS[0], "alpaca", "iex",
                                 str(feed_db), 0, 0, (cell,))
            for through in DAYS:
                run_feed_batch(feed_spec, "{}", EXAMPLES, Provider(), through=through,
                               now=datetime(2024, 1, 7, tzinfo=timezone.utc))
            self.assertTrue(audit_replay(feed_db, feed_spec.feed_id,
                                         static_db, static_id)["matched"])

    def test_config_rejects_invalid_volume_cap(self) -> None:
        source = json.loads((EXAMPLES / "experiment.json").read_text(encoding="utf-8"))
        source["cells"][0]["max_previous_volume_participation"] = 1.1
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "max_previous_volume_participation"):
                load_config(path)

    def test_volume_cap_also_limits_sells(self) -> None:
        cell = CellSpec("alternating", "simple", ("AAA",), 1000,
                        (StrategySpec("strategies:AlternatingDays", 1, {}),),
                        max_previous_volume_participation=0.1)
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            folder = Path(temp)
            prices = folder / "prices.csv"
            prices.write_text(
                "date,symbol,open,high,low,close,volume\n" +
                "".join(f"{day},AAA,10,10,10,10,10\n" for day in DAYS),
                encoding="utf-8",
            )
            database = folder / "static.sqlite"
            run_id = run_static(RunSpec(str(prices), str(database), 0, 0, (cell,)),
                                "{}", EXAMPLES)
            with closing(sqlite3.connect(database)) as connection:
                orders = [json.loads(body) for (body,) in connection.execute(
                    "SELECT payload_json FROM events WHERE run_id=? AND kind='order' "
                    "ORDER BY sequence", (run_id,),
                )]
            self.assertEqual([(order["side"], order["filled_shares"]) for order in orders],
                             [("buy", 1), ("sell", 1), ("buy", 1)])


if __name__ == "__main__":
    unittest.main()
