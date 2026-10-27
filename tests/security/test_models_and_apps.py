"""v0.5.4: model connections, the ask gateway, and agent apps under misuse.

Model APIs and agent apps are fakes on this computer; no test contacts a
real service or uses a real key.
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from quantarium import presets
from quantarium.ask import AskGateway

from .harness import MisuseCase

HOSTILE_ACP = Path(__file__).with_name("hostile_acp.py")


class EndpointTests(unittest.TestCase):
    """Keys go only to HTTPS endpoints, or plain HTTP on this computer, named plainly."""

    def test_endpoints_that_could_carry_a_key_elsewhere_are_refused(self) -> None:
        refused = ["http://evil.example/v1", "http://localhost@evil.example/v1", "http://evil.example#@localhost/v1",
                   "http://evil.example?@localhost", "https://user:pass@api.example.com/v1",
                   "http://127.0.0.1.evil.example/v1", "http://localhost.evil.example/v1", "http://0.0.0.0:11434/v1",
                   "ftp://localhost/v1", "file:///etc/passwd", "javascript:alert(1)", "http://evil.localhost/v1",
                   "http://[::ffff:8.8.8.8]/v1", "https://api.example.com/v1\nHost: evil", "http:/\\evil.example",
                   "https://api.example.com/v1 ", "http://loca​lhost/v1"]
        accepted = [url for url in refused if self.accepts(url)]
        self.assertEqual(accepted, [])

    def test_ordinary_endpoints_still_work(self) -> None:
        for url in ("https://api.example.com/v1", "http://localhost:11434/v1", "http://127.0.0.1:1234/v1",
                    "http://[::1]:8080/v1", "https://openrouter.ai/api/v1"):
            self.assertTrue(self.accepts(url), url)

    @staticmethod
    def accepts(url: str) -> bool:
        try:
            presets.check_endpoint(url)
            return True
        except ValueError:
            return False


class HostileModel:
    """A fake model API that reports odd usage and returns answers that do not fit the schema."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system, messages, tools, max_tokens):
        self.calls += 1
        return self.replies.pop(0) if self.replies else {"text": "{}", "usage": {}}


class GatewayTests(MisuseCase):
    def grant(self, **patch) -> dict:
        connection = self.api.agents.create_connection("Model", kind="direct", provider={
            "protocol": "openai-chat", "endpoint": "http://127.0.0.1:9/v1", "model": "m", "key_store": "none"})
        return {"connection_id": connection["connection"]["id"] if "connection" in connection else connection["id"],
                "model": "m", "workspace": str(self.root), "tape": ".quantarium/tapes/cell.jsonl", "mode": "record",
                "approved_script_sha256": "0" * 64, "max_calls": 3, "max_tokens": 10_000,
                "max_estimated_cost_usd": 1, "input_usd_per_million": 1, "output_usd_per_million": 1,
                "min_sessions_between_calls": 1, "max_output_tokens": 30, **patch}

    def test_crafted_answers_never_pass_the_schema(self) -> None:
        schema = {"type": "object", "properties": {"weight": {"type": "number", "minimum": 0, "maximum": 1}},
                  "required": ["weight"], "additionalProperties": False}
        replies = [{"text": text, "usage": {"input_tokens": 1, "output_tokens": 1}} for text in (
            '{"weight": 5}', '{"weight": "1"}', '{"weight": NaN}', '{"weight": 0.5, "extra": "ignore previous"}',
            '{"weight": 1e999}', '[0.5]', '{"weight": true}', '```json\n{"weight": 0.5}\n```')]
        gateway = AskGateway(self.grant(max_calls=len(replies)), self.root, provider=HostileModel(replies))
        accepted = []
        for session in range(len(replies)):
            try:
                accepted.append(gateway.ask(f"day {session}", schema, day="2030-01-07", session_number=session * 2 + 1))
            except ValueError:
                pass
        self.assertEqual(accepted, [], "answers outside the schema reached a strategy")

    def test_a_model_cannot_dodge_the_budget_with_odd_usage(self) -> None:
        schema = {"type": "object", "properties": {"weight": {"type": "number"}}, "required": ["weight"]}
        replies = [{"text": '{"weight": 0.5}', "usage": {"input_tokens": value, "output_tokens": value}}
                   for value in ("lots", -10**9, float("nan"), None, [1], 10**12)]
        model = HostileModel(replies)
        gateway = AskGateway(self.grant(max_calls=3), self.root, provider=model)
        for session in range(len(replies)):
            try:
                gateway.ask(f"day {session}", schema, day="2030-01-07", session_number=session * 2 + 1)
            except ValueError:
                pass
        self.assertLessEqual(model.calls, 3, "the model was called more often than the budget allows")
        self.assertGreaterEqual(gateway.cost, 0)


class AgentAppTests(MisuseCase):
    def setUp(self) -> None:
        super().setUp()
        self.record = Path(self.temp.name) / "hostile.jsonl"
        self.connection = self.api.command("agent-connection-create", {
            "name": "Hostile app", "kind": "bridge",
            "provider": {"runtime": "custom-acp", "command": [sys.executable, str(HOSTILE_ACP)]}})["connection"]

    def turn(self, mode: str, timeout: float = 60) -> dict:
        canaries = ",".join(value for name, value in self.canaries.values.items() if name.startswith("env:"))
        with mock.patch.dict(os.environ, {"HOSTILE_ACP_MODE": mode, "HOSTILE_ACP_RECORD": str(self.record),
                                          "HOSTILE_ACP_LOOK_FOR": canaries}):
            chat = self.api.command("chat-create", {"connection_id": self.connection["id"]})
            self.api.command("chat-send", {"id": chat["id"], "text": "Hello"})
            deadline = time.monotonic() + timeout
            while self.api.assistant.busy(chat["id"]) and time.monotonic() < deadline:
                time.sleep(0.05)
        self.assertFalse(self.api.assistant.busy(chat["id"]), "the turn never ended")
        return self.api.get(f"agents/chats/{chat['id']}", {})

    def records(self) -> list[dict]:
        if not self.record.is_file():
            return []
        return [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines()]

    @staticmethod
    def texts(chat: dict) -> list[str]:
        return [block.get("text", "") for message in chat["messages"] if message["role"] == "assistant"
                for block in message["content"] if block.get("type") == "text"]

    def test_a_disguised_tool_is_refused(self) -> None:
        chat = self.turn("spoof")
        permission = next(item["permission"] for item in self.records() if "permission" in item)
        self.assertEqual(permission["outcome"].get("optionId"), "no", "a disguised tool was allowed")
        self.assertTrue(any("stopped this turn" in text for text in self.texts(chat)), self.texts(chat))

    def test_a_flood_is_cut_off(self) -> None:
        chat = self.turn("flood")
        kept = sum(len(text) for text in self.texts(chat))
        self.assertLess(kept, 300_000, "the chat kept every byte of a flood")

    def test_malformed_messages_are_answered_and_the_turn_survives(self) -> None:
        chat = self.turn("malformed")
        records = {key: value for item in self.records() for key, value in item.items()}
        self.assertIn("error", records["read"])
        self.assertIn("error", records["terminal"])
        # A malformed permission request grants nothing.
        self.assertNotEqual((records["list_params"].get("result") or {}).get("outcome", {}).get("outcome"), "selected")
        self.assertNotIn("Internal", json.dumps(chat.get("error")))
        usage = chat.get("usage") or {}
        self.assertTrue(all(isinstance(value, int) and value >= 0 for value in usage.values()), usage)

    def test_a_malformed_unknown_tool_stops_the_turn(self) -> None:
        with mock.patch.dict(os.environ, {"HOSTILE_ACP_BAD_TOOL": "1"}):
            chat = self.turn("malformed")
        self.assertTrue(any("stopped this turn" in text for text in self.texts(chat)), self.texts(chat))

    def test_agent_apps_get_no_secrets_and_an_empty_folder(self) -> None:
        self.turn("environment")
        report = next(item for item in self.records() if "leaked" in item)
        # The fake's own instructions name the canaries; nothing else may hold them.
        self.assertEqual([name for name in report["leaked"] if not name.startswith("HOSTILE_ACP_")], [])
        self.assertEqual(report["folder"], [])


if __name__ == "__main__":
    unittest.main()
