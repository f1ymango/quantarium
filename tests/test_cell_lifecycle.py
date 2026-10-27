"""Cell failure isolation, retirement, and late-added Feed cells."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from quantarium.analysis import read_analysis
from quantarium.audit import audit_replay
from quantarium.engine import run_static
from quantarium.export import export_feed_csv
from quantarium.feed import read_feed_report, run_feed_batch
from quantarium.journal import read_report
from quantarium.lifecycle import retire_cell
from quantarium.live import LiveFeed
from quantarium.model import Bar, CellSpec, FeedSpec, LiveSpec, MinuteBar, RunSpec, StrategySpec


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
DAYS = [date(2024, 1, day) for day in (2, 3, 4, 5, 8)]
NOW = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)
STRATEGIES = '''
class Hold:
    def on_bar(self, context):
        if len(context.bars) == 1:
            return 1.0
        return {symbol: 1 / len(context.bars) for symbol in context.bars}


class CrashOn:
    def __init__(self, day):
        self.day = day

    def on_bar(self, context):
        if context.date.isoformat() == self.day:
            raise RuntimeError("boom on " + self.day)
        return 1.0
'''


def cell(name: str, symbols: tuple[str, ...], entrypoint: str = "lifecycle_strategies:Hold",
         **parameters: object) -> CellSpec:
    return CellSpec(name, "simple" if len(symbols) == 1 else "complex", symbols, 1000,
                    (StrategySpec(entrypoint, 1, parameters),))


class Provider:
    def __init__(self, tape: dict[date, dict[str, Bar]]):
        self.tape = tape
        self.fetched: list[set[str]] = []

    def sessions(self, start: date, through: date) -> set[date]:
        return {day for day in self.tape if start <= day <= through}

    def actions(self, symbols: set[str], start: date, through: date) -> list[dict]:
        return []

    def fetch(self, symbols: set[str], start: date, through: date) -> dict[date, dict[str, Bar]]:
        self.fetched.append(set(symbols))
        return {day: {s: bar for s, bar in bars.items() if s in symbols}
                for day, bars in self.tape.items() if start <= day <= through}


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.folder = Path(self.temp.name)
        (self.folder / "lifecycle_strategies.py").write_text(STRATEGIES, encoding="utf-8")
        self.tape = {
            day: {symbol: Bar(day, symbol, price + index, price + index, price + index,
                              price + index, 1000)
                  for symbol, price in (("AAA", 10), ("BBB", 20))}
            for index, day in enumerate(DAYS)
        }
        self.raw = json.dumps({"feed": "lifecycle"})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def feed_spec(self, *cells: CellSpec) -> FeedSpec:
        return FeedSpec("life", DAYS[0], "alpaca", "iex", str(self.folder / "feed.sqlite"),
                        0, 0, cells)

    def run_feed(self, spec: FeedSpec, provider: Provider, through: date) -> dict:
        return run_feed_batch(spec, self.raw, self.folder, provider, through=through, now=NOW)

    def events(self, table: str, cell_name: str) -> list[tuple[str, str]]:
        column = "timestamp" if table == "live_events" else "date"
        database = self.folder / ("live.sqlite" if table == "live_events" else "feed.sqlite")
        with closing(sqlite3.connect(database)) as connection:
            return list(connection.execute(
                f"SELECT {column}, kind FROM {table} WHERE cell_name=? ORDER BY sequence",
                (cell_name,),
            ))

    # Static ----------------------------------------------------------------

    def test_static_failure_stops_one_cell_and_matches_feed_replay(self) -> None:
        cells = (cell("steady", ("AAA", "BBB")),
                 cell("crashy", ("AAA",), "lifecycle_strategies:CrashOn", day="2024-01-04"))
        feed = self.feed_spec(*cells)
        self.run_feed(feed, Provider(self.tape), DAYS[-1])
        feed_report = read_feed_report(Path(feed.database_path), "life")
        statuses = {item["name"]: item["status"] for item in feed_report["cells"]}
        self.assertEqual(statuses, {"steady": "active", "crashy": "failed"})

        export_feed_csv(Path(feed.database_path), "life", self.folder / "tape.csv")
        static = RunSpec(str(self.folder / "tape.csv"), str(self.folder / "static.sqlite"),
                         0, 0, cells)
        run_id = run_static(static, "{}", self.folder)
        report = read_report(Path(static.database_path), run_id)
        by_name = {item["name"]: item for item in report["cells"]}
        self.assertEqual(report["status"], "completed")
        self.assertEqual(by_name["steady"]["status"], "completed")
        self.assertEqual(by_name["crashy"]["status"], "failed")
        self.assertEqual(by_name["crashy"]["error"], "RuntimeError: boom on 2024-01-04")
        with closing(sqlite3.connect(static.database_path)) as connection:
            crashy_days = [row for row in connection.execute(
                "SELECT date, kind FROM events WHERE cell_name='crashy' AND date='2024-01-04' "
                "ORDER BY sequence")]
            self.assertEqual(crashy_days, [("2024-01-04", "cell_failed")])
            equity = json.loads(connection.execute(
                "SELECT payload_json FROM events WHERE cell_name='crashy' AND kind='valuation' "
                "ORDER BY sequence DESC LIMIT 1").fetchone()[0])["equity"]
        self.assertEqual(by_name["crashy"]["final_equity"], equity)
        audit = audit_replay(Path(feed.database_path), "life", Path(static.database_path), run_id)
        self.assertTrue(audit["matched"], audit["mismatches"])

        analysis = read_analysis(Path(static.database_path), "static", benchmark="steady")
        crashy = next(item for item in analysis["cells"] if item["name"] == "crashy")
        self.assertEqual(crashy["sessions"], 2)
        self.assertIsNone(crashy["excess_return_pp"])
        self.assertEqual(crashy["status"], "failed")

    def test_static_failure_keeps_the_same_events_whether_days_are_committed_or_savepointed(self) -> None:
        from quantarium import journal

        cells = (cell("steady", ("AAA", "BBB")),
                 cell("crashy", ("AAA",), "lifecycle_strategies:CrashOn", day="2024-01-04"))
        self.run_feed(self.feed_spec(*cells), Provider(self.tape), DAYS[-1])
        export_feed_csv(Path(self.feed_spec(*cells).database_path), "life", self.folder / "tape.csv")
        events = {}
        original = journal.COMMIT_SECONDS
        try:
            for label, seconds in (("every day", 0.0), ("never", 3600.0)):
                journal.COMMIT_SECONDS = seconds
                path = self.folder / f"static-{seconds:g}.sqlite"
                run_id = run_static(RunSpec(str(self.folder / "tape.csv"), str(path), 0, 0, cells), "{}",
                                    self.folder)
                with closing(sqlite3.connect(path)) as connection:
                    events[label] = connection.execute(
                        "SELECT cell_name, sequence, date, kind, payload_json FROM events WHERE run_id=? "
                        "ORDER BY cell_name, sequence", (run_id,)).fetchall()
                    statuses = dict(connection.execute("SELECT name, status FROM cells WHERE run_id=?", (run_id,)))
                self.assertEqual(statuses, {"steady": "completed", "crashy": "failed"}, label)
        finally:
            journal.COMMIT_SECONDS = original
        self.assertEqual(events["every day"], events["never"])
        crashy = [row[2:4] for row in events["never"] if row[0] == "crashy" and row[2] == "2024-01-04"]
        self.assertEqual(crashy, [("2024-01-04", "cell_failed")])

    # Feed Batch --------------------------------------------------------------

    def test_feed_cell_added_later_starts_after_the_last_session(self) -> None:
        first = cell("first", ("AAA",))
        provider = Provider(self.tape)
        self.run_feed(self.feed_spec(first), provider, DAYS[1])
        late = cell("late", ("AAA", "BBB"))
        report = self.run_feed(self.feed_spec(first, late), provider, DAYS[3])
        cells = {item["name"]: item for item in report["cells"]}
        self.assertEqual(report["days_processed_this_call"], 2)
        self.assertEqual(cells["late"]["joined_after"], DAYS[1].isoformat())
        self.assertEqual(cells["late"]["last_date"], DAYS[3].isoformat())
        late_events = self.events("feed_events", "late")
        self.assertEqual(late_events[0], (DAYS[2].isoformat(), "strategy"))
        self.assertNotIn(DAYS[1].isoformat(), {day for day, _ in late_events})
        self.assertIn({"AAA", "BBB"}, provider.fetched)

        analysis = read_analysis(Path(self.folder / "feed.sqlite"), "feed", "life",
                                 benchmark="first")
        late_rows = [row for row in analysis["curve"] if row["cell"] == "late"]
        self.assertEqual(late_rows[0]["benchmark_index_100"], late_rows[0]["index_100"])
        first_curve = {row["date"]: row for row in analysis["curve"] if row["cell"] == "first"}
        expected = (late_rows[0]["index_100"] * first_curve[DAYS[3].isoformat()]["equity"]
                    / first_curve[DAYS[2].isoformat()]["equity"])
        self.assertAlmostEqual(late_rows[-1]["benchmark_index_100"], expected)

    def test_feed_cells_cannot_change_or_disappear(self) -> None:
        first, second = cell("first", ("AAA",)), cell("second", ("BBB",))
        provider = Provider(self.tape)
        self.run_feed(self.feed_spec(first, second), provider, DAYS[0])
        with self.assertRaisesRegex(ValueError, "Cell configuration changed in first"):
            self.run_feed(self.feed_spec(replace(first, initial_cash=5), second),
                          provider, DAYS[1])
        with self.assertRaisesRegex(ValueError, "cannot be removed.*second"):
            self.run_feed(self.feed_spec(first), provider, DAYS[1])
        with self.assertRaisesRegex(ValueError, "configuration changed outside its cells"):
            run_feed_batch(self.feed_spec(first, second), json.dumps({"feed": "other"}),
                           self.folder, provider, through=DAYS[1], now=NOW)

    def test_retired_feed_cell_stops_and_its_symbols_are_not_fetched(self) -> None:
        keep, retire = cell("keep", ("AAA",)), cell("retire", ("BBB",))
        spec = self.feed_spec(keep, retire)
        provider = Provider(self.tape)
        self.run_feed(spec, provider, DAYS[1])
        result = retire_cell("feed", Path(spec.database_path), "life", "retire", "done testing")
        self.assertEqual(result["retired_at"], DAYS[1].isoformat())
        with self.assertRaisesRegex(ValueError, "already retired"):
            retire_cell("feed", Path(spec.database_path), "life", "retire")
        with self.assertRaisesRegex(ValueError, "not found"):
            retire_cell("feed", Path(spec.database_path), "life", "nobody")
        report = self.run_feed(spec, provider, DAYS[3])
        cells = {item["name"]: item for item in report["cells"]}
        self.assertEqual(cells["retire"]["status"], "retired")
        self.assertEqual(cells["retire"]["last_date"], DAYS[1].isoformat())
        self.assertEqual(cells["keep"]["last_date"], DAYS[3].isoformat())
        self.assertEqual(provider.fetched[-1], {"AAA"})
        self.assertEqual(self.events("feed_events", "retire")[-1],
                         (DAYS[1].isoformat(), "cell_retired"))
        # BBB's saved history now has gaps, so a new cell cannot start on it.
        with self.assertRaisesRegex(ValueError, "history for BBB has gaps"):
            self.run_feed(self.feed_spec(keep, retire, cell("again", ("BBB",))),
                          provider, DAYS[4])

    def test_feed_saved_before_per_cell_pinning_upgrades_on_identical_resume(self) -> None:
        first = cell("first", ("AAA",))
        provider = Provider(self.tape)
        self.run_feed(self.feed_spec(first), provider, DAYS[0])
        with closing(sqlite3.connect(self.folder / "feed.sqlite")) as connection:
            connection.execute("UPDATE feed_experiments SET settings_sha256=NULL")
            connection.execute("UPDATE feed_cells SET spec_sha256=NULL")
            connection.commit()
        with self.assertRaisesRegex(ValueError, "original config before adding cells"):
            self.run_feed(self.feed_spec(first, cell("late", ("BBB",))), provider, DAYS[1])
        self.run_feed(self.feed_spec(first), provider, DAYS[1])
        report = self.run_feed(self.feed_spec(first, cell("late", ("BBB",))), provider, DAYS[2])
        self.assertEqual({item["name"] for item in report["cells"]}, {"first", "late"})

    def test_feed_sessions_are_labeled_backfill_or_forward(self) -> None:
        spec = self.feed_spec(cell("only", ("AAA",)))
        self.run_feed(spec, Provider(self.tape), DAYS[-1])
        # Pretend the config was frozen after the 2024-01-03 close.
        with closing(sqlite3.connect(spec.database_path)) as connection:
            connection.execute("UPDATE feed_cells SET created_utc='2024-01-03T22:00:00+00:00'")
            connection.commit()
        analysis = read_analysis(Path(spec.database_path), "feed", "life")
        summary = analysis["cells"][0]
        self.assertEqual(summary["backfill_sessions"], 2)
        self.assertEqual(summary["forward_sessions"], 3)
        phases = [row["phase"] for row in analysis["curve"]]
        self.assertEqual(phases, ["backfill"] * 2 + ["forward"] * 3)
        equity = {row["date"]: row["equity"] for row in analysis["curve"]}
        self.assertAlmostEqual(summary["forward_return_pct"],
                               100 * (equity[DAYS[-1].isoformat()] / equity[DAYS[1].isoformat()] - 1))
        # Created on the first session's afternoon: everything is forward.
        with closing(sqlite3.connect(spec.database_path)) as connection:
            connection.execute("UPDATE feed_cells SET created_utc='2024-01-02T15:00:00+00:00'")
            connection.commit()
        summary = read_analysis(Path(spec.database_path), "feed", "life")["cells"][0]
        self.assertEqual(summary["backfill_sessions"], 0)
        self.assertAlmostEqual(summary["forward_return_pct"], summary["total_return_pct"])

    # Live Feed ---------------------------------------------------------------

    def live_feed(self) -> LiveFeed:
        start = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
        self.start = start
        spec = LiveSpec("life_live", start, start + timedelta(minutes=4), "alpaca", "iex",
                        str(self.folder / "live.sqlite"), 0, 0,
                        (cell("one", ("AAA",)), cell("two", ("AAA",))))
        return LiveFeed(spec, json.dumps({"live": "lifecycle"}), self.folder)

    def advance(self, feed: LiveFeed, offset: int) -> None:
        stamp = self.start + timedelta(minutes=offset)
        feed.accept(MinuteBar(stamp, "AAA", 10, 10, 10, 10, 100), stamp + timedelta(seconds=61))
        feed.flush(stamp + timedelta(seconds=95))

    def test_live_cell_retired_while_running(self) -> None:
        feed = self.live_feed()
        try:
            self.advance(feed, 0)
            retire_cell("live", self.folder / "live.sqlite", "life_live", "one")
            self.advance(feed, 1)
            self.advance(feed, 2)
            report = feed.report()
            cells = {item["name"]: item for item in report["cells"]}
            self.assertEqual(cells["one"]["status"], "retired")
            self.assertEqual(cells["one"]["bars_processed"], 1)
            self.assertEqual(cells["two"]["bars_processed"], 3)
            self.assertEqual(self.events("live_events", "one")[-1],
                             (self.start.isoformat(), "cell_retired"))
            self.advance(feed, 3)
            self.assertEqual(feed.report()["status"], "complete")
            cells = {item["name"]: item for item in feed.report()["cells"]}
            self.assertEqual(cells["one"]["status"], "retired")
            self.assertEqual(cells["two"]["status"], "completed")
        finally:
            feed.close()

    def test_retiring_every_live_cell_halts(self) -> None:
        feed = self.live_feed()
        try:
            self.advance(feed, 0)
            for name in ("one", "two"):
                retire_cell("live", self.folder / "live.sqlite", "life_live", name)
            self.advance(feed, 1)
            self.assertEqual(feed.report()["status"], "halted")
            self.assertEqual(feed.report()["stop_reason"], "No active Live Feed cells remain")
            with self.assertRaisesRegex(ValueError, "Live Feed is halted"):
                retire_cell("live", self.folder / "live.sqlite", "life_live", "one")
        finally:
            feed.close()


if __name__ == "__main__":
    unittest.main()
