"""Fetch daily bars from Futu OpenD into a Quantarium price file.

Runs in its own environment, never inside Quantarium (docs/futu-bridge.md):

    <bridge python> -I bridge.py --codes US.AAPL,US.MSFT --from 2024-01-02 --through 2024-06-28 \\
        --output data/futu.csv

It asks Futu's OpenD gateway, which the user runs and signs in to on this
computer, for daily candles, and writes a Static-format CSV (date, symbol,
open, high, low, close, volume). With ``--adjust none --append`` it adds only
sessions after each symbol's last date, so the file can feed a ``csv`` Feed experiment.

The bridge needs no keys: OpenD holds the login. It connects only to a
loopback address, uses only Futu's quote context, and has no trading code.
It cannot import Quantarium (``-I`` keeps the script's folder off the path), so
it checks bars itself; Quantarium checks the file again when it reads it.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from datetime import date
from pathlib import Path

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
CHECKOUT = Path(__file__).resolve().parents[2]
PRIVATE_OUTPUT_FOLDERS = {"data", "scenarios", "private"}
HEADER = ["date", "symbol", "open", "high", "low", "close", "volume"]
CODE = re.compile(r"(US|HK|SH|SZ|SG|JP|AU)\.[A-Z0-9.\-]{1,20}")
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,19}")
ADJUST = {"forward": "QFQ", "none": "NONE"}
MAX_PAGES = 1000


def parse_codes(text: str) -> dict[str, str]:
    """``US.AAPL,HK.00700=TENCENT`` -> {Futu code: Quantarium symbol}."""
    codes: dict[str, str] = {}
    for item in (part.strip() for part in text.split(",")):
        if not item:
            continue
        code, _, symbol = item.partition("=")
        code = code.strip().upper()
        if not CODE.fullmatch(code):
            raise ValueError(f"{code!r} is not a Futu code such as US.AAPL or HK.00700")
        symbol = (symbol.strip() or code.split(".", 1)[1]).upper()
        if not SYMBOL.fullmatch(symbol):
            raise ValueError(f"{symbol!r} is not a symbol Quantarium accepts; map it with {code}=NAME")
        if symbol in codes.values():
            raise ValueError(f"Two codes map to {symbol}")
        codes[code] = symbol
    if not codes:
        raise ValueError("Name at least one code")
    return codes


def to_rows(symbol: str, records: list[dict]) -> list[list]:
    """Futu candle records to price-file rows, refusing anything Quantarium would refuse."""
    rows = []
    seen = set()
    for record in records:
        day = date.fromisoformat(str(record["time_key"])[:10])
        values = [float(record[name]) for name in ("open", "high", "low", "close")]
        volume = float(record["volume"])
        if not all(0 < value < math.inf for value in values) or not 0 <= volume < math.inf:
            raise ValueError(f"{symbol} on {day}: a price is not positive and finite")
        open_, high, low, close = values
        if low > min(open_, close) or high < max(open_, close):
            raise ValueError(f"{symbol} on {day}: inconsistent open, high, low, and close")
        if day in seen:
            raise ValueError(f"{symbol} has two candles on {day}")
        seen.add(day)
        rows.append([day.isoformat(), symbol, repr(open_), repr(high), repr(low), repr(close), int(volume)])
    return sorted(rows)


def last_dates(path: Path) -> dict[str, str]:
    """Each symbol's last date in an existing price file."""
    last: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        if [name.strip().lower() for name in next(reader, [])][:7] != HEADER:
            raise ValueError(f"{path} is not a Quantarium price file ({', '.join(HEADER)})")
        for row in reader:
            if row:
                symbol = row[1].strip().upper()
                last[symbol] = max(last.get(symbol, ""), row[0].strip())
    return last


def checked_output(path: Path) -> Path:
    """Resolve output links and keep checkout writes in user-local folders."""
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(CHECKOUT)
    except ValueError:
        return resolved
    if not relative.parts or relative.parts[0] not in PRIVATE_OUTPUT_FOLDERS:
        raise ValueError("--output inside Quantarium must be under data/, scenarios/, or private/")
    return resolved


def write(path: Path, rows: list[list], *, append: bool) -> int:
    """Write rows, or append those after each symbol's last date; returns the rows written."""
    path = checked_output(path)
    if append and path.is_file():
        last = last_dates(path)
        rows = [row for row in rows if row[0] > last.get(row[1], "")]
        with path.open("a", encoding="utf-8", newline="") as target:
            csv.writer(target, lineterminator="\n").writerows(sorted(rows))
        return len(rows)
    if path.exists():
        raise FileExistsError(f"{path} exists; pass --append to add new sessions to it")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(sorted(rows))
    return len(rows)


def fetch(codes: dict[str, str], start: date, through: date, *, host: str, port: int, adjust: str) -> list[list]:
    """Ask OpenD for daily candles; only the quote context is used."""
    if host not in LOOPBACK:
        raise ValueError("The bridge talks only to OpenD on this computer (127.0.0.1, localhost, or ::1)")
    if not 1 <= port <= 65535:
        raise ValueError("OpenD port must be between 1 and 65535")
    if host == "localhost":
        host = "127.0.0.1"
    import futu  # installed only in the bridge's own environment

    context = futu.OpenQuoteContext(host=host, port=port)
    try:
        rows = []
        for code, symbol in codes.items():
            records, page = [], None
            for _ in range(MAX_PAGES):
                status, data, page = context.request_history_kline(
                    code, start=start.isoformat(), end=through.isoformat(), ktype=futu.KLType.K_DAY,
                    autype=getattr(futu.AuType, ADJUST[adjust]), max_count=1000, page_req_key=page)
                if status != futu.RET_OK:
                    raise RuntimeError(f"OpenD refused {code}: {data}")
                records += data.to_dict("records")
                if page is None:
                    break
            rows += to_rows(symbol, records)
        return rows
    finally:
        context.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--codes", required=True, help="Futu codes, such as US.AAPL,HK.00700=TENCENT")
    parser.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    parser.add_argument("--through", required=True, type=date.fromisoformat)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--append", action="store_true", help="Add sessions after each symbol's last date")
    parser.add_argument("--adjust", choices=sorted(ADJUST), default="forward",
                        help="forward: split- and dividend-adjusted, like download sites (default); none: raw")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    args = parser.parse_args(argv)
    if args.start > args.through:
        parser.error("--from must not follow --through")
    if args.append and args.adjust == "forward":
        raise ValueError("Cannot --append forward-adjusted history: forward-adjusted history changes after each "
                         "corporate action. Re-fetch the whole range instead, or use --adjust none.")
    output = checked_output(args.output)
    rows = fetch(parse_codes(args.codes), args.start, args.through, host=args.host, port=args.port,
                 adjust=args.adjust)
    written = write(output, rows, append=args.append)
    print(f"Wrote {written} bars to {output}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileExistsError, RuntimeError) as exc:
        sys.exit(f"futu bridge: {exc}")
