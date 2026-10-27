"""Alpaca historical daily-bar adapter. Credentials never enter the journal."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .data import validate_bar
from .model import Bar


ALPACA_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
ALPACA_CALENDAR_URL = "https://paper-api.alpaca.markets/v2/calendar"
ALPACA_ACTIONS_URL = "https://data.alpaca.markets/v1/corporate-actions"
NEW_YORK = ZoneInfo("America/New_York")
JsonGetter = Callable[[str, dict[str, str]], object]


def _http_get_json(url: str, headers: dict[str, str]) -> object:
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Alpaca request failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot reach Alpaca: {exc.reason}") from exc


class AlpacaDailyProvider:
    def __init__(
        self,
        key_id: str,
        secret_key: str,
        *,
        feed: str = "sip",
        get_json: JsonGetter = _http_get_json,
    ) -> None:
        if not key_id or not secret_key:
            raise ValueError("Alpaca market-data credentials are required")
        if feed not in {"sip", "iex"}:
            raise ValueError("Alpaca feed must be sip or iex")
        self.headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        self.feed = feed
        self.get_json = get_json

    @classmethod
    def from_environment(cls, feed: str = "sip") -> "AlpacaDailyProvider":
        key = os.environ.get("APCA_API_KEY_ID")
        secret = os.environ.get("APCA_API_SECRET_KEY")
        if not key or not secret:
            raise ValueError("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in your environment")
        return cls(key, secret, feed=feed)

    def _calendar(self, start: date, through: date) -> list[dict]:
        if start < date(1970, 1, 1) or through > date(2029, 12, 31):
            raise ValueError("Alpaca documents calendar coverage only from 1970 through 2029")
        query = urlencode({
            "start": start.isoformat(), "end": through.isoformat(),
            "date_type": "TRADING",
        })
        payload = self.get_json(f"{ALPACA_CALENDAR_URL}?{query}", self.headers)
        if not isinstance(payload, list):
            raise ValueError("Alpaca returned an unexpected calendar response")
        seen: set[date] = set()
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("date"), str):
                raise ValueError("Alpaca returned an invalid calendar entry")
            day = date.fromisoformat(item["date"])
            if not start <= day <= through or day in seen:
                raise ValueError(f"Alpaca returned an out-of-range or duplicate session: {day}")
            seen.add(day)
        return payload

    def sessions(self, start: date, through: date) -> set[date]:
        """Return Alpaca's US equity trading dates, including early-close days."""
        if start > through:
            return set()
        return {date.fromisoformat(item["date"]) for item in self._calendar(start, through)}

    def session_hours(self, start: date, through: date) -> dict[date, tuple[datetime, datetime]]:
        """Return each trading date's regular open and close as UTC datetimes."""
        if start > through:
            return {}
        hours: dict[date, tuple[datetime, datetime]] = {}
        for item in self._calendar(start, through):
            day = date.fromisoformat(item["date"])
            try:
                opens, closes = (
                    datetime.combine(day, time.fromisoformat(item[key]), NEW_YORK)
                    .astimezone(timezone.utc)
                    for key in ("open", "close")
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Alpaca returned invalid session hours for {day}") from exc
            if opens >= closes:
                raise ValueError(f"Alpaca returned invalid session hours for {day}")
            hours[day] = (opens, closes)
        return hours

    def actions(self, symbols: set[str], start: date, through: date) -> list[dict]:
        """Find corporate actions by process date, including all supported types."""
        if start > through:
            return []
        result: list[dict] = []
        seen_ids: set[str] = set()
        ordered = sorted(symbols)
        for offset in range(0, len(ordered), 50):
            chunk = ordered[offset:offset + 50]
            base = {
                "symbols": ",".join(chunk), "start": start.isoformat(),
                "end": through.isoformat(),
                "data_quality": "complete", "limit": "1000",
            }
            token = None
            seen_tokens: set[str] = set()
            while True:
                query = dict(base)
                if token:
                    query["page_token"] = token
                payload = self.get_json(f"{ALPACA_ACTIONS_URL}?{urlencode(query)}", self.headers)
                if not isinstance(payload, dict) or not isinstance(payload.get("corporate_actions"), dict):
                    raise ValueError("Alpaca returned an unexpected corporate-actions response")
                for kind, entries in payload["corporate_actions"].items():
                    if not isinstance(entries, list):
                        raise ValueError("Alpaca returned an invalid corporate-action list")
                    for entry in entries:
                        if not isinstance(entry, dict):
                            raise ValueError("Alpaca returned an invalid corporate action")
                        action_symbol = (entry.get("symbol") or entry.get("old_symbol")
                                         or entry.get("acquiree_symbol"))
                        if action_symbol not in chunk:
                            raise ValueError("Alpaca returned an unexpected corporate-action symbol")
                        action_id = entry.get("id")
                        process_date = entry.get("process_date")
                        if not isinstance(action_id, str) or not action_id or not isinstance(process_date, str):
                            raise ValueError("Alpaca returned an action without ID or process date")
                        day = date.fromisoformat(process_date)
                        if not start <= day <= through or action_id in seen_ids:
                            raise ValueError("Alpaca returned a duplicate or out-of-range corporate action")
                        seen_ids.add(action_id)
                        result.append({**entry, "id": action_id, "type": kind,
                                       "symbol": action_symbol, "process_date": day.isoformat()})
                token = payload.get("next_page_token")
                if not token:
                    break
                if not isinstance(token, str) or token in seen_tokens:
                    raise ValueError("Alpaca returned a repeated or invalid corporate-action page token")
                seen_tokens.add(token)
        return result

    def _bar_entries(self, symbols: set[str], query: dict[str, str]):
        """Yield ``(symbol, entry)`` from every page, 50 symbols per request."""
        ordered = sorted(symbols)
        for offset in range(0, len(ordered), 50):
            chunk = ordered[offset:offset + 50]
            base = {"symbols": ",".join(chunk), **query}
            page_token = None
            seen_tokens: set[str] = set()
            while True:
                page = dict(base)
                if page_token:
                    page["page_token"] = page_token
                payload = self.get_json(f"{ALPACA_BARS_URL}?{urlencode(page)}", self.headers)
                if not isinstance(payload, dict) or not isinstance(payload.get("bars"), dict):
                    raise ValueError("Alpaca returned an unexpected bars response")
                for symbol, entries in payload["bars"].items():
                    if symbol not in chunk or not isinstance(entries, list):
                        raise ValueError("Alpaca returned an unexpected symbol or bar list")
                    for entry in entries:
                        yield symbol, entry
                page_token = payload.get("next_page_token")
                if not page_token:
                    break
                if not isinstance(page_token, str) or page_token in seen_tokens:
                    raise ValueError("Alpaca returned a repeated or invalid page token")
                seen_tokens.add(page_token)

    def fetch(self, symbols: set[str], start: date, through: date) -> dict[date, dict[str, Bar]]:
        if start > through:
            return {}
        result: dict[date, dict[str, Bar]] = defaultdict(dict)
        query = {
            "timeframe": "1Day",
            "start": start.isoformat(),
            # Alpaca's daily-bar timestamp marks the beginning of the day.
            # A date-only end on `through` can exclude that day's bar.
            "end": (through + timedelta(days=1)).isoformat(),
            "feed": self.feed,
            "adjustment": "raw",
            "limit": "10000",
            "sort": "asc",
        }
        for symbol, entry in self._bar_entries(symbols, query):
            timestamp = datetime.fromisoformat(entry["t"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("Alpaca bar timestamp lacks timezone")
            day = timestamp.astimezone(NEW_YORK).date()
            if not start <= day <= through:
                raise ValueError(f"Alpaca returned a bar outside requested dates: {day}")
            bar = Bar(day, symbol, float(entry["o"]), float(entry["h"]),
                      float(entry["l"]), float(entry["c"]), int(entry["v"]))
            validate_bar(bar)
            if symbol in result[day]:
                raise ValueError(f"Alpaca returned a duplicate bar: {symbol} {day}")
            result[day][symbol] = bar
        return dict(result)

    def minute_times(self, symbols: set[str], start_at: datetime,
                     stop_at: datetime) -> dict[str, list[datetime]]:
        """Return the UTC minute starts that have a historical bar, per symbol."""
        query = {
            "timeframe": "1Min",
            "start": start_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": stop_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "feed": self.feed,
            "adjustment": "raw",
            "limit": "10000",
            "sort": "asc",
        }
        result: dict[str, list[datetime]] = {symbol: [] for symbol in symbols}
        for symbol, entry in self._bar_entries(symbols, query):
            stamp = datetime.fromisoformat(str(entry["t"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Alpaca bar timestamp lacks timezone")
            stamp = stamp.astimezone(timezone.utc)
            if not start_at <= stamp < stop_at:
                continue
            result[symbol].append(stamp)
        return result
