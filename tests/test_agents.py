"""The agent switch: off by default, on only after the current notice is accepted."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from quantarium import agents
from quantarium.agents import CONFIRMATION, AgentSettings, AgentsDisabled, run_cli
from quantarium.api import Api
from quantarium.server import TOKEN_HEADER, QuantariumServer
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class AgentSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.workspace = Workspace(Path(self.temp.name))
        self.settings = AgentSettings(self.workspace)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_off_by_default_and_the_gate_refuses(self) -> None:
        status = self.settings.status()
        self.assertFalse(status["enabled"])
        self.assertIsNone(status["accepted_version"])
        self.assertIn("provided as is", status["notice"])
        with self.assertRaises(AgentsDisabled) as caught:
            self.settings.require_enabled()
        self.assertEqual(caught.exception.code, "agents_disabled")

    def test_enabling_needs_the_typed_confirmation_for_the_current_notice(self) -> None:
        with self.assertRaisesRegex(ValueError, "Type"):
            self.settings.enable("yes", agents.NOTICE_VERSION)
        with self.assertRaisesRegex(ValueError, "Type"):
            self.settings.enable(True, agents.NOTICE_VERSION)
        with self.assertRaisesRegex(ValueError, "notice has changed"):
            self.settings.enable(CONFIRMATION, agents.NOTICE_VERSION + 1)
        self.assertFalse(self.settings.enabled())
        status = self.settings.enable("  i understand   AND accept ", agents.NOTICE_VERSION)
        self.assertTrue(status["enabled"])
        self.settings.require_enabled()
        self.assertTrue(AgentSettings(self.workspace).enabled())

    def test_a_new_notice_turns_agent_features_off_until_accepted(self) -> None:
        self.settings.enable(CONFIRMATION, agents.NOTICE_VERSION)
        with mock.patch.object(agents, "NOTICE_VERSION", agents.NOTICE_VERSION + 1):
            status = self.settings.status()
            self.assertFalse(status["enabled"])
            self.assertTrue(status["needs_new_acceptance"])
            with self.assertRaises(AgentsDisabled):
                self.settings.require_enabled()

    def test_disable_and_unexpected_files_read_as_off(self) -> None:
        self.settings.enable(CONFIRMATION, agents.NOTICE_VERSION)
        self.assertFalse(self.settings.disable()["enabled"])
        for content in ("not json", "[]", json.dumps({"enabled": "yes", "notice_version": 1}),
                        json.dumps({"enabled": True})):
            self.settings.path.write_text(content, encoding="utf-8")
            self.assertFalse(self.settings.enabled(), content)

    def test_cli_refuses_to_enable_without_a_terminal(self) -> None:
        lines = []
        self.assertEqual(run_cli("enable", self.workspace, input_fn=lambda _: CONFIRMATION,
                                 print_fn=lines.append, interactive=False), 2)
        self.assertFalse(self.settings.enabled())
        self.assertEqual(run_cli("enable", self.workspace, input_fn=lambda _: "no",
                                 print_fn=lines.append, interactive=True), 1)
        self.assertEqual(run_cli("enable", self.workspace, input_fn=lambda _: CONFIRMATION,
                                 print_fn=lines.append, interactive=True), 0)
        self.assertTrue(self.settings.enabled())
        self.assertEqual(run_cli("disable", self.workspace, print_fn=lines.append), 0)
        self.assertFalse(self.settings.enabled())

    def test_the_switch_belongs_to_the_real_workspace_during_the_tutorial(self) -> None:
        api = Api(self.workspace)
        try:
            api.command("onboarding-start", {})
            api.command("agents-enable", {"confirmation": CONFIRMATION,
                                          "notice_version": agents.NOTICE_VERSION})
            self.assertTrue(api.get("agents", {})["enabled"])
            self.assertTrue((self.workspace.root / ".quantarium" / "agents.json").is_file())
            self.assertFalse((self.workspace.root / ".quantarium" / "tutorial" / ".quantarium" / "agents.json").exists())
        finally:
            api.close()


class AgentSwitchServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.server = QuantariumServer(Workspace(Path(self.temp.name)), port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method: str, path: str, body: object = None, token: bool = True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {}
        if body is not None:
            headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"}
            if token:
                headers[TOKEN_HEADER] = self.server.token
        connection.request(method, path, body=None if body is None else json.dumps(body).encode(),
                           headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def test_enable_through_the_api_needs_the_app_token(self) -> None:
        status, body = self.request("GET", "/api/v1/agents")
        self.assertEqual((status, body["enabled"]), (200, False))
        params = {"confirmation": CONFIRMATION, "notice_version": agents.NOTICE_VERSION}
        status, _ = self.request("POST", "/api/v1/commands", {"command": "agents-enable", "params": params},
                                 token=False)
        self.assertEqual(status, 403)
        status, body = self.request("POST", "/api/v1/commands", {"command": "agents-enable",
                                                                  "params": {**params, "confirmation": "ok"}})
        self.assertEqual(status, 400)
        status, body = self.request("POST", "/api/v1/commands", {"command": "agents-enable", "params": params})
        self.assertEqual((status, body["enabled"]), (200, True))

    def enable(self) -> None:
        status, _ = self.request("POST", "/api/v1/commands", {"command": "agents-enable", "params": {
            "confirmation": CONFIRMATION, "notice_version": agents.NOTICE_VERSION}})
        self.assertEqual(status, 200)

    def connect(self, name: str = "Claude Code", tiers: list | None = None) -> str:
        params = {"name": name} if tiers is None else {"name": name, "tiers": tiers}
        status, body = self.request("POST", "/api/v1/commands",
                                    {"command": "agent-connection-create", "params": params})
        self.assertEqual(status, 200, body)
        return body["token"]

    def agent(self, method: str, path: str, token: str, body: object = None, origin: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = origin
        connection.request(method, path, body=None if body is None else json.dumps(body).encode(),
                           headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def test_connections_need_agent_features_on(self) -> None:
        status, body = self.request("POST", "/api/v1/commands",
                                    {"command": "agent-connection-create", "params": {"name": "x"}})
        self.assertEqual((status, body["code"]), (403, "agents_disabled"))

    def test_a_token_works_within_its_grant_only(self) -> None:
        self.enable()
        token = self.connect()
        stored = (Path(self.temp.name) / ".quantarium" / "agents.json").read_text(encoding="utf-8")
        self.assertNotIn(token, stored)
        status, body = self.request("GET", "/api/v1/agents")
        self.assertEqual(body["connections"][0]["tiers"], ["observe", "draft", "simulate", "propose"])
        self.assertNotIn("token_sha256", body["connections"][0])

        self.assertEqual(self.agent("GET", "/api/v1/cells", token)[0], 200)
        status, cell = self.agent("POST", "/api/v1/commands", token, {"command": "cell-create", "params": {}})
        self.assertEqual(status, 200)
        self.assertEqual(self.agent("GET", f"/api/v1/cells/{cell['id']}", token)[0], 200)
        refused = [
            ("POST", "/api/v1/commands", {"command": "cell-delete", "params": {"ids": [cell["id"]]}}),
            ("POST", "/api/v1/commands", {"command": "cell-archive", "params": {"ids": [cell["id"]]}}),
            ("POST", "/api/v1/commands", {"command": "agents-disable", "params": {}}),
            ("POST", "/api/v1/commands", {"command": "agents-enable", "params": {
                "confirmation": CONFIRMATION, "notice_version": agents.NOTICE_VERSION}}),
            ("POST", "/api/v1/commands", {"command": "agent-connection-create", "params": {"name": "y"}}),
            ("POST", "/api/v1/commands", {"command": "onboarding-start", "params": {}}),
            ("POST", "/api/v1/jobs", {"action": "feed-batch", "params": {"config": "x.json"}}),
            ("GET", "/api/v1/agents", None),
            ("GET", "/api/v1/onboarding", None),
        ]
        for method, path, body in refused:
            status, payload = self.agent(method, path, token, body)
            self.assertEqual((status, payload["code"]), (403, "not_permitted"), (path, body))
        self.assertEqual(len(self.request("GET", "/api/v1/cells")[1]), 1)

    def test_observe_only_revoked_disabled_and_web_origins(self) -> None:
        self.enable()
        reader = self.connect("Reader", ["observe"])
        self.assertEqual(self.agent("GET", "/api/v1/workspace", reader)[0], 200)
        status, body = self.agent("POST", "/api/v1/commands", reader, {"command": "cell-create", "params": {}})
        self.assertEqual((status, body["code"]), (403, "not_permitted"))
        self.assertIn("draft", body["error"])
        status, body = self.agent("GET", "/api/v1/cells", reader, origin="https://example.com")
        self.assertEqual(status, 403)
        self.assertEqual(self.agent("GET", "/api/v1/cells", "qtm_wrong")[1]["code"], "unknown_token")

        _, listing = self.request("GET", "/api/v1/agents")
        self.request("POST", "/api/v1/commands", {"command": "agent-connection-revoke",
                                                  "params": {"id": listing["connections"][0]["id"]}})
        status, body = self.agent("GET", "/api/v1/cells", reader)
        self.assertEqual((status, body["code"]), (401, "unknown_token"))

        writer = self.connect("Writer")
        self.request("POST", "/api/v1/commands", {"command": "agents-disable", "params": {}})
        status, body = self.agent("GET", "/api/v1/cells", writer)
        self.assertEqual((status, body["code"]), (403, "agents_disabled"))

    def test_every_request_is_logged_redacted_and_visible_to_its_own_connection(self) -> None:
        self.enable()
        token = self.connect("Logger")
        other = self.connect("Other")
        self.agent("GET", "/api/v1/cells", token)
        self.agent("POST", "/api/v1/commands", token, {"command": "cell-delete", "params": {"ids": ["c_1"]}})
        self.agent("POST", "/api/v1/commands", token, {"command": "cell-save", "params": {
            "id": "c_missing", "api_key": "sk-ant-abcdefgh12345678", "name": f"see {other}"}})
        self.agent("GET", "/api/v1/cells", other)
        _, entries = self.request("GET", "/api/v1/agents/log")
        self.assertEqual([(entry["connection_name"], entry["name"], entry["outcome"]) for entry in entries], [
            ("Other", "cells", "ok"), ("Logger", "cell-save", "error"),
            ("Logger", "cell-delete", "refused"), ("Logger", "cells", "ok")])
        saved = entries[1]["params"]
        self.assertEqual(saved["api_key"], "[redacted]")
        self.assertEqual(saved["name"], "see [redacted]")
        log_file = (Path(self.temp.name) / ".quantarium" / "agent-log.sqlite").read_bytes()
        self.assertNotIn(other.encode(), log_file)
        status, mine = self.agent("GET", "/api/v1/agent-log", other)
        self.assertEqual((status, [entry["connection_name"] for entry in mine]), (200, ["Other"]))

    def test_budgets_and_pause(self) -> None:
        self.enable()
        status, body = self.request("POST", "/api/v1/commands", {"command": "agent-connection-create", "params": {
            "name": "Tight", "budgets": {"cells_per_day": 2, "jobs_per_hour": 1}}})
        token = body["token"]
        for _ in range(2):
            self.assertEqual(self.agent("POST", "/api/v1/commands", token,
                                        {"command": "cell-create", "params": {}})[0], 200)
        status, body = self.agent("POST", "/api/v1/commands", token, {"command": "cell-create", "params": {}})
        self.assertEqual((status, body["code"]), (429, "budget_exceeded"))
        spec = {"name": "tiny", "seed": 1, "start_date": "2030-01-07", "symbols": ["AAA"],
                "segments": [{"sessions": 5, "annual_drift": 0.1, "annual_volatility": 0.1}]}
        job = {"action": "scenario-generate", "params": {"output": "scenarios/tiny", "spec": spec}}
        status, submitted = self.agent("POST", "/api/v1/jobs", token, job)
        self.assertEqual(status, 202)
        self.server.api.wait(submitted["id"])
        status, body = self.agent("POST", "/api/v1/jobs", token, job)
        self.assertEqual((status, body["code"]), (429, "budget_exceeded"))

        self.request("POST", "/api/v1/commands", {"command": "agents-pause", "params": {}})
        status, body = self.agent("GET", "/api/v1/cells", token)
        self.assertEqual((status, body["code"]), (403, "agents_paused"))
        self.assertTrue(self.request("GET", "/api/v1/agents")[1]["paused"])
        self.request("POST", "/api/v1/commands", {"command": "agents-resume", "params": {}})
        self.assertEqual(self.agent("GET", "/api/v1/cells", token)[0], 200)
        status, body = self.request("POST", "/api/v1/commands", {"command": "agent-connection-create", "params": {
            "name": "Bad", "budgets": {"jobs_per_hour": 0}}})
        self.assertEqual(status, 400)

    def test_agents_write_scenarios_only_in_scenario_folders_and_never_replace_them(self) -> None:
        self.enable()
        token = self.connect("Scenarist")
        spec = {"name": "tiny", "seed": 1, "start_date": "2030-01-07", "symbols": ["AAA"],
                "segments": [{"sessions": 5, "annual_drift": 0.1, "annual_volatility": 0.1}]}
        for output in ("cells", ".quantarium/x", "scenarios/../cells", "scenarios/.hidden", "strategies/agent/x", "scenarios"):
            status, body = self.agent("POST", "/api/v1/jobs", token, {"action": "scenario-generate", "params": {
                "output": output, "spec": spec}})
            self.assertEqual((status, body["code"]), (403, "not_permitted"), output)
        status, job = self.agent("POST", "/api/v1/jobs", token, {"action": "scenario-generate", "params": {
            "output": "scenarios/tiny", "spec": spec}})
        self.assertEqual(status, 202)
        self.server.api.wait(job["id"])
        status, body = self.agent("POST", "/api/v1/jobs", token, {"action": "scenario-generate", "params": {
            "output": "scenarios/tiny", "spec": spec, "overwrite": True}})
        self.assertEqual((status, body["code"]), (403, "not_permitted"))
        self.assertIn("manage", body["error"])

    def test_agents_cannot_reach_hidden_folders_or_name_suites(self) -> None:
        self.enable()
        token = self.connect("Snoop")
        for path in ("/api/v1/config?config=.quantarium/agents.json",
                     "/api/v1/report?db=.quantarium/agent-log.sqlite",
                     "/api/v1/config?config=configs/../.quantarium/agents.json",
                     "/api/v1/scripts/strategies/../.quantarium/x.py"):
            status, body = self.agent("GET", path, token)
            self.assertEqual((status, body["code"]), (403, "not_permitted"), path)
        status, body = self.agent("POST", "/api/v1/jobs", token, {"action": "deploy-suite", "params": {
            "cells": ["c_0000000000"], "scenarios": ["scenarios/x"], "name": "mine"}})
        self.assertEqual((status, body["code"]), (403, "not_permitted"))
        status, body = self.agent("POST", "/api/v1/jobs", token, {"action": "deploy-suite", "params": {
            "cells": ["c_0000000000"], "scenarios": [".quantarium/x"]}})
        self.assertEqual((status, body["code"]), (403, "not_permitted"))

    def test_every_listed_operation_exists(self) -> None:
        api = self.server.api
        self.assertEqual(set(agents.OPERATIONS), {"get", "command", "job"})
        for route in set(agents.OPERATIONS["get"]) - agents.CONNECTION_ROUTES:
            with self.subTest(route=route):
                try:
                    api.get(route, {})
                except Exception as exc:  # missing parameters are fine; unknown routes are not
                    self.assertNotIn("Unknown endpoint", str(exc))

    def test_the_gate_reports_a_code(self) -> None:
        with mock.patch.object(self.server.api, "get", side_effect=AgentsDisabled("off")):
            status, body = self.request("GET", "/api/v1/agents")
        self.assertEqual((status, body["code"]), (403, "agents_disabled"))


if __name__ == "__main__":
    unittest.main()
