"""The user's own research data, in Quantarium's documented format (``quantarium-data-v1``).

Quantarium supplies compatibility, not data. A user converts what they bought
or gathered into one folder:

- ``source.json``: the format, who supplied the data, a license note, and the
  user's survivorship declaration (``survivorship_free``, ``survivors_only``,
  or ``unknown``). Quantarium records the declaration; it cannot verify it.
- ``prices.csv``: date, symbol, open, high, low, close, volume. A symbol may
  start after the first session and stop before the last, but it needs a bar
  on every session between its first and last.
- ``delistings.csv`` (optional): symbol, date, price, reason. ``date`` is the
  first session on which the symbol no longer trades; ``price`` is what a
  holder received, empty for the last close.
- ``membership.csv`` (optional): index, symbol, from, through. ``through`` is
  the last day of membership, empty while a symbol is still a member.

Every file is untrusted: sizes and counts are bounded, numbers must be
finite, and reading never runs code. See ``docs/plans/v0.9.x-data.md``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .legacy import is_format
from .data import load_daily_csv
from .model import Bar
from .strictjson import finite_float, refuse_constant, too_deep

FORMAT = "quantarium-data-v1"
SOURCE = "source.json"
PRICES = "prices.csv"
DELISTINGS = "delistings.csv"
MEMBERSHIP = "membership.csv"
SURVIVORSHIP = ("survivorship_free", "survivors_only", "unknown")
MAX_SOURCE_BYTES = 64 * 1024
MAX_PRICES_BYTES = 1024 * 1024 * 1024
MAX_TABLE_BYTES = 64 * 1024 * 1024
MAX_SYMBOLS = 20_000
MAX_ROWS = 1_000_000
MAX_TEXT = 500
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,19}")
INDEX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,31}")


@dataclass(frozen=True)
class Delisting:
    symbol: str
    date: date
    price: float | None
    reason: str


@dataclass(frozen=True)
class UserData:
    folder: Path
    source: dict
    days: list[date]
    tape: dict[date, dict[str, Bar]]
    # Each symbol's first and last session.
    spans: dict[str, tuple[date, date]]
    delistings: dict[str, Delisting]
    # index -> symbol -> [(from, through or None)]
    membership: dict[str, dict[str, list[tuple[date, date | None]]]]
    hashes: dict[str, str]
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        late = sum(first > self.days[0] for first, _ in self.spans.values())
        stopped = [symbol for symbol, (_, last) in self.spans.items() if last < self.days[-1]]
        return {
            "format": FORMAT, "supplier": self.source["supplier"],
            "survivorship": self.source["survivorship"], "license_note": self.source.get("license_note", ""),
            "symbols": len(self.spans), "sessions": len(self.days),
            "start_date": self.days[0].isoformat(), "end_date": self.days[-1].isoformat(),
            "listed_late": late, "stopped_early": len(stopped), "delistings": len(self.delistings),
            "indexes": {name: len(members) for name, members in sorted(self.membership.items())},
            "files": dict(self.hashes), "warnings": list(self.warnings),
        }


def _sha256(path: Path) -> str:
    # As for scenarios: line endings are normalized, so a CRLF checkout hashes the same.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _bounded(path: Path, limit: int) -> None:
    if path.stat().st_size > limit:
        size = f"{limit // 2**20} MiB" if limit >= 2**20 else f"{limit // 2**10} KiB"
        raise ValueError(f"{path.name} is larger than {size}")


def _text(value: object, name: str, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > MAX_TEXT:
        raise ValueError(f"{name} must be {'nonempty ' if required else ''}text of at most {MAX_TEXT} characters")
    return value.strip()


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"{SOURCE} repeats the key {key}")
        result[key] = value
    return result


def read_source(folder: Path) -> dict:
    path = folder / SOURCE
    if not path.is_file():
        raise ValueError(f"{folder} needs a {SOURCE} naming the format, the supplier, and a survivorship "
                         f"declaration; see docs/data-format.md")
    _bounded(path, MAX_SOURCE_BYTES)
    try:
        value = json.loads(path.read_bytes(), parse_constant=refuse_constant, parse_float=finite_float,
                           object_pairs_hook=_no_duplicates)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{SOURCE} is not valid JSON: {exc}") from None
    if not isinstance(value, dict) or too_deep(value):
        raise ValueError(f"{SOURCE} must be a JSON object")
    if not is_format(value.get("format"), "data"):
        raise ValueError(f'{SOURCE} must say "format": "{FORMAT}"')
    allowed = {"format", "supplier", "license_note", "survivorship", "description", "as_of"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{SOURCE} has unknown keys: {', '.join(unknown)}")
    survivorship = value.get("survivorship", "unknown")
    if survivorship not in SURVIVORSHIP:
        raise ValueError(f"survivorship must be one of {', '.join(SURVIVORSHIP)}")
    source = {"format": FORMAT, "supplier": _text(value.get("supplier"), "supplier", required=True),
              "license_note": _text(value.get("license_note"), "license_note"),
              "survivorship": survivorship, "description": _text(value.get("description"), "description")}
    if value.get("as_of") is not None:
        source["as_of"] = date.fromisoformat(_text(value["as_of"], "as_of", required=True)).isoformat()
    return source


def _table(path: Path, columns: tuple[str, ...]):
    """Rows of an optional CSV table, as dicts of stripped text, with their line numbers."""
    _bounded(path, MAX_TABLE_BYTES)
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        header = next(reader, None)
        if not header or [name.strip() for name in header[:len(columns)]] != list(columns):
            raise ValueError(f"{path.name} must start with the columns {', '.join(columns)}")
        count = 0
        for line, row in enumerate(reader, start=2):
            if not row:
                continue
            count += 1
            if count > MAX_ROWS:
                raise ValueError(f"{path.name} has more than {MAX_ROWS} rows")
            if len(row) < len(columns):
                raise ValueError(f"{path.name} line {line} has too few columns")
            yield line, {name: row[index].strip() for index, name in enumerate(columns)}


def _day(text: str, where: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{where}: {text!r} is not a date (YYYY-MM-DD)") from None


def read_user_data(folder: Path) -> UserData:
    """Read and check a ``quantarium-data-v1`` folder; every problem is a ValueError."""
    try:
        return _read(Path(folder))
    except csv.Error as exc:
        raise ValueError(f"Malformed CSV in {folder}: {exc}") from None


def symbol_spans(days: list[date], tape: dict[date, dict[str, Bar]]) -> dict[str, tuple[date, date]]:
    """Each symbol's first and last session; a gap between them is refused."""
    position = {day: index for index, day in enumerate(days)}
    counts: dict[str, int] = {}
    spans: dict[str, tuple[date, date]] = {}
    for day in days:
        for symbol in tape[day]:
            counts[symbol] = counts.get(symbol, 0) + 1
            first, _ = spans.get(symbol, (day, day))
            spans[symbol] = (first, day)
    for symbol in sorted(spans):
        first, last = spans[symbol]
        if counts[symbol] != position[last] - position[first] + 1:
            missing = next(day for day in days[position[first]:position[last] + 1] if symbol not in tape[day])
            raise ValueError(f"{symbol} has no bar on {missing}, between its first ({first}) and last ({last}) "
                             f"sessions; trading halts are not modelled, so fill or cut the gap")
    return spans


def parse_delistings(path: Path) -> dict[str, Delisting]:
    """The rows of a delistings file, checked on their own."""
    delistings: dict[str, Delisting] = {}
    for line, row in _table(path, ("symbol", "date", "price", "reason")):
        where = f"{path.name} line {line}"
        symbol = row["symbol"].upper()
        if not SYMBOL.fullmatch(symbol):
            raise ValueError(f"{where}: {symbol!r} is not a symbol Quantarium accepts")
        if symbol in delistings:
            raise ValueError(f"{where}: {symbol} is delisted twice")
        day = _day(row["date"], where)
        price = None
        if row["price"]:
            try:
                price = float(row["price"])
            except ValueError:
                raise ValueError(f"{where}: price {row['price']!r} is not a number") from None
            if not (0 <= price < math.inf):
                raise ValueError(f"{where}: price must be finite and nonnegative")
        delistings[symbol] = Delisting(symbol, day, price, _text(row["reason"], "reason"))
    return delistings


def check_delistings(delistings: dict[str, Delisting], spans: dict[str, tuple[date, date]]) -> None:
    for symbol, delisting in delistings.items():
        if symbol not in spans:
            raise ValueError(f"{DELISTINGS}: {symbol} has no prices")
        if delisting.date <= spans[symbol][1]:
            raise ValueError(f"{DELISTINGS}: {symbol} still has a bar on {spans[symbol][1]}; the date is the "
                             f"first session it no longer trades")


def parse_membership(path: Path) -> dict[str, dict[str, list[tuple[date, date | None]]]]:
    """index -> symbol -> sorted periods of membership, ``through`` None while open."""
    membership: dict[str, dict[str, list[tuple[date, date | None]]]] = {}
    for line, row in _table(path, ("index", "symbol", "from", "through")):
        where = f"{path.name} line {line}"
        if not INDEX.fullmatch(row["index"]):
            raise ValueError(f"{where}: {row['index']!r} is not an index name (letters, digits, '_.-')")
        symbol = row["symbol"].upper()
        if not SYMBOL.fullmatch(symbol):
            raise ValueError(f"{where}: {symbol!r} is not a symbol Quantarium accepts")
        start = _day(row["from"], where)
        through = _day(row["through"], where) if row["through"] else None
        if through is not None and through < start:
            raise ValueError(f"{where}: through comes before from")
        periods = membership.setdefault(row["index"], {}).setdefault(symbol, [])
        for other_start, other_through in periods:
            if start <= (other_through or date.max) and other_start <= (through or date.max):
                raise ValueError(f"{where}: {symbol}'s membership of {row['index']} overlaps another row")
        periods.append((start, through))
    for periods in (p for members in membership.values() for p in members.values()):
        periods.sort()
    return membership


def _read(folder: Path) -> UserData:
    source = read_source(folder)
    prices = folder / PRICES
    if not prices.is_file():
        raise ValueError(f"{folder} has no {PRICES}")
    _bounded(prices, MAX_PRICES_BYTES)
    days, tape, _ = load_daily_csv(prices, None, every_session=False)
    hashes = {SOURCE: _sha256(folder / SOURCE), PRICES: _sha256(prices)}
    warnings: list[str] = []
    # Each symbol must trade on every session between its first and last bar.
    spans = symbol_spans(days, tape)
    if len(spans) > MAX_SYMBOLS:
        raise ValueError(f"{PRICES} has more than {MAX_SYMBOLS} symbols")
    for symbol in sorted(spans):
        if not SYMBOL.fullmatch(symbol):
            raise ValueError(f"{symbol!r} is not a symbol Quantarium accepts (A-Z, 0-9, '.', '-'; up to 20)")

    delistings: dict[str, Delisting] = {}
    path = folder / DELISTINGS
    if path.is_file():
        hashes[DELISTINGS] = _sha256(path)
        delistings = parse_delistings(path)
        check_delistings(delistings, spans)
    for symbol, (_, last) in sorted(spans.items()):
        if last < days[-1] and symbol not in delistings:
            warnings.append(f"{symbol} stops on {last} without a delisting row; a holding is closed at its "
                            f"last close")

    membership: dict[str, dict[str, list[tuple[date, date | None]]]] = {}
    path = folder / MEMBERSHIP
    if path.is_file():
        hashes[MEMBERSHIP] = _sha256(path)
        membership = parse_membership(path)
        unpriced = sorted({symbol for members in membership.values() for symbol in members} - set(spans))
        if unpriced:
            warnings.append(f"{len(unpriced)} index member{'s have' if len(unpriced) > 1 else ' has'} no prices "
                            f"({', '.join(unpriced[:10])}{', ...' if len(unpriced) > 10 else ''}); a cell trading "
                            f"the index cannot hold them")
    if source["survivorship"] != "survivorship_free":
        warnings.append(f"Declared {source['survivorship'].replace('_', ' ')}: Lab verdicts on this data carry a "
                        f"survivorship caveat")
    return UserData(folder, source, days, tape, spans, delistings, membership, hashes, warnings)


def import_user_data(folder: Path, directory: Path, *, name: str, description: str = "",
                     tags: tuple[str, ...] = (), overwrite: bool = False):
    """Make a historical scenario from a data folder, recording its source and declaration."""
    from . import scenario as scenarios

    data = read_user_data(folder)
    if not scenarios._NAME.fullmatch(name):
        raise ValueError("Scenario name must use 1-64 letters, digits, dashes, or underscores")
    if (directory / scenarios.MANIFEST).exists() and not overwrite:
        raise FileExistsError(f"Scenario already exists: {directory}; pass --overwrite to replace it")
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for filename in (PRICES, DELISTINGS, MEMBERSHIP):
        target = directory / filename
        if filename in data.hashes:
            shutil.copyfile(data.folder / filename, target)
            files[filename] = _sha256(target)
        elif target.exists():
            target.unlink()
    manifest = {
        "format": scenarios.FORMAT, "name": name, "description": description or data.source["description"],
        "kind": "historical", "tags": list(scenarios._tags(list(tags))), "symbols": sorted(data.spans),
        "start_date": data.days[0].isoformat(), "end_date": data.days[-1].isoformat(),
        "sessions": len(data.days), "files": files,
        "provenance": {"method": "import-data", "source": data.source, "source_files": data.hashes,
                       "survivorship": data.source["survivorship"]},
    }
    (directory / scenarios.MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                                encoding="utf-8")
    return scenarios.load_scenario(directory), data.warnings
