"""Machine-readable error codes and hints, and idempotent job submission."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from quantarium.api import Api, ApiError
from quantarium.errors import QuantariumError, NotFound
from quantarium.server import TOKEN_HEADER, QuantariumServer
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
SPEC = {"name": "tiny", "seed": 1, "start_date": "2030-01-07", "symbols": ["AAA"],
        "segments": [{"sessions": 5, "annual_drift": 0.1, "annual_volatility": 0.1}]}


class ErrorCodeServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.server = QuantariumServer(Workspace(Path(self.temp.name)), port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method: str, path: str, body: object = None, raw: bytes | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {}
        if body is not None or raw is not None:
            headers = {"Content-Type": "application/json", TOKEN_HEADER: self.server.token}
        payload = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def test_every_error_has_a_code_and_known_ones_a_hint(self) -> None:
        status, body = self.request("GET", "/api/v1/nowhere")
        self.assertEqual((status, body["code"]), (404, "unknown_endpoint"))
        self.assertIn("docs/api.md", body["hint"])
        status, body = self.request("GET", "/api/v1/cells/c_0000000000")
        self.assertEqual((status, body["code"]), (404, "cell_not_found"))
        self.assertEqual(body["hint"], "List cells with GET cells")
        status, body = self.request("GET", "/api/v1/report")
        self.assertEqual((status, body["code"]), (400, "missing_parameter"))
        status, body = self.request("POST", "/api/v1/commands", {"command": "nope", "params": {}})
        self.assertEqual((status, body["code"]), (404, "unknown_command"))
        status, body = self.request("POST", "/api/v1/commands", raw=b"{not json")
        self.assertEqual((status, body["code"]), (400, "invalid_request"))
        status, body = self.request("GET", "/api/v1/jobs/abc")
        self.assertEqual((status, body["code"]), (404, "unknown_job"))
        status, body = self.request("POST", "/api/v1/jobs", {"action": "deploy-suite", "params": {
            "cells": ["c_0000000000"], "scenarios": ["x"]}})
        self.assertEqual((status, body["code"]), (404, "cell_not_found"))

    def test_named_errors_from_the_cell_library(self) -> None:
        first = self.request("POST", "/api/v1/commands", {"command": "cell-create", "params": {"name": "A"}})[1]
        self.request("POST", "/api/v1/commands", {"command": "cell-create", "params": {"name": "B"}})
        status, body = self.request("POST", "/api/v1/commands", {"command": "cell-save", "params": {
            "id": first["id"], "name": "B"}})
        self.assertEqual((status, body["code"], body["error"]), (400, "name_taken", "Another cell is already named B"))

    def test_failed_jobs_carry_a_code(self) -> None:
        status, job = self.request("POST", "/api/v1/jobs", {"action": "scenario-generate", "params": {
            "output": "scenarios/bad", "spec": {"name": "bad"}}})
        self.assertEqual(status, 202)
        done = self.server.api.wait(job["id"])
        self.assertEqual((done["status"], done["error"]["code"]), ("failed", "job_failed"))

    def test_idempotency_keys(self) -> None:
        job = {"action": "scenario-generate", "params": {"output": "scenarios/tiny", "spec": SPEC},
               "idempotency_key": "retry-1"}
        status, first = self.request("POST", "/api/v1/jobs", job)
        self.assertEqual(status, 202)
        status, again = self.request("POST", "/api/v1/jobs", job)
        self.assertEqual((status, again["id"]), (202, first["id"]))
        self.assertEqual(len(self.request("GET", "/api/v1/jobs")[1]), 1)
        changed = {**job, "params": {**job["params"], "output": "scenarios/other"}}
        status, body = self.request("POST", "/api/v1/jobs", changed)
        self.assertEqual((status, body["code"]), (409, "idempotency_conflict"))
        status, body = self.request("POST", "/api/v1/jobs", {**job, "idempotency_key": ""})
        self.assertEqual((status, body["code"]), (400, "invalid_parameter"))
        status, other = self.request("POST", "/api/v1/jobs", {**changed, "idempotency_key": "retry-2"})
        self.assertNotEqual(other["id"], first["id"])


class ErrorClassTests(unittest.TestCase):
    def test_codes_keep_their_base_types(self) -> None:
        self.assertIsInstance(QuantariumError("x", "y"), ValueError)
        self.assertIsInstance(NotFound("x"), FileNotFoundError)
        self.assertEqual(str(NotFound("Unknown cell: c_1", "cell_not_found")), "Unknown cell: c_1")
        error = ApiError(400, "m", "c", "h")
        self.assertEqual((error.status, error.code, error.hint), (400, "c", "h"))

    def test_the_tutorial_passes_keys_through(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            api = Api(Workspace(Path(temp)))
            try:
                api.command("onboarding-start", {})
                params = {"output": "scenarios/tiny", "spec": SPEC}
                first = api.submit("scenario-generate", params, "k")
                self.assertEqual(api.submit("scenario-generate", params, "k")["id"], first["id"])
                api.wait(first["id"])
            finally:
                api.close()


if __name__ == "__main__":
    unittest.main()
