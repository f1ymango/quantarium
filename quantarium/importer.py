"""Normalize daily price files downloaded by hand into Quantarium's Static format.

This is the keyless path to real market data: download a symbol's history
from a site's own export button, then import it. Supported layouts:

- ``yahoo``: Date, Open, High, Low, Close, Adj Close, Volume
- ``nasdaq``: Date (MM/DD/YYYY), Close/Last, Volume, Open, High, Low, with "$"
- ``stooq`` / ``plain``: Date, Open, High, Low, Close, Volume

Sites usually publish split-adjusted prices, so imported bars should be run
without split actions. ``price_basis="adjusted"`` (Yahoo only) rescales each
bar by Adj Close / Close to approximate a total-return series including
dividends.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

from . import market_calendar
from .data import load_daily_csv, validate_bar
from .model import Bar

LAYOUTS = ("yahoo", "nasdaq", "stooq", "plain")


class CsvFeedProvider:
    """Serve a hand-maintained Static price CSV to Feed Batch.

    The file is reread on every run, so appending newly downloaded days
    advances the experiment. Expected sessions come from the offline NYSE
    calendar up to the file's last date, so a forgotten download stops the
    run instead of silently skipping a session. Prices from download sites
    are usually split-adjusted, so no corporate actions are reported.
    """

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(path)
        self.path = path
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            symbols = {(row.get("symbol") or "").strip().upper() for row in csv.DictReader(source)} - {""}
        _, self.tape, _ = load_daily_csv(path, symbols)
        self.last = max(self.tape)

    def sessions(self, start: date, through: date) -> set[date]:
        return market_calendar.sessions(start, min(through, self.last))

    def fetch(self, symbols: set[str], start: date, through: date) -> dict[date, dict[str, Bar]]:
        return {day: {symbol: bar for symbol, bar in bars.items() if symbol in symbols}
                for day, bars in self.tape.items() if start <= day <= through}

    def actions(self, symbols: set[str], start: date, through: date) -> list[dict]:
        return []


def _number(text: str) -> float:
    if not isinstance(text, str):
        raise ValueError("A row has too few columns")
    value = float(text.strip().replace("$", "").replace(",", ""))
    if not math.isfinite(value):
        raise ValueError(f"{text.strip()} is not a finite number")
    return value


def _detect(fields: list[str]) -> str:
    names = {field.strip().lower() for field in fields}
    if "adj close" in names:
        return "yahoo"
    if "close/last" in names:
        return "nasdaq"
    if {"date", "open", "high", "low", "close", "volume"} <= names:
        return "plain"
    raise ValueError(f"Unrecognized price file columns: {', '.join(fields)}")


def read_vendor_csv(path: Path, symbol: str, *, layout: str | None = None,
                    price_basis: str = "close") -> tuple[list[Bar], str]:
    """Read one symbol's downloaded daily history as validated bars."""
    if price_basis not in {"close", "adjusted"}:
        raise ValueError("price_basis must be close or adjusted")
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = list(reader.fieldnames or [])
        detected = layout or _detect(fields)
        if detected not in LAYOUTS:
            raise ValueError(f"layout must be one of {', '.join(LAYOUTS)}")
        if price_basis == "adjusted" and detected != "yahoo":
            raise ValueError("price_basis adjusted needs a Yahoo file with Adj Close")
        column = {field.strip().lower(): field for field in fields}
        bars: dict[date, Bar] = {}
        skipped = 0
        for line, row in enumerate(reader, start=2):
            values = {key: (row.get(original) or "").strip() for key, original in column.items()}
            if not values.get("date") or any(
                values.get(key, "").lower() in {"", "null", "nan", "-"}
                for key in ("open", "high", "low", "volume")
            ):
                skipped += 1  # placeholder rows that some sites add for holidays or dividends
                continue
            try:
                if detected == "nasdaq":
                    day = datetime.strptime(values["date"], "%m/%d/%Y").date()
                    close = _number(values["close/last"])
                else:
                    day = date.fromisoformat(values["date"][:10])
                    close = _number(values["close"])
                open_, high, low = (_number(values[key]) for key in ("open", "high", "low"))
                volume = int(round(_number(values["volume"])))
                if price_basis == "adjusted":
                    factor = _number(values["adj close"]) / close
                    open_, high, low, close = (value * factor for value in (open_, high, low, close))
            except (KeyError, ValueError, ZeroDivisionError) as exc:
                raise ValueError(f"{path.name} line {line}: {exc}") from exc
            bar = Bar(day, symbol, open_, high, low, close, volume)
            try:
                validate_bar(bar)
            except ValueError as exc:
                raise ValueError(f"{path.name} line {line}: {exc}") from exc
            if day in bars:
                raise ValueError(f"{path.name} has two rows for {day}")
            bars[day] = bar
    if not bars:
        raise ValueError(f"{path.name} has no usable daily rows")
    if skipped and len(bars) < skipped:
        raise ValueError(f"{path.name}: most rows were unusable; check the layout")
    return [bars[day] for day in sorted(bars)], detected


def import_bars(sources: dict[str, Path], output: Path, *, layout: str | None = None,
                price_basis: str = "close", overwrite: bool = False,
                common_dates: bool = False) -> dict:
    """Merge one downloaded file per symbol into a Static price CSV.

    Every symbol must have a bar on every date, as the Static engine requires.
    With ``common_dates``, dates missing from any symbol are dropped and
    reported; otherwise a mismatch is an error naming the first gaps.
    """
    if not sources:
        raise ValueError("Give at least one SYMBOL=FILE source")
    manifest_path = output.with_name(output.name + ".manifest.json")
    if not overwrite and (output.exists() or manifest_path.exists()):
        raise FileExistsError(f"{output} exists; pass --overwrite to replace it")
    by_symbol: dict[str, dict[date, Bar]] = {}
    layouts: dict[str, str] = {}
    for symbol, path in sources.items():
        symbol = symbol.strip().upper()
        bars, layouts[symbol] = read_vendor_csv(path, symbol, layout=layout,
                                                price_basis=price_basis)
        by_symbol[symbol] = {bar.date: bar for bar in bars}
    all_dates = sorted(set().union(*(set(bars) for bars in by_symbol.values())))
    shared = [day for day in all_dates if all(day in bars for bars in by_symbol.values())]
    dropped = [day for day in all_dates if day not in set(shared)]
    if dropped and not common_dates:
        gaps = [f"{day} (missing {', '.join(s for s, bars in sorted(by_symbol.items()) if day not in bars)})"
                for day in dropped[:5]]
        raise ValueError(
            "Symbols do not share the same dates: " + "; ".join(gaps)
            + ". Download matching ranges or pass --common-dates to keep only shared dates"
        )
    if not shared:
        raise ValueError("The files have no date in common")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as target:
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(("date", "symbol", "open", "high", "low", "close", "volume"))
        for day in shared:
            for symbol in sorted(by_symbol):
                bar = by_symbol[symbol][day]
                writer.writerow((day.isoformat(), symbol, repr(round(bar.open, 6)),
                                 repr(round(bar.high, 6)), repr(round(bar.low, 6)),
                                 repr(round(bar.close, 6)), bar.volume))
    load_daily_csv(output, set(by_symbol))
    manifest = {
        "format": "quantarium-import-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "price_basis": price_basis,
        "note": ("Prices as published by the source, usually split-adjusted; do not add split "
                 "actions." + (" Dividends folded in through Adj Close." if price_basis == "adjusted"
                               else " Dividends are not included.")),
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "sources": {symbol: {"file": path.name, "layout": layouts[symbol],
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                    for symbol, path in sorted((s.strip().upper(), p) for s, p in sources.items())},
        "start_date": shared[0].isoformat(), "end_date": shared[-1].isoformat(),
        "sessions": len(shared),
        "dropped_dates": [day.isoformat() for day in dropped],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"output": str(output), "manifest": str(manifest_path), "symbols": sorted(by_symbol),
            "sessions": len(shared), "start_date": manifest["start_date"],
            "end_date": manifest["end_date"], "dropped_dates": len(dropped)}
