"""Composite and regime supercells: allocators that split money between a cell's sleeves."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from quantarium.config import _cells, load_live_config
from quantarium.engine import run_static
from quantarium.model import RunSpec
from quantarium.provenance import record_agent_write


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
STEADY = ROOT / "examples" / "scenarios" / "steady_bull"
SLEEVES = '''class AllIn:
    """Everything in the first symbol."""

    def on_bar(self, context):
        first = sorted(context.bars)[0]
        return {first: 1.0}


class Cash:
    def on_bar(self, context):
        return {}
'''
ALLOCATORS = '''class Alternate:
    """Odd sessions to the first sleeve, even ones to the second; it remembers the count."""

    def __init__(self):
        self.count = 0

    def allocate(self, context, sleeves):
        self.count += 1
        return [1.0, 0.0] if self.count % 2 else [0.0, 1.0]

    def snapshot(self):
        return {"count": self.count}

    def restore(self, state):
        self.count = state["count"]


class Greedy:
    def allocate(self, context, sleeves):
        return [0.8, 0.8]
'''


def cell(allocator: dict | None, **extra) -> dict:
    entry = {"name": "Composite", "mode": "complex", "symbols": ["AAA", "BBB"], "initial_cash": 10000,
             "strategies": [{"entrypoint": "sleeves:AllIn", "allocation": 0.5},
                            {"entrypoint": "sleeves:Cash", "allocation": 0.5}], **extra}
    if allocator is not None:
        entry["allocator"] = allocator
    return entry


class AllocatorEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        (self.root / "sleeves.py").write_text(SLEEVES, encoding="utf-8")
        (self.root / "allocators_local.py").write_text(ALLOCATORS, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cell(self, entry: dict) -> list[dict]:
        spec = RunSpec(str(STEADY / "prices.csv"), str(self.root / "run.sqlite"), 0, 0, _cells({"cells": [entry]}))
        run_static(spec, json.dumps({"cells": [entry]}), self.root)
        with closing(sqlite3.connect(self.root / "run.sqlite")) as db:
            status = db.execute("SELECT status, error FROM cells WHERE name='Composite'").fetchone()
            signals = [json.loads(row[0]) for row in db.execute(
                "SELECT payload_json FROM events WHERE kind='signal' ORDER BY sequence")]
        self.assertEqual(status[0], "completed", status[1])
        return signals

    def test_fixed_allocations_are_unchanged_without_an_allocator(self) -> None:
        signals = self.run_cell(cell(None))
        self.assertEqual(signals[0]["target_weights"], {"AAA": 0.5})
        self.assertNotIn("allocation", signals[0])

    def test_an_allocator_splits_money_between_sleeves(self) -> None:
        signals = self.run_cell(cell({"entrypoint": "allocators_local:Alternate"}))
        self.assertEqual([signal["allocation"] for signal in signals[:2]], [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(signals[0]["target_weights"], {"AAA": 1.0})
        self.assertEqual(signals[1]["target_weights"], {})

    def test_the_built_in_trend_regime_switches_on_a_moving_average(self) -> None:
        signals = self.run_cell(cell({"entrypoint": "quantarium.allocators:TrendRegime",
                                      "parameters": {"symbol": "AAA", "window": 20}}))
        # Until 20 sessions of history exist, the money stays with the risk-off sleeve (cash).
        self.assertTrue(all(signal["allocation"] == [0.0, 1.0] for signal in signals[:19]))
        self.assertIn([1.0, 0.0], [signal["allocation"] for signal in signals[19:]])

    def test_an_allocator_cannot_give_out_more_than_everything(self) -> None:
        entry = cell({"entrypoint": "allocators_local:Greedy"})
        spec = RunSpec(str(STEADY / "prices.csv"), str(self.root / "run.sqlite"), 0, 0, _cells({"cells": [entry]}))
        run_static(spec, "{}", self.root)
        with closing(sqlite3.connect(self.root / "run.sqlite")) as db:
            status, error = db.execute("SELECT status, error FROM cells").fetchone()
        self.assertEqual(status, "failed")
        self.assertIn("more than 100%", error)

    def test_an_unreviewed_agent_allocator_is_refused_not_sandboxed(self) -> None:
        from quantarium.provenance import UnreviewedScript

        source = (self.root / "allocators_local.py").read_bytes()
        record_agent_write(self.root, "allocators_local.py", source, {"id": "a_1", "name": "Agent"})
        entry = cell({"entrypoint": "allocators_local:Alternate"})
        spec = RunSpec(str(STEADY / "prices.csv"), str(self.root / "run.sqlite"), 0, 0, _cells({"cells": [entry]}))
        # Even in Static, where unreviewed strategies run in the sandbox, an allocator is refused outright.
        with self.assertRaisesRegex(UnreviewedScript, "has not been reviewed"):
            run_static(spec, "{}", self.root)

    def test_an_allocators_state_survives_a_checkpoint(self) -> None:
        from quantarium.engine import CellRuntime

        spec = RunSpec(str(STEADY / "prices.csv"), str(self.root / "run.sqlite"), 0, 0,
                       _cells({"cells": [cell({"entrypoint": "allocators_local:Alternate"})]}))
        runtime = CellRuntime(spec.cells[0], spec, self.root)
        runtime.allocator.count = 7
        saved = runtime.checkpoint()
        self.assertEqual(saved["allocator_state"], {"count": 7})
        resumed = CellRuntime(spec.cells[0], spec, self.root, saved=saved,
                              prior_history={"AAA": [], "BBB": []})
        self.assertEqual(resumed.allocator.count, 7)
        # A cell without an allocator keeps the old checkpoint shape.
        plain = CellRuntime(_cells({"cells": [cell(None)]})[0], spec, self.root)
        self.assertNotIn("allocator_state", plain.checkpoint())

    def test_allocators_need_a_complex_cell_and_are_refused_on_live(self) -> None:
        with self.assertRaisesRegex(ValueError, "Complex"):
            _cells({"cells": [{**cell({"entrypoint": "a:B"}), "mode": "simple", "symbols": ["AAA"],
                               "strategies": [{"entrypoint": "sleeves:AllIn", "allocation": 1}]}]})
        config = self.root / "live.json"
        source = json.loads((ROOT / "examples" / "live_replay_experiment.json").read_text(encoding="utf-8"))
        source["cells"][0] = {**source["cells"][0], "mode": "complex",
                              "allocator": {"entrypoint": "quantarium.allocators:FixedAllocation"}}
        config.write_text(json.dumps(source), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Allocators"):
            load_live_config(config)


class AllocatorLibraryTests(unittest.TestCase):
    """Allocators through the cell library, the Lab, and a language model."""

    def setUp(self) -> None:
        import shutil

        from quantarium.api import Api
        from quantarium.workspace import Workspace

        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        (self.root / "strategies").mkdir()
        (self.root / "strategies" / "sleeves.py").write_text(SLEEVES, encoding="utf-8")
        shutil.copytree(STEADY, self.root / "scenarios" / "steady_bull")
        self.api = Api(Workspace(self.root))
        record = self.api.command("cell-create", {"name": "Regime"})
        self.spec = {"mode": "complex", "symbols": ["AAA", "BBB"], "initial_cash": 10000,
                     "allocator": {"builtin": True, "class": "TrendRegime",
                                   "parameters": {"symbol": "AAA", "window": 20}},
                     "sleeves": [{"script": "strategies/sleeves.py", "class": "AllIn", "allocation": 0.5},
                                 {"script": "strategies/sleeves.py", "class": "Cash", "allocation": 0.5}]}
        self.cell = self.api.command("cell-save", {"id": record["id"], "spec": self.spec})

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def test_a_built_in_regime_runs_from_the_library(self) -> None:
        self.assertEqual(self.api.cells.check(self.api.cells.get(self.cell["id"])), [])
        job = self.api.wait(self.api.submit("deploy-static", {"cells": [self.cell["id"]],
                                                               "data": "scenarios/steady_bull/prices.csv"})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])

    def test_the_lab_shows_what_the_allocator_adds_over_fixed_allocations(self) -> None:
        registered = self.api.command("lab-register", {"idea": "regime", "name": "Trend regime",
                                                       "cells": [self.cell["id"]],
                                                       "scenarios": ["scenarios/steady_bull"]})
        job = self.api.wait(self.api.submit("lab-run", {"id": registered["id"]})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])
        ablations = job["result"]["result"]["verdict"]["rows"][0]["ablations"]
        self.assertEqual([entry["part"] for entry in ablations], ["allocator"])
        self.assertIsNotNone(ablations[0]["contribution_pp"])

    def test_problems_are_reported_for_bad_allocators(self) -> None:
        for allocator, problem in (({"builtin": True, "class": "Nope"}, "Unknown built-in"),
                                   ({"script": "strategies/sleeves.py", "class": "AllIn"}, "was not found")):
            with self.subTest(allocator=allocator):
                saved = self.api.command("cell-save", {"id": self.cell["id"],
                                                       "spec": {**self.spec, "allocator": allocator}})
                self.assertTrue(any(problem in item for item in self.api.cells.check(saved)))

    def test_an_allocator_may_ask_a_model_and_its_ablation_keeps_the_last_split(self) -> None:
        import hashlib
        from unittest import mock

        from quantarium.agents import NOTICE_VERSION

        self.api.agents.enable("I understand and accept", NOTICE_VERSION)
        connection = self.api.agents.create_connection("Local", kind="direct", provider={
            "preset": "custom", "protocol": "openai-chat", "endpoint": "http://localhost:1234/v1",
            "model": "m", "key_store": "none"})["connection"]
        script = self.root / "strategies" / "asking_allocator.py"
        script.write_text('''SCHEMA = {"type": "object", "properties": {"sleeve": {"type": "integer", "minimum": 0, "maximum": 1}},
          "required": ["sleeve"], "additionalProperties": False}


class AskingAllocator:
    def allocate(self, context, sleeves):
        chosen = context.ask(f"Which sleeve on {context.date}?", SCHEMA)["sleeve"]
        return [1.0 if index == chosen else 0.0 for index in range(len(sleeves))]
''', encoding="utf-8")
        grant = {"connection_id": connection["id"], "model": "m", "workspace": str(self.root),
                 "tape": ".quantarium/tapes/allocator.jsonl", "mode": "record",
                 "approved_script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
                 "max_calls": 1000, "max_tokens": 100000, "max_estimated_cost_usd": 1,
                 "input_usd_per_million": 1, "output_usd_per_million": 1,
                 "min_sessions_between_calls": 1, "max_output_tokens": 20}
        self.api.command("cell-save", {"id": self.cell["id"], "spec": {
            **self.spec, "ask": grant,
            "allocator": {"script": "strategies/asking_allocator.py", "class": "AskingAllocator"}}})

        class First:
            calls = 0

            def complete(self, system, messages, tools, max_tokens):
                First.calls += 1
                return {"content": [{"type": "text", "text": '{"sleeve": 0}'}],
                        "usage": {"input_tokens": 5, "output_tokens": 3}}

        registered = self.api.command("lab-register", {"idea": "asking_allocator", "name": "n",
                                                       "cells": [self.cell["id"]],
                                                       "scenarios": ["scenarios/steady_bull"]})
        with mock.patch("quantarium.ask.provider_for", return_value=First()):
            job = self.api.wait(self.api.submit("lab-run", {"id": registered["id"]})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])
        rows = job["result"]["result"]["verdict"]["rows"]
        parts = {entry["part"]: entry for entry in rows[0]["ablations"]}
        self.assertEqual(set(parts), {"ask", "allocator"})
        # The model picked the invested sleeve every day; without it the allocator keeps the fixed split.
        self.assertGreater(parts["ask"]["contribution_pp"], 0)
        self.assertEqual(First.calls, rows[0]["sessions"])
