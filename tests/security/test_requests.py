"""v0.5.1: requests and permissions under misuse.

Seeded fuzzing of every agent operation and browser request, plus targeted
checks of the browser boundary. A failure names its operation and seed, so
it can be replayed. Set QUANTARIUM_FUZZ_SEED or QUANTARIUM_FUZZ_ROUNDS to explore
further than the default run.
"""

from __future__ import annotations

import os
import socket
import time
import unittest
from unittest import mock
from urllib.parse import quote, urlencode

from quantarium import agents, server

from .harness import MisuseCase, Fuzzer, hostile_strings

SEED = int(os.environ.get("QUANTARIUM_FUZZ_SEED") or 2026)
ROUNDS = int(os.environ.get("QUANTARIUM_FUZZ_ROUNDS") or 6)
PARAM_KEYS = ["action", "actions", "archived", "arguments", "benchmark", "budgets", "cell_id", "cells", "config",
              "confirmation", "connection", "connection_id", "data", "db", "decision", "description",
              "disguised_replay", "edit_user_scripts", "evidence", "from", "holdout", "id", "idea", "ids", "key",
              "kind", "limit", "mode", "model", "name", "note", "notice_version", "output", "overwrite", "params",
              "pass_rule", "provider", "rationale", "reason", "scenarios", "scope", "script", "source", "spec",
              "start_date", "status", "step", "text", "through", "tiers", "title", "training_cutoff", "wait",
              "inbox", "date", "weights", "symbols", "orders", "decisions"]
BROWSER_GETS = ["health", "workspace", "analysis", "timeline", "report", "config", "scenario", "suite-report",
                "jobs", "cells", "scripts", "holdings", "arena", "supercells", "lab", "onboarding", "agents",
                "agents/chats", "agents/proposals", "agents/scripts", "agents/log"]
BROWSER_PREFIXES = ["jobs/", "cells/", "lab/", "tapes/", "scripts/", "agents/chats/"]
# chat-send is left out: it starts an agent app or a model call, which these tests never do.
BROWSER_COMMANDS = ["onboarding-step", "onboarding-finish", "agents-pause", "agents-resume",
                    "agent-connection-create", "agent-connection-revoke", "agent-connection-key",
                    "agent-connection-model", "agent-connection-cutoff", "agent-connection-test", "script-review",
                    "scenario-holdout", "proposal-create", "proposal-decide", "lab-register", "script-write",
                    "script-check", "cell-create", "cell-ask-grant", "cell-save", "cell-duplicate", "cell-delete",
                    "cell-archive", "cell-restore", "chat-create", "chat-rename", "chat-stop", "chat-delete",
                    "agents-disable", "agents-enable", "onboarding-start"]


class FuzzCase(MisuseCase):
    def check(self, failures: list, label: str, seed: int, status: int, body: object) -> None:
        if status >= 500:
            failures.append(f"{label} (seed {seed}): {status} {str(body)[:200]}")

    def report(self, failures: list) -> None:
        self.assertEqual(failures, [], "requests that crashed instead of refusing cleanly:\n" + "\n".join(failures))


class AgentFuzzTests(FuzzCase):
    """A hostile agent with every tier sends random parameters to every operation."""

    def test_every_agent_operation_refuses_cleanly(self) -> None:
        failures: list[str] = []
        operations = [(kind, name) for kind, names in agents.OPERATIONS.items() for name in names]
        for index, (kind, name) in enumerate(operations):
            for round_ in range(ROUNDS):
                seed = SEED * 1000 + index * 100 + round_
                params = Fuzzer(seed, self.sentinel).params(PARAM_KEYS)
                if kind == "get":
                    params = {key: str(value)[:2000] for key, value in params.items()}
                status, body = self.agent(kind, name, params)
                self.check(failures, f"agent {kind} {name}", seed, status, body)
        self.report(failures)

    def test_every_agent_prefix_route_refuses_cleanly(self) -> None:
        failures: list[str] = []
        for prefix in agents.PREFIX_ROUTES:
            for value in hostile_strings(self.sentinel):
                status, body = self.call("GET", f"/api/v1/{prefix}{quote(value[:500], safe='/')}", token=self.token)
                self.check(failures, f"agent get {prefix}{value[:40]!r}", 0, status, body)
        self.report(failures)

    def test_hidden_and_outside_paths_are_refused_for_every_path_parameter(self) -> None:
        paths = [".quantarium/agents.json", ".quantarium/agent-log.jsonl", "../outside/sentinel.txt", str(self.sentinel),
                 "scenarios/../../outside/sentinel.txt"]
        allowed = []
        for route in ("report", "config", "scenario", "analysis", "timeline", "holdings", "suite-report"):
            for key in agents.PATH_PARAMS:
                for path in paths:
                    status, body = self.agent("get", route, {key: path})
                    if status < 400:
                        allowed.append(f"{route} {key}={path}: {status}")
        self.assertEqual(allowed, [])


class BrowserFuzzTests(FuzzCase):
    """The browser's own requests are trusted, but must still fail cleanly and stay in the workspace."""

    def test_every_browser_read_refuses_cleanly(self) -> None:
        failures: list[str] = []
        for index, route in enumerate(BROWSER_GETS):
            for round_ in range(ROUNDS):
                seed = SEED * 1000 + 500 + index * 10 + round_
                params = {key: str(value)[:2000] for key, value in Fuzzer(seed, self.sentinel).params(PARAM_KEYS).items()}
                query = f"?{urlencode(params)}" if params else ""
                status, body = self.call("GET", f"/api/v1/{route}{query}")
                self.check(failures, f"browser get {route}", seed, status, body)
        for prefix in BROWSER_PREFIXES:
            for value in hostile_strings(self.sentinel):
                status, body = self.call("GET", f"/api/v1/{prefix}{quote(value[:500], safe='/')}")
                self.check(failures, f"browser get {prefix}{value[:40]!r}", 0, status, body)
        self.report(failures)

    def test_every_browser_command_and_job_refuses_cleanly(self) -> None:
        failures: list[str] = []
        names = [("command", name) for name in BROWSER_COMMANDS] + [("job", name) for name in agents.OPERATIONS["job"]]
        for index, (kind, name) in enumerate(names):
            for round_ in range(ROUNDS):
                seed = SEED * 1000 + 800 + index * 10 + round_
                params = Fuzzer(seed, self.sentinel).params(PARAM_KEYS)
                endpoint, field = ("commands", "command") if kind == "command" else ("jobs", "action")
                status, body = self.call("POST", f"/api/v1/{endpoint}", {field: name, "params": params})
                self.check(failures, f"browser {kind} {name}", seed, status, body)
        self.report(failures)


class BoundaryTests(MisuseCase):
    """The HTTP boundary itself: malformed and slow requests."""

    def test_malformed_bodies_are_refused_without_a_server_error(self) -> None:
        bodies = [b"[" * 100_000, b"{" * 50_000, b'{"command": "cell-create", "params": {"spec": NaN}}',
                  b'{"command": "cell-create", "params": [1, 2]}', b"\xff\xfe\x00", b'"just a string"',
                  b'{"command": ["cell-create"]}', b"{" + b'"a": 1, ' * 50_000 + b'"command": "x"}']
        for body in bodies:
            for token in (None, self.token):
                status, payload = self.call("POST", "/api/v1/commands", raw=body, token=token)
                self.assertLess(status, 500, f"{body[:40]!r}: {payload}")
                self.assertGreaterEqual(status, 400, f"{body[:40]!r}: {payload}")

    def test_request_handlers_have_a_socket_timeout(self) -> None:
        # Without one, a client that stops sending holds a server thread forever.
        self.assertIsNotNone(server.Handler.timeout)
        self.assertLessEqual(server.Handler.timeout, 60)

    def test_a_stalled_client_is_disconnected(self) -> None:
        with mock.patch.object(server.Handler, "timeout", 0.5):
            for partial in (b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1",
                            f"POST /api/v1/commands HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                            "Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{".encode()):
                with socket.create_connection(("127.0.0.1", self.port), timeout=10) as client:
                    client.sendall(partial)
                    started = time.monotonic()
                    try:
                        while client.recv(4096):
                            pass
                    except (ConnectionResetError, ConnectionAbortedError):
                        pass
                    self.assertLess(time.monotonic() - started, 5)

    def test_foreign_hosts_and_origins_are_refused(self) -> None:
        self.assertEqual(self.call("GET", "/api/v1/health", headers={"Host": "evil.example"})[0], 421)
        self.assertEqual(self.call("GET", "/api/v1/cells", token=self.token,
                                   headers={"Origin": "https://evil.example"})[0], 403)
        status, _ = self.call("POST", "/api/v1/commands", {"command": "cell-create", "params": {}},
                              headers={"Origin": "null"})
        self.assertEqual(status, 403)
        status, _ = self.call("POST", "/api/v1/commands", {"command": "cell-create", "params": {}},
                              headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)


if __name__ == "__main__":
    unittest.main()
