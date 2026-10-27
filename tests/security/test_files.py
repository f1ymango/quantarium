"""v0.5.3: files and data under misuse.

Hostile or damaged files, of the kind a bad download, a careless edit, or a
broken disk produces. Parsers must refuse them with an ordinary error (a
ValueError, KeyError, TypeError, OSError, or QuantariumError), never another
exception, and the server must keep answering with a 4xx at worst.
"""

from __future__ import annotations

import json
import math
import os
import random
import unittest
from pathlib import Path

from quantarium.config import load_config
from quantarium.data import load_actions_csv, load_daily_csv
from quantarium.errors import QuantariumError
from quantarium.importer import read_vendor_csv
from quantarium.ml.card import load_card, save_card
from quantarium.scenario import load_scenario
from quantarium.suite import load_suite

from .harness import Fuzzer, MisuseCase, mutate_csv, mutate_json

SEED = int(os.environ.get("QUANTARIUM_FUZZ_SEED") or 2026)
ROUNDS = int(os.environ.get("QUANTARIUM_FUZZ_ROUNDS") or 6) * 10
EXPECTED = (ValueError, KeyError, TypeError, OSError, QuantariumError)
HOLD = "class Hold:\n    def on_bar(self, context):\n        return 1.0\n"


def scenario_spec(name: str = "plain") -> dict:
    return {"name": name, "seed": 5, "start_date": "2030-01-07", "symbols": ["AAA", "BBB"],
            "segments": [{"sessions": 30, "annual_drift": 0.1, "annual_volatility": 0.2}]}


class ParserFuzzTests(MisuseCase):
    def setUp(self) -> None:
        super().setUp()
        job = self.api.submit("scenario-generate", {"output": "scenarios/plain", "spec": scenario_spec()})
        self.assertEqual(self.api.wait(job["id"])["status"], "succeeded")
        self.scenario = self.root / "scenarios" / "plain"
        self.prices = (self.scenario / "prices.csv").read_text(encoding="utf-8")
        self.manifest = json.loads((self.scenario / "scenario.json").read_text(encoding="utf-8"))
        self.work = self.root / "fuzz"
        self.work.mkdir()

    def run_parser(self, failures: list, label: str, seed: int, call) -> object:
        try:
            return call()
        except EXPECTED:
            return None
        except Exception as error:  # anything else is a finding
            failures.append(f"{label} (seed {seed}): {type(error).__name__}: {str(error)[:120]}")
            return None

    def test_price_files(self) -> None:
        failures = []
        for round_ in range(ROUNDS):
            seed = SEED * 1000 + round_
            fuzzer = Fuzzer(seed, self.sentinel)
            path = self.work / f"prices-{round_}.csv"
            path.write_text(mutate_csv(self.prices, fuzzer), encoding="utf-8")
            loaded = self.run_parser(failures, "load_daily_csv", seed, lambda: load_daily_csv(path, {"AAA", "BBB"}))
            if loaded:
                for day in loaded[1].values():
                    for bar in day.values():
                        values = (bar.open, bar.high, bar.low, bar.close)
                        if not all(math.isfinite(value) and value > 0 for value in values) or bar.volume < 0:
                            failures.append(f"load_daily_csv (seed {seed}): accepted bar {bar}")
            vendor = self.work / f"vendor-{round_}.csv"
            vendor.write_text(mutate_csv("Date,Open,High,Low,Close,Adj Close,Volume\n"
                                         "2030-01-07,10,11,9,10.5,10.4,1000\n2030-01-08,10.5,11,10,10.8,10.7,1200\n",
                                         fuzzer), encoding="utf-8")
            self.run_parser(failures, "read_vendor_csv", seed, lambda: read_vendor_csv(vendor, "AAA"))
            actions = self.work / f"actions-{round_}.csv"
            actions.write_text(mutate_csv("date,symbol,kind,value\n2030-01-09,AAA,split,2\n2030-01-10,BBB,dividend,0.5\n",
                                          fuzzer), encoding="utf-8")
            sessions = set(loaded[0]) if loaded else {__import__("datetime").date(2030, 1, 9)}
            self.run_parser(failures, "load_actions_csv", seed,
                            lambda: load_actions_csv(actions, {"AAA", "BBB"}, sessions))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_scenario_manifests(self) -> None:
        failures = []
        for round_ in range(ROUNDS):
            seed = SEED * 1000 + 100 + round_
            folder = self.work / f"scenario-{round_}"
            folder.mkdir()
            (folder / "prices.csv").write_text(self.prices, encoding="utf-8")
            manifest = mutate_json(self.manifest, Fuzzer(seed, self.sentinel))
            (folder / "scenario.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.run_parser(failures, "load_scenario", seed, lambda: load_scenario(folder))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_configs_and_suites(self) -> None:
        (self.root / "strategies").mkdir()
        (self.root / "strategies" / "hold.py").write_text(HOLD, encoding="utf-8")
        cell = self.api.command("cell-create", {"name": "Holder"})
        self.api.command("cell-save", {"id": cell["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
            "sleeves": [{"script": "strategies/hold.py", "class": "Hold"}]}})
        static = self.api.wait(self.api.submit("deploy-static", {"cells": [cell["id"]],
                                                                  "data": "scenarios/plain/prices.csv"})["id"])
        suite = self.api.wait(self.api.submit("deploy-suite", {"cells": [cell["id"]],
                                                                "scenarios": ["scenarios/plain"]})["id"])
        self.assertEqual((static["status"], suite["status"]), ("succeeded", "succeeded"), (static, suite))
        failures = []
        for loader, key in ((load_config, static["result"]["config"]), (load_suite, suite["result"]["config"])):
            source = self.root / key
            original = json.loads(source.read_text(encoding="utf-8"))
            for round_ in range(ROUNDS):
                seed = SEED * 1000 + 200 + round_
                path = source.with_name(f"fuzz-{round_}-{source.name}")
                path.write_text(json.dumps(mutate_json(original, Fuzzer(seed, self.sentinel))), encoding="utf-8")
                self.run_parser(failures, loader.__name__, seed, lambda: loader(path))
                path.unlink()
        self.assertEqual(failures, [], "\n".join(failures))

    def test_model_cards(self) -> None:
        from test_ml_card import example_card

        failures = []
        original = example_card("2029-12-31")
        for round_ in range(ROUNDS):
            seed = SEED * 1000 + 300 + round_
            card = mutate_json(original, Fuzzer(seed, self.sentinel))
            try:
                path = save_card(self.work / f"cards-{round_}", card)
            except EXPECTED:
                continue
            except Exception as error:
                failures.append(f"save_card (seed {seed}): {type(error).__name__}: {error}")
                continue
            self.run_parser(failures, "load_card", seed, lambda: load_card(path))
        self.assertEqual(failures, [], "\n".join(failures))


class DamagedWorkspaceTests(MisuseCase):
    """Damaged files in the workspace: the server keeps answering, and agent checks fail closed."""

    def test_damaged_files_never_break_reads(self) -> None:
        job = self.api.submit("scenario-generate", {"output": "scenarios/plain", "spec": scenario_spec()})
        self.assertEqual(self.api.wait(job["id"])["status"], "succeeded")
        cell = self.api.command("cell-create", {"name": "Good"})
        rng = random.Random(SEED)
        damaged = {
            "cells/c_broken.json": "{not json", "cells/c_list.json": "[1, 2]",
            "cells/c_spec.json": json.dumps({"format": "quantarium-cell-v1", "id": "c_spec", "name": 5, "spec": [],
                                             "history": {}, "deployments": "x"}),
            "scenarios/bad/scenario.json": "[]", "scenarios/bad2/scenario.json": "{\"format\": 1}",
            "scenarios/bad3/prices.csv": "date\n", "configs/bad.json": "{\"cells\": 5}",
            "strategies/broken.py": "def (:\n", "strategies/binary.py": bytes(rng.randrange(256) for _ in range(200)),
            "runs/bad.sqlite": b"SQLite format 3\x00" + bytes(100),
        }
        for key, content in damaged.items():
            path = self.root / key
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        reads = ["workspace", "cells", "scripts", "arena", "lab", "supercells", f"cells/{cell['id']}",
                 "cells/c_broken", "cells/c_spec", "scenario?key=scenarios/bad", "report?db=runs/bad.sqlite",
                 "scripts/strategies/binary.py", "config?key=configs/bad.json", "analysis?db=runs/bad.sqlite"]
        crashed = []
        for route in reads:
            for token in (None, self.token):
                status, body = self.call("GET", f"/api/v1/{route}", token=token)
                if status >= 500:
                    crashed.append(f"{'agent' if token else 'browser'} {route}: {status} {str(body)[:120]}")
        self.assertEqual(crashed, [])

    def test_damaged_agent_settings_fail_closed(self) -> None:
        settings = self.root / ".quantarium" / "agents.json"
        for content in ("{not json", "[]", json.dumps({"enabled": True, "connections": "x"}),
                        json.dumps({"enabled": "yes", "connections": [{"token_sha256": 5, "tiers": "manage"}]})):
            settings.write_text(content, encoding="utf-8")
            status, body = self.agent("get", "cells")
            self.assertIn(status, {401, 403}, f"{content[:40]}: {status} {body}")
            status, body = self.agent("command", "cell-create", {"name": "x"})
            self.assertIn(status, {401, 403}, f"{content[:40]}: {status} {body}")
            self.assertLess(self.call("GET", "/api/v1/agents")[0], 500, content[:40])

    def test_damaged_tapes_never_switch_a_budget_off(self) -> None:
        from quantarium.ask import AskGateway, tape_summary

        tape = self.root / ".quantarium" / "tapes" / "cell.jsonl"
        tape.parent.mkdir(parents=True)
        good = {"key": "a", "status": "recorded", "input_tokens": 10, "output_tokens": 5, "estimated_cost_usd": 0.01}
        lines = [json.dumps(good), "{damaged", "[]",
                 '{"key": "b", "status": "recorded", "estimated_cost_usd": NaN}',
                 json.dumps({"key": "c", "status": "failed", "input_tokens": -500}),
                 json.dumps({"key": "d", "status": "recorded", "output_tokens": "many"}),
                 json.dumps({"key": "e", "status": "recorded", "estimated_cost_usd": 1e308})]
        tape.write_text("\n".join(lines) + "\n", encoding="utf-8")
        summary = tape_summary(self.root, ".quantarium/tapes/cell.jsonl")
        self.assertTrue(all(math.isfinite(value) and value >= 0 for value in summary.values()), summary)
        grant = {"connection_id": "x", "model": "m", "workspace": str(self.root), "tape": ".quantarium/tapes/cell.jsonl",
                 "mode": "record", "approved_script_sha256": "0" * 64, "max_calls": 5, "max_tokens": 1000,
                 "max_estimated_cost_usd": 1, "input_usd_per_million": 1, "output_usd_per_million": 1,
                 "min_sessions_between_calls": 1, "max_output_tokens": 30}
        for line in lines[1:6]:
            tape.write_text(json.dumps(good) + "\n" + line + "\n", encoding="utf-8")
            with self.subTest(line=line[:40]), self.assertRaises(ValueError):
                AskGateway(grant, self.root)

    def test_damaged_records_fail_closed(self) -> None:
        cell = self.api.command("cell-create", {"name": "Good"})
        (self.root / "strategies" / "agent").mkdir(parents=True)
        self.assertEqual(self.agent("command", "script-write", {"key": "strategies/agent/a.py", "source": HOLD})[0], 200)
        registry = self.root / ".quantarium" / "script-provenance.json"
        registry.write_text("{damaged", encoding="utf-8")
        # With provenance unreadable, the agent's script must not run as the user's own code.
        self.api.command("cell-save", {"id": cell["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
            "sleeves": [{"script": "strategies/agent/a.py", "class": "Hold"}]}})
        job = self.api.submit("scenario-generate", {"output": "scenarios/plain", "spec": scenario_spec()})
        self.assertEqual(self.api.wait(job["id"])["status"], "succeeded")
        status, job = self.call("POST", "/api/v1/jobs", {"action": "deploy-static", "params": {
            "cells": [cell["id"]], "data": "scenarios/plain/prices.csv"}})
        if status == 202:
            self.assertEqual(self.api.wait(job["id"])["status"], "failed")
        for name in ("lab.json", "proposals.json", "holdout.json"):
            for path in (self.root / ".quantarium").glob(name):
                path.write_text("{damaged", encoding="utf-8")
        for route in ("lab", "agents/proposals", "workspace"):
            self.assertLess(self.call("GET", f"/api/v1/{route}")[0], 500, route)


if __name__ == "__main__":
    unittest.main()
