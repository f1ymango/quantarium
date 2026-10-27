"""The model gateway spends only on approved scripts and replays its tape."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quantarium.agents import AgentSettings, NOTICE_VERSION
from quantarium.api import Api
from quantarium.ask import AskGateway
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
SCHEMA = {"type": "object", "properties": {
    "choice": {"type": "string", "enum": ["hold", "buy"], "maxLength": 4},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
}, "required": ["choice", "confidence"], "additionalProperties": False}


class FakeProvider:
    def __init__(self, answer: str = '{"choice":"hold","confidence":0.7}') -> None:
        self.answer = answer
        self.calls = 0

    def complete(self, system, messages, tools, max_tokens):
        self.calls += 1
        return {"content": [{"type": "text", "text": self.answer}],
                "usage": {"input_tokens": 20, "output_tokens": 10}}


class AskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        settings = AgentSettings(self.workspace)
        settings.enable("I understand and accept", NOTICE_VERSION)
        self.connection = settings.create_connection("Local mock", kind="direct", provider={
            "preset": "custom", "protocol": "openai-chat", "endpoint": "http://localhost:1234/v1",
            "model": "test-model", "key_store": "none",
        })["connection"]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def grant(self, script_hash: str, **patch) -> dict:
        return {"connection_id": self.connection["id"], "model": "test-model",
                "workspace": str(self.root), "tape": ".quantarium/tapes/cell.jsonl",
                "mode": "record", "approved_script_sha256": script_hash,
                "max_calls": 1, "max_tokens": 100, "max_estimated_cost_usd": 1,
                "input_usd_per_million": 1, "output_usd_per_million": 1,
                "min_sessions_between_calls": 1, "max_output_tokens": 30, **patch}

    def test_record_cache_replay_and_schema_guard(self) -> None:
        grant = self.grant("0" * 64)
        provider = FakeProvider()
        gateway = AskGateway(grant, self.root, provider=provider)
        first = gateway.ask("What should this cell do?", SCHEMA, day="2030-01-07", session_number=1)
        second = gateway.ask("What should this cell do?", SCHEMA, day="2030-01-08", session_number=2)
        self.assertEqual(first, second)
        self.assertEqual(provider.calls, 1)
        replay = AskGateway({**grant, "mode": "replay"}, self.root)
        self.assertEqual(replay.ask("What should this cell do?", SCHEMA,
                                    day="2030-02-01", session_number=20), first)
        with self.assertRaisesRegex(ValueError, "no answer"):
            replay.ask("A different question", SCHEMA, day="2030-02-01", session_number=20)
        with self.assertRaisesRegex(ValueError, "budget"):
            gateway.ask("A different question", SCHEMA, day="2030-01-09", session_number=3)
        bad = AskGateway(self.grant("1" * 64, tape=".quantarium/tapes/bad.jsonl"),
                         self.root, provider=FakeProvider('{"choice":"sell","confidence":0.7}'))
        with self.assertRaisesRegex(ValueError, "allowed choices"):
            bad.ask("Do something", SCHEMA, day="2030-01-07", session_number=1)

    def test_a_reviewed_script_can_ask_through_a_cell(self) -> None:
        script = self.root / "strategies/asker.py"
        script.parent.mkdir()
        script.write_text("class Asker:\n"
                          "    def on_bar(self, context):\n"
                          "        answer = context.ask('One fixed question', " + repr(SCHEMA) + ")\n"
                          "        return 1.0 if answer['choice'] == 'buy' else 0.0\n",
                          encoding="utf-8")
        data = self.root / "data/prices.csv"
        data.parent.mkdir()
        data.write_bytes((ROOT / "examples/sample_daily.csv").read_bytes())
        digest = hashlib.sha256(script.read_bytes()).hexdigest()
        api = Api(self.workspace)
        try:
            record = api.command("cell-create", {"name": "Asking cell"})
            api.command("cell-save", {"id": record["id"], "spec": {
                "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000,
                "ask": self.grant(digest),
                "sleeves": [{"script": "strategies/asker.py", "class": "Asker",
                             "allocation": 1, "parameters": {}}],
            }})
            provider = FakeProvider('{"choice":"buy","confidence":0.7}')
            with mock.patch("quantarium.ask.provider_for", return_value=provider):
                job = api.wait(api.submit("deploy-static", {"cells": [record["id"]],
                                                             "data": "data/prices.csv"})["id"])
            self.assertEqual(job["status"], "succeeded", job["error"])
            self.assertEqual(provider.calls, 1)
            run = job["result"]
            timeline = api.get("timeline", {"db": run["db"], "kind": "static",
                                            "id": run["id"], "cell": "Asking cell"})
            answers = [event for day in timeline["days"] for event in day["other_events"]
                       if event["kind"] == "model_answer"]
            self.assertTrue(answers)
            self.assertEqual(answers[0]["payload"]["answer"]["choice"], "buy")
            script.write_text(script.read_text(encoding="utf-8") + "# edit\n", encoding="utf-8")
            self.assertTrue(any("approved" in problem for problem in api.get("cells", {})[0]["problems"]))
        finally:
            api.close()

    def test_running_out_of_output_tokens_says_so(self) -> None:
        class Thinker(FakeProvider):
            def complete(self, system, messages, tools, max_tokens):
                self.calls += 1
                return {"content": [{"type": "text", "text": "The model returned an empty reply."}],
                        "stop": "length", "usage": {"input_tokens": 20, "output_tokens": 30}}

        gateway = AskGateway(self.grant("0" * 64, max_calls=5), self.root, provider=Thinker())
        with self.assertRaisesRegex(ValueError, "used all 30 output tokens"):
            gateway.ask("Question", SCHEMA, day="2030-01-07", session_number=1)

    def test_failed_answers_still_count_against_the_budget(self) -> None:
        provider = FakeProvider('{"choice":"sell","confidence":0.7}')  # outside the schema's choices
        grant = self.grant("0" * 64, max_calls=2, max_tokens=1000)
        gateway = AskGateway(grant, self.root, provider=provider)
        for session in (1, 2):
            with self.assertRaisesRegex(ValueError, "allowed choices"):
                gateway.ask(f"Question {session}", SCHEMA, day="2030-01-07", session_number=session)
        with self.assertRaisesRegex(ValueError, "budget is exhausted"):
            gateway.ask("Question 3", SCHEMA, day="2030-01-09", session_number=3)
        self.assertEqual(provider.calls, 2)
        # A later run reads the same spending from the tape, and a failure is never replayed as an answer.
        later = AskGateway(grant, self.root, provider=FakeProvider())
        self.assertEqual((later.calls, later.tokens), (2, 60))
        replay = AskGateway({**grant, "mode": "replay"}, self.root)
        with self.assertRaisesRegex(ValueError, "no answer"):
            replay.ask("Question 1", SCHEMA, day="2030-01-07", session_number=1)

    def test_the_rhythm_restarts_with_each_run(self) -> None:
        grant = self.grant("0" * 64, max_calls=5, max_tokens=1000, min_sessions_between_calls=5)
        first_run = AskGateway(grant, self.root, provider=FakeProvider())
        first_run.ask("Day 250 question", SCHEMA, day="2030-12-30", session_number=250)
        # A new run starts again at session 1; it must not wait for session 255.
        second_run = AskGateway(grant, self.root, provider=FakeProvider())
        second_run.ask("A new run's first question", SCHEMA, day="2031-01-02", session_number=1)
        with self.assertRaisesRegex(ValueError, "rhythm"):
            second_run.ask("Too soon", SCHEMA, day="2031-01-03", session_number=2)

    def test_thinking_blocks_and_a_code_fence_are_accepted(self) -> None:
        class Thinking(FakeProvider):
            def complete(self, system, messages, tools, max_tokens):
                self.calls += 1
                return {"content": [{"type": "thinking", "thinking": "Let me see.", "signature": "s"},
                                    {"type": "text", "text": '```json\n{"choice":"buy","confidence":0.4}\n```'}],
                        "usage": {"input_tokens": 20, "output_tokens": 10}}

        gateway = AskGateway(self.grant("0" * 64), self.root, provider=Thinking())
        self.assertEqual(gateway.ask("Question", SCHEMA, day="2030-01-07", session_number=1),
                         {"choice": "buy", "confidence": 0.4})

    def test_no_tools_are_offered_to_the_model(self) -> None:
        provider = FakeProvider()
        seen = []
        original = provider.complete
        provider.complete = lambda system, messages, tools, max_tokens: (seen.append(tools),
                                                                         original(system, messages, tools, max_tokens))[1]
        AskGateway(self.grant("0" * 64), self.root, provider=provider).ask(
            "Question", SCHEMA, day="2030-01-07", session_number=1)
        self.assertEqual(seen, [[]])

    def test_a_script_an_agent_wrote_needs_review_before_it_may_ask(self) -> None:
        from quantarium.provenance import record_agent_write

        script = self.root / "strategies/agent/asker.py"
        script.parent.mkdir(parents=True)
        source = b"class Asker:\n    def on_bar(self, context):\n        return 1.0\n"
        script.write_bytes(source)
        record_agent_write(self.root, "strategies/agent/asker.py", source, {"id": "a_1", "name": "Agent"})
        api = Api(self.workspace)
        try:
            record = api.command("cell-create", {"name": "Agent asker"})
            api.command("cell-save", {"id": record["id"], "spec": {
                "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000,
                "ask": self.grant(hashlib.sha256(source).hexdigest()),
                "sleeves": [{"script": "strategies/agent/asker.py", "class": "Asker",
                             "allocation": 1, "parameters": {}}]}})
            problems = api.cells.check(api.cells.get(record["id"]))
            self.assertTrue(any("reviewed before it may use context.ask" in problem for problem in problems))
        finally:
            api.close()


if __name__ == "__main__":
    unittest.main()
