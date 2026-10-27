"""LLM and hybrid supercells in the Lab: cutoffs, lineage, ablation, and the disguised replay."""

from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from quantarium.agents import NOTICE_VERSION, AgentSettings
from quantarium.api import Api
from quantarium.config import _cells, load_feed_config
from quantarium.scenario import disguise_scenario, load_scenario, slice_scenario
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
STEADY = ROOT / "examples" / "scenarios" / "steady_bull"
# Asks every session, with the date in the prompt, and invests only when told to.
ASKER = '''SCHEMA = {"type": "object", "properties": {"action": {"type": "string", "enum": ["hold", "invest"], "maxLength": 6}},
          "required": ["action"], "additionalProperties": False}


class Asker:
    def on_bar(self, context):
        answer = context.ask(f"On {context.date}, invest in {sorted(context.bars)}?", SCHEMA)
        return 1.0 if answer["action"] == "invest" else 0.0
'''


class Investor:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system, messages, tools, max_tokens):
        self.calls += 1
        return {"content": [{"type": "text", "text": '{"action": "invest"}'}],
                "usage": {"input_tokens": 10, "output_tokens": 5}}


class SupercellLabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        settings = AgentSettings(Workspace(self.root))
        settings.enable("I understand and accept", NOTICE_VERSION)
        self.connection = settings.create_connection("Local", kind="direct", provider={
            "preset": "custom", "protocol": "openai-chat", "endpoint": "http://localhost:1234/v1",
            "model": "test-model", "key_store": "none"})["connection"]
        # A short "real history" slice, so dates and lineage are those of real data.
        slice_scenario(STEADY / "prices.csv", self.root / "scenarios" / "history",
                       date(2030, 1, 7), date(2030, 2, 28), name="history")
        script = self.root / "strategies" / "asker.py"
        script.parent.mkdir(parents=True)
        script.write_text(ASKER, encoding="utf-8")
        self.api = Api(Workspace(self.root))
        record = self.api.command("cell-create", {"name": "Asker"})
        self.cell = self.api.command("cell-save", {"id": record["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000, "ask": self.grant(script),
            "sleeves": [{"script": "strategies/asker.py", "class": "Asker", "allocation": 1, "parameters": {}}]}})

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def grant(self, script: Path) -> dict:
        return {"connection_id": self.connection["id"], "model": "test-model", "workspace": str(self.root),
                "tape": ".quantarium/tapes/asker.jsonl", "mode": "record",
                "approved_script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
                "max_calls": 500, "max_tokens": 100000, "max_estimated_cost_usd": 1,
                "input_usd_per_million": 1, "output_usd_per_million": 1,
                "min_sessions_between_calls": 1, "max_output_tokens": 50}

    def run_lab(self, **extra) -> tuple[dict, Investor]:
        params = {"idea": "asker", "name": "Asking cell", "cells": [self.cell["id"]],
                  "scenarios": ["scenarios/history"], "pass_rule": {"min_sessions": 20}, **extra}
        registered = self.api.command("lab-register", params)
        provider = Investor()
        with mock.patch("quantarium.ask.provider_for", return_value=provider):
            job = self.api.wait(self.api.submit("lab-run", {"id": registered["id"]})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])
        return job["result"]["result"]["verdict"], provider

    def set_cutoff(self, value: str | None) -> None:
        self.api.command("agent-connection-cutoff", {"id": self.connection["id"], "training_cutoff": value})

    def test_real_history_after_the_cutoff_is_clean(self) -> None:
        self.set_cutoff("2029-12-31")
        verdict, _ = self.run_lab()
        self.assertEqual(verdict["rows"][0]["model_contamination"], "clean")
        self.assertEqual(verdict["scope"]["model_training_cutoff"], "2029-12-31")
        self.assertNotEqual(verdict["status"], "unknown")

    def test_real_history_before_the_cutoff_is_contaminated(self) -> None:
        self.set_cutoff("2030-06-30")
        verdict, _ = self.run_lab()
        self.assertEqual(verdict["rows"][0]["model_contamination"], "contaminated")
        self.assertEqual(verdict["status"], "unknown")
        self.assertTrue(any("may remember" in caveat for caveat in verdict["caveats"]))

    def test_without_a_cutoff_real_history_is_unknown(self) -> None:
        verdict, _ = self.run_lab()
        self.assertEqual((verdict["rows"][0]["model_contamination"], verdict["status"]), ("unknown", "unknown"))

    def test_a_changed_cutoff_makes_the_registration_stale(self) -> None:
        self.set_cutoff("2029-12-31")
        registered = self.api.command("lab-register", {"idea": "asker", "name": "n", "cells": [self.cell["id"]],
                                                       "scenarios": ["scenarios/history"]})
        self.set_cutoff("2031-01-01")
        job = self.api.wait(self.api.submit("lab-run", {"id": registered["id"]})["id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.api.get("lab/" + registered["id"], {})["status"], "stale")

    def test_a_new_model_clears_its_cutoff(self) -> None:
        self.set_cutoff("2029-12-31")
        updated = self.api.command("agent-connection-model", {"id": self.connection["id"], "model": "other"})
        self.assertNotIn("training_cutoff", updated)

    def test_ablation_shows_what_the_model_adds_and_calls_nothing(self) -> None:
        verdict, provider = self.run_lab()
        sessions = verdict["rows"][0]["sessions"]
        # The candidate asked once per session; the variant without the model asked nobody.
        self.assertEqual(provider.calls, sessions)
        ablation = verdict["rows"][0]["ablations"]
        self.assertEqual([entry["part"] for entry in ablation], ["ask"])
        # Without the model's say, the sleeve keeps its last target: cash from the start.
        self.assertEqual(ablation[0]["return_pct"], 0)
        self.assertGreater(ablation[0]["contribution_pp"], 0)
        self.assertTrue(any("Without the language model" in caveat for caveat in verdict["caveats"]))

    def test_the_disguised_replay_hides_identity_and_reports_the_gap(self) -> None:
        verdict, provider = self.run_lab(disguised_replay=True)
        disguise = verdict["disguise"]
        self.assertEqual(len(disguise["rows"]), 1)
        row = disguise["rows"][0]
        self.assertEqual(row["status"], "completed", row["error"])
        # Investing every day: returns are the same shape, so the gap is only whole-share rounding.
        self.assertLess(abs(row["gap_pp"]), 1.0)
        # The disguised run asked again, about invented symbols on future dates.
        tape = [json.loads(line) for line in (self.root / ".quantarium/tapes/asker.jsonl").read_text().splitlines()]
        self.assertEqual(provider.calls, len(tape))
        self.assertTrue(any(entry["day"] >= "2041" for entry in tape))

    def test_supercells_list_parts_tape_and_latest_evidence(self) -> None:
        self.set_cutoff("2029-12-31")
        before = self.api.get("supercells", {})
        self.assertEqual([item["name"] for item in before], ["Asker"])
        self.assertIsNone(before[0]["evidence"])
        self.assertEqual(before[0]["parts"]["ask"]["answers"], 0)
        verdict, provider = self.run_lab()
        item = self.api.get("supercells", {})[0]
        ask = item["parts"]["ask"]
        self.assertEqual((ask["model"], ask["training_cutoff"], ask["answers"]), ("test-model", "2029-12-31", provider.calls))
        self.assertEqual(item["evidence"]["status"], verdict["status"])
        self.assertTrue(item["evidence"]["current_version"])
        self.assertIn("ask", item["evidence"]["contributions_pp"])
        self.assertEqual(item["evidence"]["model_contamination"], {"clean": 1, "contaminated": 0, "unknown": 0})
        # Cell cards name the asking model, so Home can mark the supercell.
        self.assertEqual(self.api.get("cells", {})[0]["asks"], "test-model")

    def test_the_app_grants_model_access_and_pins_the_script(self) -> None:
        script = self.root / "strategies" / "asker.py"
        granted = self.api.command("cell-ask-grant", {
            "id": self.cell["id"], "script": "strategies/asker.py", "connection_id": self.connection["id"],
            "max_calls": 40, "input_usd_per_million": 0, "output_usd_per_million": 0})
        grant = granted["spec"]["ask"]
        self.assertEqual(grant["approved_script_sha256"], hashlib.sha256(script.read_bytes()).hexdigest())
        self.assertEqual((grant["model"], grant["max_calls"], grant["mode"]), ("test-model", 40, "record"))
        # The tape is kept when the grant changes, so its budget history stays with the cell.
        self.assertEqual(grant["tape"], ".quantarium/tapes/asker.jsonl")
        self.assertEqual(self.api.cells.check(self.api.cells.get(self.cell["id"])), [])
        with self.assertRaisesRegex(Exception, "one of this cell's scripts"):
            self.api.command("cell-ask-grant", {"id": self.cell["id"], "script": "strategies/other.py",
                                                "connection_id": self.connection["id"]})

    def test_the_tape_viewer_and_progress_report_what_happened(self) -> None:
        updates = []
        original = self.api.lab._update

        def spy(experiment_id, **fields):
            if fields.get("progress"):
                updates.append(fields["progress"]["stage"])
            return original(experiment_id, **fields)

        with mock.patch.object(self.api.lab, "_update", spy):
            verdict, provider = self.run_lab(disguised_replay=True)
        self.assertIn("The candidate, its controls, and its variants", updates)
        self.assertIn("The disguised replay", updates)
        record = self.api.get("lab", {})[0]
        self.assertIsNone(record.get("progress"))
        tape = self.api.get(f"tapes/{self.cell['id']}", {})
        self.assertEqual(len(tape["entries"]), provider.calls)
        newest = tape["entries"][0]
        self.assertEqual((newest["status"], newest["answer"]), ("recorded", {"action": "invest"}))
        self.assertGreaterEqual(newest["day"], tape["entries"][-1]["day"])

    def test_lineage_follows_transformations(self) -> None:
        self.api.wait(self.api.submit("scenario-generate", {"output": "scenarios/made_up", "spec": {
            "name": "made_up", "seed": 1, "start_date": "2030-01-07", "symbols": ["AAA"],
            "segments": [{"sessions": 40, "annual_drift": 0.1, "annual_volatility": 0.2}]}})["id"])
        for source, output in (("scenarios/made_up", "scenarios/made_up_shocked"),
                               ("scenarios/history", "scenarios/history_shocked")):
            job = self.api.wait(self.api.submit("scenario-transform", {"source": source, "output": output, "spec": {
                "name": output.split("/")[1], "operations": [{"op": "add_drift", "annual_drift": 0.05}]}})["id"])
            self.assertEqual(job["status"], "succeeded", job["error"])
        lab = self.api.lab
        lineage = {key: lab._lineage(load_scenario(self.root / key)) for key in (
            "scenarios/made_up_shocked", "scenarios/history_shocked", "scenarios/history")}
        self.assertEqual(lineage, {"scenarios/made_up_shocked": "synthetic",
                                   "scenarios/history_shocked": "historical", "scenarios/history": "historical"})


class DisguiseAndAblationUnitTests(unittest.TestCase):
    def test_a_disguise_keeps_the_shape_and_drops_the_identity(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            copy, mapping = disguise_scenario(STEADY, Path(temp) / "d", seed="s", name="disguised_0")
            again, same = disguise_scenario(STEADY, Path(temp) / "e", seed="s", name="disguised_0")
            self.assertEqual(mapping, same)  # deterministic for one seed
            self.assertTrue(set(mapping.values()).isdisjoint({"AAA", "BBB"}))
            self.assertGreaterEqual(copy.start_date, date(2041, 1, 2))
            text = copy.prices_path.read_text(encoding="utf-8") + json.dumps(copy.provenance)
            self.assertNotIn("AAA", text)
            self.assertNotIn("steady", text.lower())

            def closes(path: Path, symbol: str) -> list[float]:
                with path.open(encoding="utf-8") as handle:
                    return [float(row["close"]) for row in csv.DictReader(handle) if row["symbol"] == symbol]

            original, disguised = closes(STEADY / "prices.csv", "AAA"), closes(copy.prices_path, mapping["AAA"])
            ratios = [later / earlier for earlier, later in zip(original, original[1:])]
            disguised_ratios = [later / earlier for earlier, later in zip(disguised, disguised[1:])]
            self.assertTrue(all(abs(a - b) < 1e-4 for a, b in zip(ratios, disguised_ratios)))

    def test_ablation_needs_the_part_it_switches_off_and_is_lab_only(self) -> None:
        base = {"name": "C", "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
                "strategies": [{"entrypoint": "x:Y", "allocation": 1}]}
        for ablation in (["ask"], ["risk"], ["other"], ["ask", "ask"]):
            with self.subTest(ablation=ablation), self.assertRaises(ValueError):
                _cells({"cells": [{**base, "ablation": ablation}]})
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            config = Path(temp) / "feed.json"
            config.write_text(json.dumps({
                "feed_id": "f", "start_date": "2030-01-07", "database_path": "f.sqlite",
                "provider": "scenario", "scenario_path": str(STEADY),
                "cells": [{**base, "model_card": "models/risk_0000000000000000.json", "ablation": ["risk"]}]}),
                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Lab variants only"):
                load_feed_config(config)


if __name__ == "__main__":
    unittest.main()
