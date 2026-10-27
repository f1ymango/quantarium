"""Export immutable Feed bars for deterministic Static replay."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from .data import validate_bar
from .model import Bar


def export_feed_csv(
    database: Path, feed_id: str, output: Path, through: date | None = None,
    *, overwrite: bool = False,
) -> dict:
    """Write first-seen Feed bars, pinned actions, and a provenance manifest."""
    if not database.is_file():
        raise FileNotFoundError(database)
    if output.suffix.lower() != ".csv":
        raise ValueError("Feed export output must be a .csv file")
    if database.resolve() == output.resolve():
        raise ValueError("Feed export cannot overwrite its source database")
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    actions_path = output.with_suffix(".actions.csv")
    if not overwrite and any(path.exists() for path in (output, actions_path, manifest_path)):
        raise FileExistsError("Feed export output exists; use --overwrite to replace it")
    temp_csv: Path | None = None
    temp_actions: Path | None = None
    temp_manifest: Path | None = None
    with closing(sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)) as connection:
        experiment = connection.execute(
            "SELECT provider, market_feed, start_date FROM feed_experiments WHERE id=?",
            (feed_id,),
        ).fetchone()
        if experiment is None:
            raise ValueError(f"Feed experiment not found: {feed_id}")
        connection.execute("BEGIN")
        where = "feed_id=?" + (" AND date<=?" if through else "")
        values: tuple[str, ...] = ((feed_id, through.isoformat()) if through else (feed_id,))
        rows = connection.execute(
            f"SELECT date, symbol, open, high, low, close, volume FROM feed_bars "
            f"WHERE {where} ORDER BY date, symbol", values,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        expected_symbols: set[str] | None = None
        day_symbols: set[str] = set()
        current_day: str | None = None
        first_day: str | None = None
        count = 0
        outputs_replaced = False
        try:
            descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp",
                                                 dir=output.parent)
            temp_csv = Path(name)
            with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as target:
                writer = csv.writer(target, lineterminator="\n")
                writer.writerow(("date", "symbol", "open", "high", "low", "close", "volume"))
                for day, symbol, opening, high, low, close, volume in rows:
                    if day != current_day:
                        if current_day is not None:
                            if expected_symbols is None:
                                expected_symbols = day_symbols
                            elif day_symbols != expected_symbols:
                                raise ValueError(f"Incomplete saved Feed bars on {current_day}")
                        if first_day is None:
                            first_day = day
                        current_day = day
                        day_symbols = set()
                    if symbol in day_symbols:
                        raise ValueError(f"Duplicate saved Feed bar for {symbol} on {day}")
                    day_symbols.add(symbol)
                    bar = Bar(date.fromisoformat(day), symbol, opening, high, low, close, volume)
                    validate_bar(bar)
                    writer.writerow((day, symbol, opening, high, low, close, volume))
                    count += 1
            if not count:
                raise ValueError(f"Feed experiment {feed_id} has no saved bars in the selected range")
            if expected_symbols is not None and day_symbols != expected_symbols:
                raise ValueError(f"Incomplete saved Feed bars on {current_day}")
            symbols = sorted(day_symbols if expected_symbols is None else expected_symbols)
            digest = hashlib.sha256()
            with temp_csv.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            descriptor, name = tempfile.mkstemp(prefix=f".{actions_path.name}.", suffix=".tmp",
                                                 dir=output.parent)
            temp_actions = Path(name)
            action_count = 0
            has_action_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feed_actions'"
            ).fetchone() is not None
            with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as target:
                writer = csv.writer(target, lineterminator="\n")
                writer.writerow(("date", "symbol", "kind", "value", "ex_date", "source_id"))
                if has_action_table:
                    pinned = connection.execute(
                        "SELECT terms_json FROM feed_actions WHERE feed_id=? ORDER BY id", (feed_id,)
                    )
                    selected = []
                    for (terms_json,) in pinned:
                        terms = json.loads(terms_json)
                        if first_day <= terms["ex_date"] <= current_day:
                            if terms["symbol"] not in symbols:
                                raise ValueError("Pinned action references a symbol outside saved Feed bars")
                            selected.append(terms)
                    for terms in sorted(selected, key=lambda item: (item["ex_date"], item["symbol"], item["id"])):
                        writer.writerow((terms["date"], terms["symbol"], terms["kind"],
                                         terms["value"], terms["ex_date"], terms["id"]))
                        action_count += 1
            action_digest = hashlib.sha256(temp_actions.read_bytes()).hexdigest()
            manifest = {
                "feed_id": feed_id,
                "source_database": str(database.resolve()),
                "provider": experiment[0],
                "market_feed": experiment[1],
                "experiment_start_date": experiment[2],
                "first_date": first_day,
                "last_date": current_day,
                "requested_through": through.isoformat() if through else None,
                "symbols": symbols,
                "bars": count,
                "csv_sha256": digest.hexdigest(),
                "actions_csv": str(actions_path.resolve()),
                "actions": action_count,
                "actions_sha256": action_digest,
                "actions_scope": "pinned actions with ex-dates in exported bars; completeness is not guaranteed",
                "exported_utc": datetime.now(timezone.utc).isoformat(),
                "price_basis": "raw first-seen Feed bars; use the action CSV for supported actions",
            }
            descriptor, name = tempfile.mkstemp(prefix=f".{manifest_path.name}.", suffix=".tmp",
                                                 dir=output.parent)
            temp_manifest = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
                json.dump(manifest, target, indent=2)
                target.write("\n")
            temp_csv.replace(output)
            temp_csv = None
            outputs_replaced = True
            temp_actions.replace(actions_path)
            temp_actions = None
            temp_manifest.replace(manifest_path)
            temp_manifest = None
            return {"csv": str(output.resolve()), "manifest": str(manifest_path.resolve()), **manifest}
        except BaseException:
            if outputs_replaced:
                manifest_path.unlink(missing_ok=True)
            raise
        finally:
            if temp_csv is not None:
                temp_csv.unlink(missing_ok=True)
            if temp_actions is not None:
                temp_actions.unlink(missing_ok=True)
            if temp_manifest is not None:
                temp_manifest.unlink(missing_ok=True)
