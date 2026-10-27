"""Model costs: the dated price table and daily cost limits per connection (v0.7.2)."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from quantarium import presets
from quantarium.agents import NOTICE_VERSION, AgentSettings
from quantarium.api import Api, ApiError
from quantarium.ask import AskGateway, spent_today
from quantarium.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
SCRIPT = "class Asker:\n    def on_bar(self, context):\n        return 1.0\n"


class PriceTableTests(unittest.TestCase):
    def test_the_table_is_dated_sourced_and_sane(self) -> None:
        table = presets.prices()
        date.fromisoformat(table["checked"])
        self.assertTrue(all(url.startswith("https://") for url in table["sources"].values()))
        known = {item["id"] for item in presets.load()["api"]}
        for item in table["prices"]:
            with self.subTest(item["model"]):
                self.assertIn(item["preset"], known)
                for key in ("input_usd_per_million", "output_usd_per_million"):
                    self.assertTrue(math.isfinite(item[key]) and item[key] >= 0)
        self.assertEqual(presets.price_for("anthropic", "claude-opus-5-5")["output_usd_per_million"], 20.0)
        self.assertEqual(presets.price_for("ollama", "anything")["input_usd_per_million"], 0.0)
        self.assertIsNone(presets.price_for("openai", "no-such-model"))


class GrantPriceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        settings = AgentSettings(Workspace(self.root))
        settings.enable("I understand and accept", NOTICE_VERSION)
        self.listed = settings.create_connection("Claude", kind="direct", provider={
            "preset": "anthropic", "model": "claude-opus-5-5", "key_env": "QUANTARIUM_TEST_KEY"})["connection"]
        self.unlisted = settings.create_connection("Custom", kind="direct", provider={
            "preset": "custom", "protocol": "openai-chat", "endpoint": "https://models.example.com/v1",
            "model": "mystery", "key_env": "QUANTARIUM_TEST_KEY"})["connection"]
        (self.root / "strategies").mkdir()
        (self.root / "strategies" / "asker.py").write_text(SCRIPT, encoding="utf-8")
        self.api = Api(Workspace(self.root))
        record = self.api.command("cell-create", {"name": "Asker"})
        self.cell = self.api.command("cell-save", {"id": record["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000,
            "sleeves": [{"script": "strategies/asker.py", "class": "Asker"}]}})

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def grant(self, connection: dict, **extra) -> dict:
        return self.api.command("cell-ask-grant", {"id": self.cell["id"], "script": "strategies/asker.py",
                                                   "connection_id": connection["id"], **extra})["spec"]["ask"]

    def test_prices_come_from_the_table_unless_given(self) -> None:
        listed = self.grant(self.listed)
        self.assertEqual((listed["input_usd_per_million"], listed["output_usd_per_million"]), (4.0, 20.0))
        given = self.grant(self.listed, input_usd_per_million=1, output_usd_per_million=2)
        self.assertEqual((given["input_usd_per_million"], given["output_usd_per_million"]), (1, 2))
        with self.assertRaises(ApiError):
            self.grant(self.unlisted)
        self.assertEqual(self.grant(self.unlisted, input_usd_per_million=3, output_usd_per_million=9)["model"], "mystery")

    def test_a_daily_limit_stops_calls_across_cells(self) -> None:
        self.api.command("agent-connection-daily-limit", {"id": self.listed["id"], "usd": 0.5})
        with self.assertRaises(ValueError):
            self.api.command("agent-connection-daily-limit", {"id": self.listed["id"], "usd": -1})
        # Another cell's tape already spent 60 cents today with this connection.
        tapes = self.root / ".quantarium" / "tapes"
        tapes.mkdir(parents=True)
        today = datetime.now(timezone.utc).isoformat()
        (tapes / "other.jsonl").write_text(json.dumps({
            "key": "k", "status": "recorded", "connection_id": self.listed["id"], "created_utc": today,
            "estimated_cost_usd": 0.6}) + "\n" + json.dumps({
            "key": "old", "status": "recorded", "connection_id": self.listed["id"], "created_utc": "2020-01-01T00:00:00+00:00",
            "estimated_cost_usd": 99}) + "\n", encoding="utf-8")
        self.assertAlmostEqual(spent_today(self.root, self.listed["id"]), 0.6)
        grant = self.grant(self.listed)

        class NeverCalled:
            def complete(self, *args, **kwargs):
                raise AssertionError("the model was called past the daily limit")

        gateway = AskGateway(grant, self.root, provider=NeverCalled())
        with self.assertRaisesRegex(ValueError, "daily cost limit"):
            gateway.ask("hello", {"type": "object", "properties": {"x": {"type": "number"}}}, day="2030-01-07",
                        session_number=1)


if __name__ == "__main__":
    unittest.main()
