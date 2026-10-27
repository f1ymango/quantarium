"""v0.6.3: forward inboxes under misuse by a hostile agent."""

from __future__ import annotations

import unittest

from quantarium import inbox

from .harness import MisuseCase


class InboxMisuseTests(MisuseCase):
    def test_an_agent_submits_through_the_checkpoint_and_is_stamped(self) -> None:
        status, created = self.agent("command", "decision-inbox-create", {"name": "mine", "symbols": ["AAA"]})
        self.assertEqual(status, 200, created)
        status, record = self.agent("command", "decision-submit",
                                    {"inbox": "mine", "date": "2099-01-05", "weights": {"AAA": 0.5}})
        self.assertEqual(status, 200, record)
        self.assertTrue(record["on_time"])
        self.assertTrue(record["submitted_by"].startswith("agent:"))
        status, listed = self.agent("get", "decision-inboxes")
        self.assertEqual((status, listed[0]["on_time"]), (200, 1))

    def test_hostile_inbox_requests_are_refused(self) -> None:
        self.agent("command", "decision-inbox-create", {"name": "mine", "symbols": ["AAA"]})
        attempts = [
            ("decision-inbox-create", {"name": "../escape", "symbols": ["AAA"]}),
            ("decision-inbox-create", {"name": ".quantarium", "symbols": ["AAA"]}),
            ("decision-inbox-create", {"name": str(self.sentinel), "symbols": ["AAA"]}),
            ("decision-inbox-create", {"name": "mine", "symbols": ["AAA"]}),
            ("decision-inbox-create", {"name": "many", "symbols": [f"S{index}" for index in range(501)]}),
            ("decision-submit", {"inbox": "../outside/sentinel", "date": "2099-01-05", "weights": None}),
            ("decision-submit", {"inbox": "mine", "date": "2099-01-05", "weights": {"ZZZ": 0.5}}),
            ("decision-submit", {"inbox": "mine", "date": "2099-01-05", "weights": {"AAA": 1.5}}),
            ("decision-submit", {"inbox": "mine", "date": "2099-01-05", "weights": None, "received_utc": "2000-01-01"}),
            ("decision-submit", {"inbox": "mine", "date": "2099-01-05", "weights": None, "note": "x" * 1000}),
        ]
        accepted = []
        for name, params in attempts:
            status, body = self.agent("command", name, params)
            if status < 400:
                accepted.append(f"{name} {str(params)[:60]}: {status}")
        self.assertEqual(accepted, [])
        raw = b'{"command": "decision-submit", "params": {"inbox": "mine", "date": "2099-01-05", "weights": {"AAA": NaN}}}'
        self.assertEqual(self.call("POST", "/api/v1/commands", raw=raw, token=self.token)[0], 400)

    def test_an_agent_cannot_choose_its_own_arrival_time(self) -> None:
        self.agent("command", "decision-inbox-create", {"name": "mine", "symbols": ["AAA"]})
        # Too late by Quantarium's clock, however the request is dressed up.
        status, record = self.agent("command", "decision-submit", {"inbox": "mine", "date": "2020-01-06",
                                                                    "weights": {"AAA": 1}})
        self.assertEqual(status, 200, record)
        self.assertFalse(record["on_time"])
        read = inbox.read(inbox.inbox_path(self.root, "mine"))
        self.assertEqual((len(read.decisions), read.late), (0, 1))

    def test_the_daily_budget_limits_submissions(self) -> None:
        limited = self.api.agents.create_connection("Limited", tiers=["observe", "simulate"],
                                                    budgets={"decisions_per_day": 3})["token"]
        statuses = []
        for index in range(5):
            name, params = ("decision-inbox-create", {"name": "budget", "symbols": ["AAA"]}) if index == 0 else \
                ("decision-submit", {"inbox": "budget", "date": f"2099-01-0{index + 4}", "weights": None})
            statuses.append(self.call("POST", "/api/v1/commands", {"command": name, "params": params},
                                      token=limited)[0])
        self.assertEqual(statuses[:3], [200, 200, 200])
        self.assertTrue(all(status == 429 for status in statuses[3:]), statuses)


if __name__ == "__main__":
    unittest.main()
