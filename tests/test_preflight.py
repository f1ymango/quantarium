"""Alpaca readiness check against a fake market-data HTTP layer."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from quantarium.preflight import alpaca_preflight, quiet_runs


ENV = {"APCA_API_KEY_ID": "key-id-value", "APCA_API_SECRET_KEY": "secret-value"}
# Wednesday 2024-07-03 was an early close (13:00 New York); "now" is the next day.
NOW = datetime(2024, 7, 4, 15, 0, tzinfo=timezone.utc)
OPEN = datetime(2024, 7, 3, 13, 30, tzinfo=timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class FakeAlpaca:
    def __init__(self, *, sip_allowed: bool = False, daily_symbols: set[str] | None = None,
                 minutes: dict[str, list[int]] | None = None) -> None:
        self.sip_allowed = sip_allowed
        self.daily_symbols = daily_symbols
        self.minutes = minutes or {}
        self.urls: list[str] = []

    def __call__(self, url: str, headers: dict[str, str]) -> object:
        self.urls.append(url)
        parsed = urlparse(url)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if parsed.path.endswith("/calendar"):
            return [
                {"date": "2024-07-01", "open": "09:30", "close": "16:00"},
                {"date": "2024-07-02", "open": "09:30", "close": "16:00"},
                {"date": "2024-07-03", "open": "09:30", "close": "13:00"},
            ]
        symbols = query["symbols"].split(",")
        if query["feed"] == "sip" and not self.sip_allowed:
            raise RuntimeError("Alpaca request failed with HTTP 403")
        if query["timeframe"] == "1Day":
            available = self.daily_symbols if self.daily_symbols is not None else set(symbols)
            return {"bars": {symbol: [{
                "t": "2024-07-03T04:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10, "v": 100,
            }] for symbol in symbols if symbol in available}, "next_page_token": None}
        # Split each symbol's minute bars across two pages.
        page = query.get("page_token")
        bars = {}
        for symbol in symbols:
            offsets = self.minutes.get(symbol, [])
            half = offsets[:len(offsets) // 2] if page is None else offsets[len(offsets) // 2:]
            bars[symbol] = [{"t": _stamp(OPEN + timedelta(minutes=offset)), "o": 10,
                             "h": 10, "l": 10, "c": 10, "v": 1} for offset in half]
        return {"bars": bars, "next_page_token": None if page else "second"}


class PreflightTests(unittest.TestCase):
    def test_missing_credentials_stop_before_any_request(self) -> None:
        fake = FakeAlpaca()
        report = alpaca_preflight("feed", {"AAPL"}, "iex", now=NOW,
                                  get_json=fake, environ={})
        self.assertFalse(report["ready"])
        self.assertFalse(report["checks"]["credentials"]["ok"])
        self.assertEqual(fake.urls, [])

    def test_live_check_reports_quiet_runs_on_an_early_close(self) -> None:
        fake = FakeAlpaca(minutes={
            "AAPL": [offset for offset in range(210) if not 10 <= offset < 15],
            "MSFT": [0, 209],
        })
        report = alpaca_preflight("live", {"AAPL", "MSFT"}, "iex", now=NOW,
                                  get_json=fake, environ=ENV)
        checks = report["checks"]
        self.assertTrue(report["ready"])
        self.assertEqual(checks["calendar"]["last_completed_session"], "2024-07-03")
        self.assertTrue(checks["daily_bars"]["ok"])
        self.assertFalse(checks["sip_history"]["accessible"])
        self.assertIn("HTTP 403", checks["sip_history"]["error"])
        coverage = checks["minute_coverage"]
        self.assertEqual(coverage["session_minutes"], 210)
        self.assertEqual(coverage["by_symbol"]["AAPL"], {
            "minutes_with_bars": 205, "minutes_without_bars": 5, "longest_quiet_run": 5,
        })
        self.assertEqual(coverage["by_symbol"]["MSFT"]["longest_quiet_run"], 208)
        self.assertEqual(coverage["longest_quiet_run_minutes"], 208)
        minute_requests = [url for url in fake.urls if "1Min" in url]
        self.assertEqual(len(minute_requests), 2)
        self.assertIn("start=2024-07-03T13%3A30%3A00Z", minute_requests[0])
        self.assertIn("end=2024-07-03T17%3A00%3A00Z", minute_requests[0])

    def test_report_never_contains_credentials(self) -> None:
        report = alpaca_preflight("live", {"AAPL"}, "iex", now=NOW,
                                  get_json=FakeAlpaca(), environ=ENV)
        text = json.dumps(report)
        self.assertNotIn(ENV["APCA_API_KEY_ID"], text)
        self.assertNotIn(ENV["APCA_API_SECRET_KEY"], text)

    def test_missing_daily_symbol_is_not_ready_and_sip_access_is_suggested(self) -> None:
        report = alpaca_preflight("feed", {"AAPL", "THIN"}, "iex", now=NOW,
                                  get_json=FakeAlpaca(sip_allowed=True, daily_symbols={"AAPL"}),
                                  environ=ENV)
        self.assertFalse(report["ready"])
        self.assertEqual(report["checks"]["daily_bars"]["missing_symbols"], ["THIN"])
        self.assertNotIn("minute_coverage", report["checks"])
        self.assertTrue(any("SIP daily history" in item for item in report["warnings"]))

    def test_large_iex_live_subscription_warns_about_stream_limit(self) -> None:
        symbols = {f"S{index:02d}" for index in range(31)}
        report = alpaca_preflight("live", symbols, "iex", now=NOW,
                                  get_json=FakeAlpaca(), environ=ENV)
        self.assertTrue(any("30-symbol" in item for item in report["warnings"]))

    def test_calendar_failure_is_reported(self) -> None:
        def failing(url: str, headers: dict[str, str]) -> object:
            raise RuntimeError("Alpaca request failed with HTTP 401")

        report = alpaca_preflight("feed", {"AAPL"}, "sip", now=NOW,
                                  get_json=failing, environ=ENV)
        self.assertFalse(report["ready"])
        self.assertIn("HTTP 401", report["checks"]["calendar"]["error"])

    def test_quiet_runs_counts_leading_and_trailing_gaps(self) -> None:
        start = OPEN
        times = [start + timedelta(minutes=2), start + timedelta(minutes=3)]
        self.assertEqual(quiet_runs(times, start, start + timedelta(minutes=7)), {
            "minutes_with_bars": 2, "minutes_without_bars": 5, "longest_quiet_run": 3,
        })


if __name__ == "__main__":
    unittest.main()
