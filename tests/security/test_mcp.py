"""v0.5.1: the MCP server under malformed and hostile JSON-RPC messages."""

from __future__ import annotations

import io
import json
import os
import unittest

from quantarium import mcp

from .harness import Fuzzer, MisuseCase

SEED = int(os.environ.get("QUANTARIUM_FUZZ_SEED") or 2026)
ROUNDS = int(os.environ.get("QUANTARIUM_FUZZ_ROUNDS") or 6)


class McpMisuseTests(MisuseCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = mcp.QuantariumClient(self.server.url, self.token)

    def reply(self, message: object) -> list[dict]:
        """Send one line to the stdio server and return its replies; the server must survive it."""
        output = io.StringIO()
        line = message if isinstance(message, str) else json.dumps(message)
        mcp.serve_stdio(self.client, io.StringIO(line + "\n" + json.dumps({"jsonrpc": "2.0", "id": "after",
                                                                              "method": "ping"}) + "\n"), output)
        replies = [json.loads(text) for text in output.getvalue().splitlines()]
        self.responses.extend(replies)
        self.assertEqual(replies[-1], {"jsonrpc": "2.0", "id": "after", "result": {}}, "the server stopped answering")
        return replies[:-1]

    def test_malformed_messages_get_errors_not_crashes(self) -> None:
        messages = ["not json", "[]", "null", "{\"id\": 1}", {"id": 1, "method": "tools/call", "params": []},
                    {"id": 1, "method": "tools/call", "params": "x"},
                    {"id": 1, "method": "tools/call", "params": {"name": "workspace_overview", "arguments": []}},
                    {"id": 1, "method": "tools/call", "params": {"name": ["workspace_overview"]}},
                    {"id": 1, "method": "tools/call", "params": {"name": "workspace_overview", "arguments": "x"}},
                    {"id": 1, "method": "initialize", "params": []}, {"id": {"nested": 1}, "method": 5},
                    "[" * 100_000]
        for message in messages:
            with self.subTest(message=str(message)[:60]):
                self.reply(message)

    def test_every_tool_survives_hostile_arguments(self) -> None:
        failures = []
        for index, (name, tool) in enumerate(mcp.TOOLS.items()):
            keys = list(tool.schema.get("properties", {}))
            for round_ in range(ROUNDS):
                seed = SEED * 1000 + 300 + index * 10 + round_
                arguments = Fuzzer(seed, self.sentinel).params(keys)
                try:
                    replies = self.reply({"jsonrpc": "2.0", "id": seed, "method": "tools/call",
                                          "params": {"name": name, "arguments": arguments}})
                except AssertionError as error:
                    failures.append(f"{name} (seed {seed}): {error}")
                    continue
                text = json.dumps(replies)
                if "Internal error" in text:
                    failures.append(f"{name} (seed {seed}): {text[:200]}")
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
