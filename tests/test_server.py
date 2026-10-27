"""Local JSON API over real HTTP: reads, jobs, and the request guards."""

from __future__ import annotations

import http.client
import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from quantarium.server import TOKEN_HEADER, QuantariumServer
from quantarium.workspace import Workspace, config_kind


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        root = Path(cls.temp.name)
        shutil.copytree(EXAMPLES / "scenarios", root / "scenarios")
        configs = root / "configs"
        configs.mkdir()
        shutil.copy(EXAMPLES / "strategies.py", configs / "strategies.py")
        shutil.copy(EXAMPLES / "sample_daily.csv", configs / "sample_daily.csv")
        suite = json.loads((EXAMPLES / "suite.json").read_text(encoding="utf-8"))
        suite["scenarios"] = [
            ("../" + entry) if isinstance(entry, str) else {**entry, "path": "../" + entry["path"]}
            for entry in suite["scenarios"]
        ]
        (configs / "suite.json").write_text(json.dumps(suite), encoding="utf-8")
        static = json.loads((EXAMPLES / "experiment.json").read_text(encoding="utf-8"))
        (configs / "experiment.json").write_text(json.dumps(static), encoding="utf-8")
        (root / "runs").mkdir()
        cls.root = root
        cls.server = QuantariumServer(Workspace(root), port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, method: str, path: str, body: object = None,
                headers: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        payload = None if body is None else json.dumps(body).encode()
        merged = {"Content-Type": "application/json"} if body is not None else {}
        merged.update(headers or {})
        connection.request(method, path, body=payload, headers=merged)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        if response.getheader("Content-Type", "").startswith("application/json"):
            return response.status, json.loads(raw)
        return response.status, raw.decode()

    def submit(self, action: str, params: dict) -> dict:
        status, job = self.request("POST", "/api/v1/jobs", {"action": action, "params": params},
                                   {TOKEN_HEADER: self.server.token})
        self.assertEqual(status, 202, job)
        return self.server.api.wait(job["id"])

    def test_health_and_workspace(self) -> None:
        self.assertEqual(self.request("GET", "/api/v1/health")[1]["api"], "v1")
        status, overview = self.request("GET", "/api/v1/workspace")
        self.assertEqual(status, 200)
        kinds = {item["key"]: item["kind"] for item in overview["configs"]}
        self.assertEqual(kinds["configs/suite.json"], "suite")
        self.assertEqual(kinds["configs/experiment.json"], "static")
        self.assertEqual(len(overview["scenarios"]), 4)

    def test_index_receives_the_token_and_security_headers(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/")
        response = connection.getresponse()
        page = response.read().decode()
        self.assertIn(self.server.token, page)
        self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
        connection.close()

    def test_suite_job_then_report_and_scenario_series(self) -> None:
        job = self.submit("run-suite", {"config": "configs/suite.json"})
        self.assertEqual(job["status"], "succeeded", job["error"])
        status, report = self.request("GET", "/api/v1/suite-report?config=configs/suite.json")
        self.assertEqual(status, 200)
        self.assertEqual(len(report["matrix"]), 12)
        status, scenario = self.request("GET", "/api/v1/scenario?key=scenarios/steady_bull")
        self.assertEqual(len(scenario["closes"]["AAA"]), 252)

    def test_static_job_then_analysis_timeline_and_job_list(self) -> None:
        job = self.submit("run-static", {"config": "configs/experiment.json"})
        self.assertEqual(job["status"], "succeeded", job["error"])
        run_id = job["result"]["run_id"]
        query = f"db=runs/example.sqlite&kind=static&id={run_id}"
        status, analysis = self.request("GET", f"/api/v1/analysis?{query}&benchmark=single_stock")
        self.assertEqual(status, 200, analysis)
        self.assertEqual({cell["name"] for cell in analysis["cells"]}, {"single_stock", "two_sleeves"})
        status, timeline = self.request("GET", f"/api/v1/timeline?{query}&cell=two_sleeves"
                                               "&from=2024-01-03")
        self.assertEqual(status, 200, timeline)
        self.assertTrue(all(day["date"] >= "2024-01-03" for day in timeline["days"]))
        jobs = self.request("GET", "/api/v1/jobs")[1]
        self.assertIn(job["id"], {item["id"] for item in jobs})
        self.assertEqual(self.request("GET", f"/api/v1/jobs/{job['id']}")[1]["status"], "succeeded")

    def test_failed_job_reports_its_error(self) -> None:
        job = self.submit("scenario-generate", {"spec": {"name": "bad"}, "output": "scenarios/bad"})
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["type"], "ValueError")

    def test_writes_need_the_token_json_and_same_origin(self) -> None:
        body = {"action": "run-static", "params": {"config": "configs/experiment.json"}}
        self.assertEqual(self.request("POST", "/api/v1/jobs", body)[0], 403)
        self.assertEqual(self.request("POST", "/api/v1/jobs", body,
                                      {TOKEN_HEADER: "wrong"})[0], 403)
        self.assertEqual(self.request("POST", "/api/v1/jobs", body,
                                      {TOKEN_HEADER: self.server.token,
                                       "Origin": "https://evil.example"})[0], 403)
        status, _ = self.request("POST", "/api/v1/jobs", body,
                                 {TOKEN_HEADER: self.server.token, "Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_foreign_host_header_is_refused(self) -> None:
        status, payload = self.request("GET", "/api/v1/health", headers={"Host": "attacker.example"})
        self.assertEqual(status, 421, payload)

    def test_paths_must_stay_in_the_workspace(self) -> None:
        status, payload = self.request("GET", "/api/v1/config?key=../../pyproject.toml")
        self.assertEqual(status, 403, payload)
        status, _ = self.request("POST", "/api/v1/jobs",
                                 {"action": "scenario-generate",
                                  "params": {"spec": {}, "output": "../outside"}},
                                 {TOKEN_HEADER: self.server.token})
        self.assertEqual(status, 403)

    def test_bad_requests_are_explained(self) -> None:
        self.assertEqual(self.request("GET", "/api/v1/nothing")[0], 404)
        status, payload = self.request("GET", "/api/v1/analysis?db=runs/missing.sqlite&kind=static")
        self.assertEqual(status, 404)
        status, payload = self.request("GET", "/api/v1/analysis?db=configs/suite.json&kind=nope")
        self.assertEqual(status, 400)
        self.assertTrue(re.search("kind must be", payload["error"]))
        status, _ = self.request("POST", "/api/v1/jobs", {"action": "launch-rockets"},
                                 {TOKEN_HEADER: self.server.token})
        self.assertEqual(status, 404)

    def test_app_files_are_served_with_module_types(self) -> None:
        for path, expected in (("/js/main.js", "text/javascript"), ("/app.css", "text/css"),
                               ("/vendor/htm/standalone.module.js", "text/javascript"),
                               ("/vendor/uplot/uPlot.esm.js", "text/javascript")):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            connection.request("GET", path)
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 200, path)
            self.assertTrue(response.getheader("Content-Type").startswith(expected), path)
            connection.close()
        for path in ("/js/../../server.py", "/vendor/%2e%2e/api.py", "/missing.js"):
            self.assertEqual(self.request("GET", path)[0], 404, path)

    def test_commands_need_the_token_and_edit_cells(self) -> None:
        body = {"command": "cell-create", "params": {"name": "Over HTTP"}}
        self.assertEqual(self.request("POST", "/api/v1/commands", body)[0], 403)
        status, record = self.request("POST", "/api/v1/commands", body, {TOKEN_HEADER: self.server.token})
        self.assertEqual(status, 200, record)
        cards = self.request("GET", "/api/v1/cells")[1]
        self.assertIn(record["id"], {card["id"] for card in cards})
        self.assertEqual(self.request("GET", f"/api/v1/cells/{record['id']}")[1]["card"]["status"], "draft")

    def test_non_loopback_bind_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            QuantariumServer(Workspace(self.root), host="0.0.0.0", port=0)

    def test_config_kind_recognizes_shapes(self) -> None:
        self.assertEqual(config_kind({"cells": [], "start_at": 1, "stop_at": 2}), "live")
        self.assertIsNone(config_kind({"name": "not a config"}))


if __name__ == "__main__":
    unittest.main()
