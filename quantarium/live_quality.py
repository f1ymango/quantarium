"""Read-only coverage and timing diagnostics for Live Feed frame captures."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .legacy import is_format
from .live import MINUTE, _read_timestamp, parse_alpaca_minute_event
from .model import LiveSpec


def inspect_live_capture(spec: LiveSpec, path: Path) -> dict:
    """Assess only the symbol-minutes whose finalization clock was reached."""
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    symbols = {symbol for cell in spec.cells for symbol in cell.symbols}
    received: dict[tuple[datetime, str], list[tuple[datetime, str]]] = defaultdict(list)
    finalized: dict[datetime, datetime] = {}
    next_minute = spec.start_at
    previous_clock: datetime | None = None
    frame_count = 0
    unexpected_symbols: set[str] = set()
    stream_errors = 0
    ignored_outside_session = 0
    recorded_clock = False
    header_seen = False
    with path.open("r", encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"Invalid capture JSON on line {line_number}") from exc
            if (frame_count == 0 and not header_seen and isinstance(entry, dict)
                    and "format" in entry):
                recorded_clock = True
                header_seen = True
                header_symbols = entry.get("symbols")
                if (not is_format(entry.get("format"), "live-capture")
                        or entry.get("start_at") != spec.start_at.isoformat()
                        or entry.get("stop_at") != spec.stop_at.isoformat()
                        or entry.get("market_feed") != spec.market_feed
                        or entry.get("finalization_delay_seconds")
                        != spec.finalization_delay_seconds
                        or not isinstance(header_symbols, list)
                        or not all(isinstance(symbol, str)
                                   for symbol in header_symbols)
                        or set(header_symbols) != symbols):
                    raise ValueError("Capture header does not match the Live Feed config")
                continue
            try:
                stamp = entry["received_at"]
                if not isinstance(stamp, str):
                    raise ValueError("received_at must be a timestamp string")
                observed_at = _read_timestamp(stamp)
                items = entry["items"]
                if not isinstance(items, list) or not all(
                    isinstance(item, dict) for item in items
                ):
                    raise ValueError("items must be an array of objects")
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid capture frame on line {line_number}") from exc
            if previous_clock is not None and observed_at < previous_clock:
                raise ValueError(f"Capture receive times are out of order on line {line_number}")
            previous_clock = observed_at
            frame_count += 1
            for item in items:
                if item.get("T") == "error":
                    stream_errors += 1
                try:
                    parsed = parse_alpaca_minute_event(item)
                except ValueError as exc:
                    raise ValueError(f"Invalid minute bar on line {line_number}") from exc
                if parsed is None:
                    continue
                bar, kind = parsed
                if bar.symbol not in symbols:
                    unexpected_symbols.add(bar.symbol)
                    continue
                if not spec.start_at <= bar.timestamp < spec.stop_at:
                    ignored_outside_session += 1
                    continue
                if observed_at < bar.timestamp + MINUTE:
                    raise ValueError(f"Minute bar arrived before close on line {line_number}")
                received[(bar.timestamp, bar.symbol)].append((observed_at, kind))
            if recorded_clock:
                while (next_minute < spec.stop_at
                       and observed_at >= next_minute + timedelta(
                           seconds=spec.finalization_delay_seconds
                       )):
                    finalized[next_minute] = observed_at
                    next_minute += MINUTE
    if frame_count == 0:
        raise ValueError("Capture has no timestamped frames")
    if not recorded_clock:
        while next_minute < spec.stop_at:
            finalized[next_minute] = next_minute + timedelta(
                seconds=spec.finalization_delay_seconds
            )
            next_minute += MINUTE
    with path.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != digest:
            raise ValueError("Capture changed while being inspected")
    by_symbol = {symbol: {
        "on_time_minutes": 0, "missing_minutes": 0,
        "late_only_minutes": 0, "updates_before_finalization": 0,
        "updates_after_finalization": 0, "longest_quiet_run": 0,
    } for symbol in sorted(symbols)}
    quiet_run = dict.fromkeys(symbols, 0)
    issues = []
    for minute, cutoff in finalized.items():
        for symbol in sorted(symbols):
            events = received.get((minute, symbol), ())
            on_time = [(stamp, kind) for stamp, kind in events if stamp <= cutoff]
            after = [(stamp, kind) for stamp, kind in events if stamp > cutoff]
            summary = by_symbol[symbol]
            summary["updates_before_finalization"] += sum(
                kind == "u" for _, kind in on_time
            )
            summary["updates_after_finalization"] += sum(
                kind == "u" for _, kind in after
            )
            if on_time:
                summary["on_time_minutes"] += 1
                quiet_run[symbol] = 0
            else:
                summary["missing_minutes"] += 1
                quiet_run[symbol] += 1
                summary["longest_quiet_run"] = max(summary["longest_quiet_run"],
                                                   quiet_run[symbol])
                if after:
                    summary["late_only_minutes"] += 1
                if len(issues) < 25:
                    issues.append({
                        "timestamp": minute.isoformat(), "symbol": symbol,
                        "reason": "late_only" if after else "missing",
                    })
    declared_minutes = int((spec.stop_at - spec.start_at) / MINUTE)
    return {
        "capture_sha256": digest,
        "clock": "recorded" if recorded_clock else "ideal_deadlines",
        "declared_minutes": declared_minutes,
        "assessed_minutes": len(finalized),
        "window_complete": len(finalized) == declared_minutes,
        "symbols": len(symbols),
        "expected_symbol_minutes": len(finalized) * len(symbols),
        "on_time_symbol_minutes": sum(
            item["on_time_minutes"] for item in by_symbol.values()
        ),
        "missing_symbol_minutes": sum(
            item["missing_minutes"] for item in by_symbol.values()
        ),
        "late_only_symbol_minutes": sum(
            item["late_only_minutes"] for item in by_symbol.values()
        ),
        # The smallest max_quiet_minutes that would not have halted on this capture.
        "longest_quiet_run_minutes": max(
            (item["longest_quiet_run"] for item in by_symbol.values()), default=0
        ),
        "late_finalization_minutes": sum(
            cutoff >= minute + 2 * MINUTE for minute, cutoff in finalized.items()
        ),
        "stream_errors": stream_errors,
        "unexpected_symbols": sorted(unexpected_symbols),
        "ignored_outside_session": ignored_outside_session,
        "frames_read": frame_count,
        "by_symbol": by_symbol,
        "issues": issues,
    }
