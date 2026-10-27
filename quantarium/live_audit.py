"""Compare a completed Live Feed run with replay of its captured input."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from itertools import zip_longest
from pathlib import Path

from .legacy import is_format
from .audit import _open_readonly


_QUERIES = {
    "bars": (
        "SELECT timestamp, symbol, open, high, low, close, volume, "
        "finalized_at, sha256 FROM live_bars WHERE feed_id=? "
        "ORDER BY timestamp, symbol"
    ),
    "events": (
        "SELECT cell_name, sequence, timestamp, kind, payload_json "
        "FROM live_events WHERE feed_id=? ORDER BY cell_name, sequence"
    ),
    "minutes": (
        "SELECT timestamp, bar_count, finalized_at FROM live_minutes "
        "WHERE feed_id=? ORDER BY timestamp"
    ),
    "revisions": (
        "SELECT timestamp, symbol, detected_at, original_sha256, "
        "revised_sha256, revised_bar_json FROM live_revisions "
        "WHERE feed_id=? ORDER BY timestamp, symbol, revised_sha256"
    ),
    "cells": (
        "SELECT name, mode, initial_cash, source_hashes_json, state_json, sequence, "
        "status, error "
        "FROM live_cells WHERE feed_id=? ORDER BY name"
    ),
}


def _metadata(connection: sqlite3.Connection, feed_id: str) -> dict:
    row = connection.execute(
        "SELECT provider, market_feed, start_at, stop_at, input_kind, "
        "input_sha256, status FROM live_experiments WHERE id=?", (feed_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Live Feed experiment not found: {feed_id}")
    return dict(zip(("provider", "market_feed", "start_at", "stop_at",
                     "input_kind", "input_sha256", "status"), row))


def _capture_header(path: Path) -> tuple[dict, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    with path.open("r", encoding="utf-8-sig") as source:
        first = next((line for line in source if line.strip()), None)
    if first is None:
        raise ValueError("Live capture is empty")
    try:
        header = json.loads(first)
    except ValueError as exc:
        raise ValueError("Invalid Live capture header") from exc
    if not isinstance(header, dict) or not is_format(header.get("format"), "live-capture"):
        raise ValueError("Expected a Quantarium Live Feed capture")
    return header, digest


def audit_live_replay(source_database: Path, source_feed_id: str,
                      capture_path: Path, replay_database: Path,
                      replay_feed_id: str) -> dict:
    """Verify provenance and compare saved source/replay journals row by row."""
    header, digest = _capture_header(capture_path)
    with closing(_open_readonly(source_database)) as source, closing(
        _open_readonly(replay_database)
    ) as replay:
        source_meta = _metadata(source, source_feed_id)
        replay_meta = _metadata(replay, replay_feed_id)
        if source_meta["status"] != "complete" or replay_meta["status"] != "complete":
            raise ValueError("Live audit requires completed source and replay experiments")
        if source_meta["input_kind"] != "websocket" or replay_meta["input_kind"] != "replay":
            raise ValueError("Live audit requires WebSocket source and captured replay")
        if (replay_meta["input_sha256"] != digest
                or header.get("source_feed_id") != source_feed_id
                or header.get("start_at") != source_meta["start_at"]
                or header.get("stop_at") != source_meta["stop_at"]
                or header.get("market_feed") != source_meta["market_feed"]
                or any(source_meta[key] != replay_meta[key]
                       for key in ("provider", "market_feed", "start_at", "stop_at"))):
            raise ValueError("Live capture provenance or experiment session does not match")
        mismatches: list[dict] = []
        counts: dict[str, int] = {}
        for name, query in _QUERIES.items():
            if name == "minutes" and not all(
                database.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                 "AND name='live_minutes'").fetchone()
                for database in (source, replay)
            ):
                continue
            counts[name] = 0
            left = source.execute(query, (source_feed_id,))
            right = replay.execute(query, (replay_feed_id,))
            for index, (source_row, replay_row) in enumerate(zip_longest(left, right), 1):
                counts[name] += 1
                if source_row != replay_row and len(mismatches) < 20:
                    mismatches.append({
                        "section": name, "row_number": index,
                        "source": source_row, "replay": replay_row,
                    })
        if counts["bars"] == 0:
            raise ValueError("Completed Live experiments have no bars to audit")
        return {
            "source_feed_id": source_feed_id,
            "replay_feed_id": replay_feed_id,
            "capture_sha256": digest,
            "rows_compared": counts,
            "matched": not mismatches,
            "mismatches": mismatches,
        }
