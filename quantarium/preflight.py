"""Read-only Alpaca readiness check for Feed Batch and Live Feed configs.

It requests only market data and the trading calendar. Credentials are read
from the environment and never appear in the returned report.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

from .provider import NEW_YORK, AlpacaDailyProvider, JsonGetter, _http_get_json

# Alpaca's free Basic plan documents a 30-symbol limit for the real-time
# stream; paid plans raise it. Quantarium cannot see the account's plan.
BASIC_STREAM_SYMBOL_LIMIT = 30
MINUTE = timedelta(minutes=1)


def _error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def quiet_runs(times: list[datetime], start_at: datetime, stop_at: datetime) -> dict:
    """Summarize minutes without a bar between ``start_at`` and ``stop_at``."""
    seen = set(times)
    longest = run = missing = 0
    minute = start_at
    while minute < stop_at:
        if minute in seen:
            run = 0
        else:
            missing += 1
            run += 1
            longest = max(longest, run)
        minute += MINUTE
    return {"minutes_with_bars": len(seen), "minutes_without_bars": missing,
            "longest_quiet_run": longest}


def alpaca_preflight(mode: str, symbols: set[str], market_feed: str, *,
                     now: datetime | None = None,
                     get_json: JsonGetter = _http_get_json,
                     environ: dict[str, str] | None = None) -> dict:
    """Check that the account can supply what an experiment would request."""
    if mode not in {"feed", "live"}:
        raise ValueError("mode must be feed or live")
    environ = os.environ if environ is None else environ
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    report: dict = {
        "mode": mode, "market_feed": market_feed, "symbols": len(symbols),
        "checks": {}, "warnings": [],
    }
    checks = report["checks"]
    key, secret = environ.get("APCA_API_KEY_ID"), environ.get("APCA_API_SECRET_KEY")
    checks["credentials"] = {"ok": bool(key and secret)}
    if not (key and secret):
        checks["credentials"]["error"] = (
            "Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in the environment"
        )
        report["ready"] = False
        return report
    provider = AlpacaDailyProvider(key, secret, feed=market_feed, get_json=get_json)

    yesterday = now.astimezone(NEW_YORK).date() - timedelta(days=1)
    try:
        hours = provider.session_hours(yesterday - timedelta(days=14), yesterday)
        if not hours:
            raise ValueError("No trading session in the last two weeks")
        session = max(hours)
        checks["calendar"] = {"ok": True, "last_completed_session": session.isoformat()}
    except Exception as exc:
        checks["calendar"] = {"ok": False, "error": _error(exc)}
        report["ready"] = False
        return report

    def daily(feed: str) -> dict:
        source = AlpacaDailyProvider(key, secret, feed=feed, get_json=get_json)
        try:
            bars = source.fetch(symbols, session, session).get(session, {})
        except Exception as exc:
            return {"ok": False, "error": _error(exc)}
        missing = sorted(symbols - bars.keys())
        return {"ok": not missing, "symbols_with_bar": len(bars),
                "missing_symbols": missing[:50],
                "missing_count": len(missing)}

    checks["daily_bars"] = {"feed": market_feed, **daily(market_feed)}
    if market_feed == "iex":
        # Alpaca documents SIP history as available without a subscription once
        # the requested end is at least 15 minutes old; this tests that claim
        # for this account on a session that closed at least a day ago.
        sip = daily("sip")
        checks["sip_history"] = {"feed": "sip", "accessible": "error" not in sip, **sip}
        if "error" not in sip:
            report["warnings"].append(
                f"SIP daily history is accessible to this account "
                f"({sip['symbols_with_bar']} of {len(symbols)} symbols on "
                f"{session.isoformat()}, versus "
                f"{checks['daily_bars'].get('symbols_with_bar', 0)} on IEX); consider "
                "market_feed \"sip\" for Feed Batch"
            )
    if mode == "live":
        if market_feed == "iex" and len(symbols) > BASIC_STREAM_SYMBOL_LIMIT:
            report["warnings"].append(
                f"{len(symbols)} symbols exceed the Basic plan's documented "
                f"{BASIC_STREAM_SYMBOL_LIMIT}-symbol stream limit; the subscription "
                "may be refused unless the account has a higher limit"
            )
        open_at, close_at = hours[session]
        try:
            times = provider.minute_times(symbols, open_at, close_at)
            by_symbol = {symbol: quiet_runs(times[symbol], open_at, close_at)
                         for symbol in sorted(symbols)}
            checks["minute_coverage"] = {
                "ok": True, "feed": market_feed,
                "session_minutes": int((close_at - open_at) / MINUTE),
                # The smallest max_quiet_minutes that would not have halted on
                # this session's historical bars. Streamed bars can differ.
                "longest_quiet_run_minutes": max(
                    (item["longest_quiet_run"] for item in by_symbol.values()), default=0
                ),
                "by_symbol": by_symbol,
            }
        except Exception as exc:
            checks["minute_coverage"] = {"ok": False, "error": _error(exc)}
    report["ready"] = all(check["ok"] for name, check in checks.items()
                          if name != "sip_history")
    return report
