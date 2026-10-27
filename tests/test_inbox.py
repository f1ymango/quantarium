"""Forward inboxes: decisions submitted before their sessions, stamped on arrival (v0.6.3)."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from quantarium import inbox
from quantarium.__main__ import main
from quantarium.api import Api
from quantarium.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
STEADY = ROOT / "examples" / "scenarios" / "steady_bull"


def at(text: str):
    return lambda: datetime.fromisoformat(text)


class DeadlineTests(unittest.TestCase):
    def test_the_deadline_is_the_next_session_open_in_new_york(self) -> None:
        # Friday's decision is due before Monday's open, 09:30 New York (summer time: 13:30 UTC).
        self.assertEqual(inbox.deadline(date(2026, 10, 23)), datetime(2026, 10, 26, 13, 30, tzinfo=timezone.utc))
        # Thanksgiving is closed: Wednesday's decision is due Friday at 09:30 (winter time: 14:30 UTC).
        self.assertEqual(inbox.deadline(date(2026, 11, 25)), datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc))


class InboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        inbox.create(self.root, "agent-1", ["AAA", "bbb"], "user")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def submit(self, when: str, **params) -> dict:
        return inbox.submit(self.root, "agent-1", params, "user", now=at(when))

    def test_on_time_decisions_count_and_late_ones_do_not(self) -> None:
        on_time = self.submit("2030-01-07T20:00:00+00:00", date="2030-01-07", weights={"AAA": 0.5})
        late = self.submit("2030-01-08T15:00:00+00:00", date="2030-01-07", weights={"AAA": 1})
        self.assertTrue(on_time["on_time"])
        self.assertFalse(late["on_time"])
        replaced = self.submit("2030-01-08T13:00:00+00:00", date="2030-01-08", weights={"AAA": 0.2})
        self.submit("2030-01-08T23:00:00+00:00", date="2030-01-08", weights=None, note="changed my mind")
        self.assertTrue(replaced["on_time"])
        read = inbox.read(inbox.inbox_path(self.root, "agent-1"))
        self.assertEqual(dict(read.entry(date(2030, 1, 7)).weights), {"AAA": 0.5})
        self.assertIsNone(read.entry(date(2030, 1, 8)).weights)
        self.assertEqual((read.late, read.first_on_time), (1, date(2030, 1, 7)))
        summary = inbox.summary(inbox.inbox_path(self.root, "agent-1"))
        self.assertEqual((summary["submissions"], summary["on_time"], summary["late"], summary["symbols"]),
                         (4, 2, 1, ["AAA", "BBB"]))

    def test_the_inbox_is_append_only_and_can_be_read_as_it_was(self) -> None:
        path = inbox.inbox_path(self.root, "agent-1")
        self.submit("2030-01-07T20:00:00+00:00", date="2030-01-07", weights={"AAA": 0.5})
        frozen = inbox.pin(path)
        self.submit("2030-01-08T20:00:00+00:00", date="2030-01-08", weights={"AAA": 1})
        self.assertEqual(len(inbox.read(path, frozen["bytes"]).decisions), 1)
        self.assertEqual(len(inbox.read(path).decisions), 2)

    def test_bad_requests_are_refused(self) -> None:
        cases = [
            lambda: inbox.create(self.root, "agent-1", ["AAA"], "user"),
            lambda: inbox.create(self.root, "../escape", ["AAA"], "user"),
            lambda: inbox.create(self.root, ".hidden", ["AAA"], "user"),
            lambda: inbox.create(self.root, "x", [], "user"),
            lambda: inbox.submit(self.root, "missing", {"date": "2030-01-07", "weights": None}, "user"),
            lambda: self.submit("2030-01-07T20:00:00+00:00", date="2030-01-07", weights={"ZZZ": 0.5}),
            lambda: self.submit("2030-01-07T20:00:00+00:00", date="2030-01-07"),
            lambda: self.submit("2030-01-07T20:00:00+00:00", date="2030-01-07", weights={"AAA": 2}),
            lambda: self.submit("2030-01-07T20:00:00+00:00", date="soon", weights=None),
            lambda: self.submit("2030-01-07T20:00:00+00:00", date="2030-01-07", weights=None, extra=1),
        ]
        for index, case in enumerate(cases):
            with self.subTest(index), self.assertRaises(ValueError):
                case()

    def test_the_command_line(self) -> None:
        output = io.StringIO()
        with mock.patch("sys.argv", ["quantarium", "decisions", "inbox-create", "cli", "--symbols", "AAA,BBB",
                                     "--workspace", str(self.root)]), redirect_stdout(output):
            main()
        with mock.patch("sys.argv", ["quantarium", "decisions", "submit", "cli", "--date", "2099-01-05",
                                     "--weights", "AAA=0.5,BBB=0.25", "--workspace", str(self.root)]), \
                redirect_stdout(output):
            main()
        self.assertEqual(inbox.summary(inbox.inbox_path(self.root, "cli"))["on_time"], 1)


class ForwardRefereeTests(unittest.TestCase):
    """An inbox cell runs like any cell, and the Lab judges it in full on real prices."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        shutil.copytree(STEADY, self.root / "scenarios" / "steady_bull")
        (self.root / "data").mkdir()
        shutil.copy(STEADY / "prices.csv", self.root / "data" / "prices.csv")
        self.api = Api(Workspace(self.root))
        job = self.api.wait(self.api.submit("scenario-slice", {
            "source": "data/prices.csv", "output": "scenarios/real", "name": "real",
            "from": "2030-01-07", "through": "2030-06-28"})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])
        inbox.create(self.root, "fwd", ["AAA"], "user")
        for day, weight in (("2030-01-07", 1.0), ("2030-03-01", 0.5)):
            inbox.submit(self.root, "fwd", {"date": day, "weights": {"AAA": weight}}, "user",
                         now=at("2029-12-31T12:00:00+00:00"))
        inbox.submit(self.root, "fwd", {"date": "2030-02-01", "weights": {"AAA": 0.0}}, "user",
                     now=at("2030-03-01T12:00:00+00:00"))
        record = self.api.command("cell-create", {"name": "Forward"})
        self.cell = self.api.command("cell-save", {"id": record["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000, "sleeves": [{"inbox": "fwd"}]}})

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def register(self, scenario: str = "scenarios/real") -> dict:
        return self.api.command("lab-register", {"idea": "forward", "name": "Forward", "cells": [self.cell["id"]],
                                                 "scenarios": [scenario],
                                                 "pass_rule": {"min_mean_excess_return_pp": 0, "min_sessions": 20}})

    def test_an_inbox_cell_runs_and_ignores_late_decisions(self) -> None:
        self.assertEqual(self.api.cells.check(self.api.cells.get(self.cell["id"])), [])
        job = self.api.wait(self.api.submit("deploy-static", {"cells": [self.cell["id"]],
                                                               "data": "data/prices.csv"})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])

    def test_the_lab_judges_forward_decisions_in_full(self) -> None:
        experiment = self.register()
        self.assertEqual(experiment["spec"]["decisions"], "forward")
        self.assertEqual(experiment["spec"]["inboxes"]["fwd"]["late"], 1)
        # Decisions submitted after registration are not part of it.
        inbox.submit(self.root, "fwd", {"date": "2030-04-01", "weights": {"AAA": 0.0}}, "user",
                     now=at("2029-12-31T12:00:00+00:00"))
        job = self.api.wait(self.api.submit("lab-run", {"id": experiment["id"]})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])
        verdict = job["result"]["result"]["verdict"]
        self.assertIn(verdict["status"], {"supported_within_tested_scope", "not_supported"})
        self.assertTrue(any("late decision" in caveat for caveat in verdict["caveats"]), verdict["caveats"])

    def test_synthetic_markets_and_rewritten_inboxes_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.register("scenarios/steady_bull")
        experiment = self.register()
        path = inbox.inbox_path(self.root, "fwd")
        path.write_text(path.read_text(encoding="utf-8").replace('"AAA": 0.5', '"AAA": 0.9'), encoding="utf-8")
        job = self.api.wait(self.api.submit("lab-run", {"id": experiment["id"]})["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("changed", job["error"]["message"])


if __name__ == "__main__":
    unittest.main()
