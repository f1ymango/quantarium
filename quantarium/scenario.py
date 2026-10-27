"""Named, hash-pinned daily input packages for comparing market conditions.

A scenario is a directory holding ``scenario.json`` and a ``prices.csv`` in the
Static OHLCV format, with an optional ``actions.csv``. Every way of producing
one (slicing real bars, a seeded generator, or a recorded transformation)
writes the same format, so the engine reads scenarios like any Static input.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .legacy import is_format
from .data import load_daily_csv, validate_bar
from .market_calendar import sessions
from .model import Bar

FORMAT = "quantarium-scenario-v1"
MANIFEST = "scenario.json"
PRICES = "prices.csv"
ACTIONS = "actions.csv"
# From imported research data (quantarium/userdata.py).
DELISTINGS = "delistings.csv"
MEMBERSHIP = "membership.csv"
KINDS = ("historical", "synthetic", "transformed")
SESSIONS_PER_YEAR = 252
# Disguised copies move to market sessions from this date on, long after any model's training.
DISGUISE_START = date(2041, 1, 2)
_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True)
class Scenario:
    directory: Path
    name: str
    description: str
    kind: str
    tags: tuple[str, ...]
    symbols: tuple[str, ...]
    start_date: date
    end_date: date
    sessions: int
    provenance: dict
    manifest_sha256: str

    @property
    def prices_path(self) -> Path:
        return self.directory / PRICES

    @property
    def actions_path(self) -> Path | None:
        path = self.directory / ACTIONS
        return path if path.is_file() else None

    @property
    def delistings_path(self) -> Path | None:
        path = self.directory / DELISTINGS
        return path if path.is_file() else None

    @property
    def membership_path(self) -> Path | None:
        path = self.directory / MEMBERSHIP
        return path if path.is_file() else None

    @property
    def survivorship(self) -> str | None:
        """The user's declaration for imported data; None for scenarios that predate it."""
        return self.provenance.get("survivorship")

    def summary(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "tags": list(self.tags),
            "description": self.description, "symbols": list(self.symbols),
            "start_date": self.start_date.isoformat(), "end_date": self.end_date.isoformat(),
            "sessions": self.sessions, "manifest_sha256": self.manifest_sha256,
            **({"survivorship": self.survivorship} if self.survivorship else {}),
        }


def _sha256(path: Path) -> str:
    # Line endings are normalized so a checkout that converts LF to CRLF
    # (Git on Windows) does not look like a changed scenario.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _csv_symbols(path: Path) -> set[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or "symbol" not in reader.fieldnames:
            raise ValueError(f"{path} needs a symbol column")
        return {row["symbol"].strip().upper() for row in reader}


def _tags(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not all(
        isinstance(tag, str) and _NAME.fullmatch(tag) for tag in values
    ):
        raise ValueError("tags must be a list of 1-64 letters, digits, dashes, or underscores")
    return tuple(sorted(set(values)))


def load_scenario(directory: Path) -> Scenario:
    """Read a scenario and verify its files still match the manifest hashes."""
    manifest_path = directory / MANIFEST
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or not is_format(manifest.get("format"), "scenario"):
        raise ValueError(f"{manifest_path} is not a {FORMAT} manifest")
    name = manifest.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("Scenario name must use 1-64 letters, digits, dashes, or underscores")
    if manifest.get("kind") not in KINDS:
        raise ValueError(f"Scenario kind must be one of {', '.join(KINDS)}")
    files = manifest.get("files")
    if not isinstance(files, dict) or PRICES not in files:
        raise ValueError("Scenario manifest must list the hash of prices.csv")
    for filename, digest in files.items():
        if filename not in {PRICES, ACTIONS, DELISTINGS, MEMBERSHIP}:
            raise ValueError(f"Unexpected scenario file: {filename}")
        path = directory / filename
        if not path.is_file() or _sha256(path) != digest:
            raise ValueError(f"Scenario file {filename} is missing or changed since it was written")
    for filename in (ACTIONS, DELISTINGS, MEMBERSHIP):
        if (directory / filename).is_file() and filename not in files:
            raise ValueError(f"{filename} is not listed in the scenario manifest")
    symbols = manifest.get("symbols")
    if not isinstance(symbols, list) or not symbols or not all(isinstance(s, str) for s in symbols):
        raise ValueError("Scenario manifest needs its symbol list")
    return Scenario(
        directory=directory, name=name, description=str(manifest.get("description", "")),
        kind=manifest["kind"], tags=_tags(manifest.get("tags", [])),
        symbols=tuple(symbols),
        start_date=date.fromisoformat(manifest["start_date"]),
        end_date=date.fromisoformat(manifest["end_date"]),
        sessions=int(manifest["sessions"]), provenance=dict(manifest.get("provenance", {})),
        manifest_sha256=hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
    )


def _rounded(day: date, symbol: str, open_: float, high: float, low: float,
             close: float, volume: int) -> Bar:
    """Round to six decimals while keeping a valid OHLC bar."""
    open_, close = round(open_, 6), round(close, 6)
    high = max(round(high, 6), open_, close)
    low = max(min(round(low, 6), open_, close), 1e-6)
    bar = Bar(day, symbol, open_, high, low, close, int(volume))
    validate_bar(bar)
    return bar


def _write(directory: Path, *, name: str, description: str, kind: str,
           tags: tuple[str, ...], provenance: dict, tape: dict[date, dict[str, Bar]],
           action_rows: list[dict] | None = None, action_fields: list[str] | None = None,
           overwrite: bool = False) -> Scenario:
    if not _NAME.fullmatch(name):
        raise ValueError("Scenario name must use 1-64 letters, digits, dashes, or underscores")
    if kind not in KINDS:
        raise ValueError(f"Scenario kind must be one of {', '.join(KINDS)}")
    if not tape:
        raise ValueError("A scenario needs at least one session")
    if (directory / MANIFEST).exists() and not overwrite:
        raise FileExistsError(f"Scenario already exists: {directory}; pass --overwrite to replace it")
    directory.mkdir(parents=True, exist_ok=True)
    symbols = sorted({symbol for bars in tape.values() for symbol in bars})
    with (directory / PRICES).open("w", encoding="utf-8", newline="\n") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("date", "symbol", "open", "high", "low", "close", "volume"))
        for day in sorted(tape):
            for symbol in sorted(tape[day]):
                bar = tape[day][symbol]
                writer.writerow((day.isoformat(), symbol, repr(bar.open), repr(bar.high),
                                 repr(bar.low), repr(bar.close), bar.volume))
    files = {PRICES: _sha256(directory / PRICES)}
    actions = directory / ACTIONS
    if action_rows:
        with actions.open("w", encoding="utf-8", newline="\n") as output:
            writer = csv.DictWriter(output, fieldnames=action_fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(action_rows)
        files[ACTIONS] = _sha256(actions)
    elif actions.exists():
        actions.unlink()
    # Reject anything the Static loader would reject before publishing it.
    load_daily_csv(directory / PRICES, set(symbols))
    days = sorted(tape)
    manifest = {
        "format": FORMAT, "name": name, "description": description, "kind": kind,
        "tags": list(tags), "symbols": symbols,
        "start_date": days[0].isoformat(), "end_date": days[-1].isoformat(),
        "sessions": len(days), "provenance": provenance, "files": files,
    }
    (directory / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                      encoding="utf-8")
    return load_scenario(directory)


def slice_scenario(source: Path, directory: Path, start: date, through: date, *,
                   name: str, description: str = "", tags: tuple[str, ...] = (),
                   actions: Path | None = None, overwrite: bool = False) -> Scenario:
    """Cut a date range out of real daily bars, such as a Feed Batch export."""
    if start > through:
        raise ValueError("Slice start must not follow its end")
    symbols = _csv_symbols(source)
    days, tape, _ = load_daily_csv(source, symbols)
    selected = {day: tape[day] for day in days if start <= day <= through}
    if not selected:
        raise ValueError(f"No sessions between {start} and {through} in {source}")
    action_rows: list[dict] = []
    fields: list[str] | None = None
    if actions is not None:
        with actions.open("r", encoding="utf-8-sig", newline="") as reader_file:
            reader = csv.DictReader(reader_file)
            fields = list(reader.fieldnames or [])
            for row in reader:
                # Keep actions whose entitlement falls inside the slice; a
                # dividend paid after the last session stays a receivable.
                if start <= date.fromisoformat(row["ex_date"].strip()) <= through:
                    action_rows.append(row)
    provenance = {
        "method": "slice", "source_file": source.name, "source_sha256": _sha256(source),
        "from": start.isoformat(), "through": through.isoformat(),
    }
    if actions is not None:
        provenance["source_actions_sha256"] = _sha256(actions)
    return _write(directory, name=name, description=description, kind="historical",
                  tags=_tags(list(tags)), provenance=provenance, tape=selected,
                  action_rows=action_rows, action_fields=fields, overwrite=overwrite)


class ScenarioProvider:
    """Serve a scenario's bars to Feed Batch as if they arrived over time.

    It exists to exercise checkpoints, resumes, and stateful strategies on
    chosen conditions. Its sessions are never a forward test.
    """

    def __init__(self, scenario: Scenario) -> None:
        if scenario.actions_path is not None:
            raise ValueError("Scenario Feeds do not support corporate actions")
        self.scenario = scenario
        days, self.tape, _ = load_daily_csv(scenario.prices_path, set(scenario.symbols))
        self.days = set(days)

    def sessions(self, start: date, through: date) -> set[date]:
        return {day for day in self.days if start <= day <= through}

    def fetch(self, symbols: set[str], start: date, through: date) -> dict[date, dict[str, Bar]]:
        unknown = symbols - set(self.scenario.symbols)
        if unknown:
            raise ValueError(f"Scenario {self.scenario.name} has no {', '.join(sorted(unknown))}")
        return {day: {symbol: bars[symbol] for symbol in symbols}
                for day, bars in self.tape.items() if start <= day <= through}

    def actions(self, symbols: set[str], start: date, through: date) -> list[dict]:
        return []


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _positive(spec: dict, key: str, default: float | None = None, *, allow_zero: bool = False) -> float:
    value = spec.get(key, default)
    if value is None:
        raise ValueError(f"{key} is required")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        raise ValueError(f"{key} must be {'nonnegative' if allow_zero else 'positive'}")
    return number


def generate_scenario(spec: dict, directory: Path, *, overwrite: bool = False) -> Scenario:
    """Build a seeded synthetic market of correlated symbols.

    Each session draws one market log-return per segment's drift and
    volatility; every symbol moves by ``beta`` times the market plus its own
    noise. Sessions are weekdays without holidays. The same spec and seed
    always produce the same file on the same platform.
    """
    seed = spec.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    segments = spec.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("segments must be a nonempty list")
    raw_symbols = spec.get("symbols")
    if isinstance(raw_symbols, list):
        raw_symbols = {symbol: {} for symbol in raw_symbols}
    if not isinstance(raw_symbols, dict) or not raw_symbols:
        raise ValueError("symbols must be a nonempty list or object")
    symbols: dict[str, dict[str, float]] = {}
    for symbol, settings in raw_symbols.items():
        if not isinstance(symbol, str) or not symbol.strip() or not isinstance(settings, dict):
            raise ValueError("Each symbol needs a name and an optional settings object")
        symbols[symbol.strip().upper()] = {
            "price": _positive(settings, "price", 100),
            "beta": float(settings.get("beta", 1.0)),
            "idiosyncratic_volatility": _positive(settings, "idiosyncratic_volatility", 0.1,
                                                  allow_zero=True),
            "volume": _positive(settings, "volume", 1_000_000),
        }
    plan: list[tuple[float, float]] = []
    for segment in segments:
        sessions = segment.get("sessions")
        if isinstance(sessions, bool) or not isinstance(sessions, int) or sessions < 1:
            raise ValueError("Each segment needs a positive integer sessions count")
        drift = float(segment.get("annual_drift", 0))
        volatility = _positive(segment, "annual_volatility", allow_zero=True)
        if not math.isfinite(drift):
            raise ValueError("annual_drift must be finite")
        plan.extend([(drift / SESSIONS_PER_YEAR,
                      volatility / math.sqrt(SESSIONS_PER_YEAR))] * sessions)
    days = _weekdays(date.fromisoformat(spec["start_date"]), len(plan))
    rng = random.Random(seed)
    closes = {symbol: settings["price"] for symbol, settings in symbols.items()}
    tape: dict[date, dict[str, Bar]] = {}
    for day, (drift, sigma) in zip(days, plan):
        market = rng.gauss(drift - sigma * sigma / 2, sigma)
        tape[day] = {}
        for symbol in sorted(symbols):
            settings = symbols[symbol]
            idio = settings["idiosyncratic_volatility"] / math.sqrt(SESSIONS_PER_YEAR)
            total = math.hypot(settings["beta"] * sigma, idio) or 1e-4
            log_return = settings["beta"] * market + rng.gauss(-idio * idio / 2, idio)
            previous = closes[symbol]
            gap = rng.gauss(0, 0.25 * total)
            open_ = previous * math.exp(gap)
            close = previous * math.exp(log_return)
            high = max(open_, close) * math.exp(abs(rng.gauss(0, 0.5 * total)))
            low = min(open_, close) * math.exp(-abs(rng.gauss(0, 0.5 * total)))
            volume = settings["volume"] * math.exp(rng.gauss(0, 0.25))
            tape[day][symbol] = _rounded(day, symbol, open_, high, low, close, round(volume))
            closes[symbol] = tape[day][symbol].close
    return _write(directory, name=str(spec.get("name", "")),
                  description=str(spec.get("description", "")), kind="synthetic",
                  tags=_tags(spec.get("tags", [])),
                  provenance={"method": "generate", "generator": "correlated_random_walk_v1",
                              "spec": spec},
                  tape=tape, overwrite=overwrite)


def _factor_shock(count: int, start: int, operation: dict) -> list[float]:
    sessions = operation.get("sessions")
    if isinstance(sessions, bool) or not isinstance(sessions, int) or sessions < 1:
        raise ValueError("shock sessions must be a positive integer")
    drawdown = float(operation.get("drawdown", 0))
    if not 0 < drawdown < 1:
        raise ValueError("shock drawdown must be between 0 and 1")
    recovery_sessions = operation.get("recovery_sessions", 0)
    if isinstance(recovery_sessions, bool) or not isinstance(recovery_sessions, int) \
            or recovery_sessions < 0:
        raise ValueError("recovery_sessions must be a nonnegative integer")
    recovery = float(operation.get("recovery_fraction", 0))
    if not 0 <= recovery <= 1:
        raise ValueError("recovery_fraction must be between 0 and 1")
    bottom = 1 - drawdown
    settled = bottom + drawdown * recovery
    factors = []
    for index in range(count):
        step = index - start + 1
        if step <= 0:
            factors.append(1.0)
        elif step <= sessions:
            factors.append(bottom ** (step / sessions))
        elif recovery_sessions and step <= sessions + recovery_sessions:
            factors.append(bottom * (settled / bottom) ** ((step - sessions) / recovery_sessions))
        else:
            factors.append(settled if recovery_sessions else bottom)
    return factors


def _factor_returns(closes: list[float], operation: dict) -> list[float]:
    """Rebuild a close path from altered log-returns, as factors on the old path."""
    returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
    if operation["op"] == "scale_volatility":
        factor = _positive(operation, "factor", allow_zero=True)
        mean = sum(returns) / len(returns) if returns else 0
        altered = [mean + factor * (value - mean) for value in returns]
    else:
        annual = float(operation.get("annual", 0))
        if not math.isfinite(annual):
            raise ValueError("annual drift must be finite")
        altered = [value + annual / SESSIONS_PER_YEAR for value in returns]
    factors, level = [1.0], 0.0
    for old, new in zip(returns, altered):
        level += new - old
        factors.append(math.exp(level))
    return factors


def disguise_scenario(source: Path, directory: Path, *, seed: str, name: str) -> tuple[Scenario, dict[str, str]]:
    """Copy a scenario with invented symbols, future market sessions, and rescaled prices.

    The shape of the market is kept: each symbol's bars are multiplied by one
    factor, so returns and intraday ranges are unchanged, and session order is
    kept. Only identity changes: names, dates, and price levels. Returns the
    new scenario and the mapping from original symbols to invented ones. The
    provenance records the parent's hash, not its name.
    """
    parent = load_scenario(source)
    if parent.actions_path is not None:
        raise ValueError("The disguised replay does not support scenarios with corporate actions")
    if parent.survivorship is not None:
        raise ValueError("The disguised replay does not support imported research data yet (ADR-0039)")
    days, tape, _ = load_daily_csv(parent.prices_path, set(parent.symbols))
    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())
    mapping: dict[str, str] = {}
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for symbol in sorted(parent.symbols):
        while True:
            fake = "Z" + "".join(rng.choice(letters) for _ in range(3))
            if fake not in mapping.values() and fake not in parent.symbols:
                break
        mapping[symbol] = fake
    factors = {symbol: round(rng.uniform(0.5, 2.0), 4) for symbol in sorted(parent.symbols)}
    start = DISGUISE_START + timedelta(days=7 * rng.randrange(0, 52))
    future = sorted(sessions(start, start + timedelta(days=2 * len(days) + 60)))[:len(days)]
    if len(future) < len(days):
        raise ValueError("The calendar has too few future sessions for this scenario")
    disguised = {}
    for old, new in zip(days, future):
        disguised[new] = {mapping[symbol]: _rounded(new, mapping[symbol], bar.open * factors[symbol],
                                                    bar.high * factors[symbol], bar.low * factors[symbol],
                                                    bar.close * factors[symbol], bar.volume)
                          for symbol, bar in tape[old].items()}
    scenario = _write(directory, name=name, description="A disguised copy for the Lab", kind="transformed",
                      tags=(), provenance={"method": "disguise", "parent_manifest_sha256": parent.manifest_sha256},
                      tape=disguised, overwrite=True)
    return scenario, mapping


def transform_scenario(source: Path, spec: dict, directory: Path, *,
                       overwrite: bool = False) -> Scenario:
    """Apply recorded price transformations to an existing scenario.

    Each operation multiplies every price of a symbol's session by one factor,
    so bars stay valid and intraday ranges keep their proportions. Volume is
    unchanged. Scenarios with corporate actions are refused because cash
    dividends would no longer match the altered prices.
    """
    parent = load_scenario(source)
    if parent.actions_path is not None:
        raise ValueError("Transforms do not support scenarios with corporate actions")
    operations = spec.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("operations must be a nonempty list")
    days, tape, _ = load_daily_csv(parent.prices_path, set(parent.symbols))
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("op") not in {
            "shock", "scale_volatility", "add_drift",
        }:
            raise ValueError("Each operation needs op: shock, scale_volatility, or add_drift")
        targets = operation.get("symbols", list(parent.symbols))
        if not isinstance(targets, list) or not set(targets) <= set(parent.symbols) or not targets:
            raise ValueError("operation symbols must be a nonempty subset of the scenario's")
        for symbol in targets:
            if operation["op"] == "shock":
                start = date.fromisoformat(operation["start_date"])
                if start not in tape:
                    raise ValueError(f"shock start_date {start} is not a session in {parent.name}")
                factors = _factor_shock(len(days), days.index(start), operation)
            else:
                factors = _factor_returns([tape[day][symbol].close for day in days], operation)
            for day, factor in zip(days, factors):
                bar = tape[day][symbol]
                tape[day][symbol] = _rounded(day, symbol, bar.open * factor, bar.high * factor,
                                             bar.low * factor, bar.close * factor, bar.volume)
    return _write(directory, name=str(spec.get("name", "")),
                  description=str(spec.get("description", "")), kind="transformed",
                  tags=_tags(spec.get("tags", list(parent.tags))),
                  provenance={"method": "transform", "parent": parent.name,
                              "parent_manifest_sha256": parent.manifest_sha256,
                              "operations": operations},
                  tape=tape, overwrite=overwrite)
