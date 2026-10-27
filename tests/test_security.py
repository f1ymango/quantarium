"""Regression tests for the v0.3.0 security review."""

from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from quantarium import agents
from quantarium.agents import AgentSettings
from quantarium.provenance import is_unreviewed, record_agent_write, unreviewed_entry
from quantarium.sandbox import policy_problems
from quantarium.server import TOKEN_HEADER, QuantariumServer
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
HOLD = "class Hold:\n    def on_bar(self, context):\n        return 1.0\n"
SPEC = {"name": "m", "seed": 3, "start_date": "2030-01-07", "symbols": ["AAA"],
        "segments": [{"sessions": 30, "annual_drift": 0.1, "annual_volatility": 0.2}]}


class ServerSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        self.server = QuantariumServer(Workspace(self.root), port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.api = self.server.api
        (self.root / "strategies").mkdir()
        (self.root / "strategies" / "hold.py").write_bytes(HOLD.encode())
        for name in ("visible", "secret"):
            self.api.wait(self.api.submit("scenario-generate", {"output": f"scenarios/{name}",
                                                                "spec": {**SPEC, "name": name}})["id"])
        self.cell = self.api.command("cell-create", {"name": "Holder"})
        self.api.command("cell-save", {"id": self.cell["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
            "sleeves": [{"script": "strategies/hold.py", "class": "Hold"}]}})
        self.command("agents-enable", {"confirmation": agents.CONFIRMATION, "notice_version": agents.NOTICE_VERSION})
        self.token = self.command("agent-connection-create", {"name": "Agent", "tiers": list(agents.TIERS)})[1]["token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def call(self, method: str, path: str, body: object = None, token: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
            if token is None:
                headers[TOKEN_HEADER] = self.server.token
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        connection.request(method, path, body=None if body is None else json.dumps(body).encode(), headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def command(self, name: str, params: dict, token: str | None = None):
        return self.call("POST", "/api/v1/commands", {"command": name, "params": params}, token)

    def test_agents_read_only_their_own_job_results(self) -> None:
        # The user's Static run on holdout data, then the holdout marked.
        user_job = self.api.submit("deploy-static", {"cells": [self.cell["id"]], "data": "scenarios/secret/prices.csv"})
        self.api.wait(user_job["id"])
        self.command("scenario-holdout", {"key": "scenarios/secret", "holdout": True})
        status, seen = self.call("GET", f"/api/v1/jobs/{user_job['id']}", token=self.token)
        self.assertEqual(status, 200)
        self.assertNotIn("result", seen)
        self.assertNotIn("params", seen)
        self.assertEqual(seen["status"], "succeeded")
        # Its own job keeps its full result.
        status, job = self.call("POST", "/api/v1/jobs", {"action": "deploy-suite", "params": {
            "cells": [self.cell["id"]], "scenarios": ["scenarios/visible"]}}, self.token)
        done = self.call("GET", f"/api/v1/jobs/{job['id']}?wait=30", token=self.token)[1]
        self.assertIn("config", done["result"])

    def test_static_configs_and_feed_reads_respect_holdout(self) -> None:
        result = self.api.wait(self.api.submit("deploy-static", {"cells": [self.cell["id"]],
                                                                 "data": "scenarios/secret/prices.csv"})["id"])["result"]
        feed = self.api.wait(self.api.submit("deploy-feed", {"cells": [self.cell["id"]], "feed_id": "secret_forward",
                                                              "provider": "scenario", "source": "scenarios/secret",
                                                              "start_date": "2030-01-07"})["id"])["result"]
        self.command("scenario-holdout", {"key": "scenarios/secret", "holdout": True})
        for path in (f"/api/v1/config?config={result['config']}",
                     f"/api/v1/analysis?db={feed['db']}&kind=feed&id=secret_forward"):
            status, body = self.call("GET", path, token=self.token)
            self.assertEqual((status, body["code"]), (403, "holdout"), path)
        status, body = self.call("POST", "/api/v1/jobs", {"action": "run-static", "params": {"config": result["config"]}},
                                 self.token)
        self.assertEqual((status, body["code"]), (403, "holdout"))

    def test_a_copy_of_the_users_script_does_not_mark_the_original(self) -> None:
        self.command("script-write", {"key": "strategies/agent/copy.py", "source": HOLD}, self.token)
        original = self.root / "strategies" / "hold.py"
        self.assertIsNone(unreviewed_entry(original, original.read_bytes()))
        copy = self.root / "strategies" / "agent" / "copy.py"
        self.assertIsNotNone(unreviewed_entry(copy, copy.read_bytes()))
        scripts = {item["key"]: item["agent_unreviewed"] for item in self.call("GET", "/api/v1/scripts")[1]}
        self.assertEqual((scripts["strategies/hold.py"], scripts["strategies/agent/copy.py"]), (False, True))
        # The user's Feed on their own script still runs.
        done = self.api.wait(self.api.submit("deploy-feed", {"cells": [self.cell["id"]], "feed_id": "mine",
                                                              "provider": "scenario", "source": "scenarios/visible",
                                                              "start_date": "2030-01-07"})["id"])
        self.assertEqual(done["status"], "succeeded", done["error"])

    def test_agents_cannot_store_hidden_paths_inside_a_cell_spec(self) -> None:
        for spec in ({"model_card": ".quantarium/agents.json"}, {"model_card": "models/../.quantarium/agents.json"},
                     {"sleeves": [{"script": ".quantarium/evil.py", "class": "Hold"}]},
                     {"mode": "complex", "allocator": {"script": ".quantarium/evil.py", "class": "Evil"}}):
            body = {"id": self.cell["id"], "spec": {"mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
                                                    "sleeves": [{"script": "strategies/hold.py", "class": "Hold"}],
                                                    **spec}}
            with self.subTest(spec=spec):
                status, reply = self.command("cell-save", body, self.token)
                self.assertEqual(status, 403, reply)
        self.assertIsNone(self.api.cells.get(self.cell["id"])["spec"].get("model_card"))

    def test_agents_cannot_grant_or_copy_model_access(self) -> None:
        import hashlib

        grant = {"connection_id": "a_0000000000", "model": "m", "workspace": str(self.root),
                 "tape": ".quantarium/tapes/c.jsonl", "mode": "replay",
                 "approved_script_sha256": hashlib.sha256(HOLD.encode()).hexdigest(),
                 "max_calls": 1, "max_tokens": 10, "max_estimated_cost_usd": 1, "input_usd_per_million": 1,
                 "output_usd_per_million": 1, "min_sessions_between_calls": 1, "max_output_tokens": 5}
        spec = {"mode": "simple", "symbols": ["AAA"], "initial_cash": 1000, "ask": grant,
                "sleeves": [{"script": "strategies/hold.py", "class": "Hold"}]}
        status, _ = self.command("cell-save", {"id": self.cell["id"], "spec": spec}, self.token)
        self.assertEqual(status, 403)
        # The user grants it; the agent may then neither edit nor copy that cell.
        self.api.command("cell-save", {"id": self.cell["id"], "spec": spec})
        for name, body in (("cell-save", {"id": self.cell["id"], "spec": {**spec, "ask": None}}),
                           ("cell-duplicate", {"id": self.cell["id"]})):
            with self.subTest(name=name):
                status, _ = self.command(name, body, self.token)
                self.assertEqual(status, 403)

    def test_agents_cannot_set_a_models_training_cutoff(self) -> None:
        connection = self.api.command("agent-connection-create", {"name": "Model", "kind": "direct", "provider": {
            "preset": "custom", "protocol": "openai-chat", "endpoint": "http://127.0.0.1:9/v1", "model": "m",
            "key_store": "none"}})["connection"]
        status, _ = self.command("agent-connection-cutoff", {"id": connection["id"],
                                                             "training_cutoff": "2000-01-01"}, self.token)
        self.assertEqual(status, 403)
        self.assertNotIn("training_cutoff", self.api.agents.connection_by_id(connection["id"]))

    def test_a_lab_proposal_without_costs_uses_the_defaults(self) -> None:
        from quantarium.mcp import TOOLS

        _, _, _, body = TOOLS["propose_lab_experiment"].build({
            "idea": "i", "name": "n", "cell_id": self.cell["id"], "scenarios": ["scenarios/visible"],
            "pass_rule": {"min_sessions": 20}, "rationale": "Testing"})
        self.assertFalse(any(value is None for value in body["params"]["params"].values()))
        status, reply = self.command("proposal-create", body["params"], self.token)
        self.assertEqual(status, 200, reply)

    def test_agents_cannot_read_supercells_or_their_tapes(self) -> None:
        status, _ = self.call("GET", "/api/v1/supercells", token=self.token)
        self.assertEqual(status, 403)
        status, _ = self.call("GET", f"/api/v1/tapes/{self.cell['id']}", token=self.token)
        self.assertEqual(status, 403)
        status, _ = self.command("cell-ask-grant", {"id": self.cell["id"], "script": "strategies/hold.py",
                                                    "connection_id": "a_0000000000"}, self.token)
        self.assertEqual(status, 403)
        status, _ = self.call("GET", "/api/v1/supercells")
        self.assertEqual(status, 200)

    def test_a_negative_content_length_is_refused(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(f"POST /api/v1/commands HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                         "Content-Length: -5\r\nContent-Type: application/json\r\n\r\n".encode())
            reply = sock.recv(4096).decode(errors="replace")
        self.assertIn("400", reply.split("\r\n")[0])


class ConcurrencyAndPolicyTests(unittest.TestCase):
    def test_concurrent_writes_never_lose_a_revocation(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            settings = AgentSettings(Workspace(Path(temp)))
            settings.enable(agents.CONFIRMATION, agents.NOTICE_VERSION)
            victims = [settings.create_connection(f"Agent {index}")["connection"]["id"] for index in range(8)]
            bridge = settings.create_connection("Bridge", kind="bridge", provider={"bridge": "codex"})["connection"]["id"]
            stop = threading.Event()

            def churn() -> None:
                while not stop.is_set():
                    settings.issue_session_token(bridge)
                    settings.end_session_token(bridge)

            workers = [threading.Thread(target=churn) for _ in range(3)]
            for worker in workers:
                worker.start()
            for victim in victims:
                settings.revoke_connection(victim)
            stop.set()
            for worker in workers:
                worker.join()
            revoked = {item["id"] for item in settings.connections() if item["revoked_utc"]}
            self.assertEqual(revoked, set(victims))

    def test_the_code_policy_blocks_introspection_and_file_reads(self) -> None:
        for source in ("x = (y for y in ()).gi_frame.f_builtins\n", "x = '{0.__class__}'.format(1)\n",
                       "import operator\nx = operator.attrgetter('a')\n", "import numpy\nx = numpy.fromfile('f')\n",
                       "import numpy\nx = numpy.load('f')\n", "def f():\n    return f.__code__\n"):
            with self.subTest(source=source):
                self.assertTrue(policy_problems(source), source)
        self.assertEqual(policy_problems("x = f'{1 + 1}'\nimport statistics\ny = statistics.mean([1, 2])\n"), [])

    def test_a_tampered_thread_id_never_reaches_the_command_line(self) -> None:
        from unittest import mock
        from quantarium.runtimes.codex_exec import CodexBridge
        from quantarium.errors import QuantariumError

        class Api:
            workspace = Workspace(Path(tempfile.gettempdir()))

        with mock.patch("quantarium.runtimes.shutil.which", return_value="codex"):
            with self.assertRaises(QuantariumError):
                CodexBridge({}).command(Api(), "qtm_x", "--dangerously-bypass-approvals-and-sandbox")
            command = CodexBridge({}).command(Api(), "qtm_x", "11111111-2222-3333-4444-555555555555")
        self.assertEqual(command[1:4], ["exec", "resume", "11111111-2222-3333-4444-555555555555"])
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)

    def test_provenance_keys_are_per_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            root = Path(temp)
            record_agent_write(root, "strategies/agent/a.py", HOLD.encode(), {"id": "a_1", "name": "Agent"})
            record_agent_write(root, "strategies/agent/b.py", HOLD.encode(), {"id": "a_1", "name": "Agent"})
            for key in ("strategies/agent/a.py", "strategies/agent/b.py"):
                path = root / key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(HOLD.encode())
                self.assertIsNotNone(unreviewed_entry(path, HOLD.encode()), key)
            self.assertTrue(is_unreviewed(root, HOLD.encode(), "strategies/agent/b.py"))
            self.assertFalse(is_unreviewed(root, HOLD.encode(), "strategies/mine.py"))


if __name__ == "__main__":
    unittest.main()
