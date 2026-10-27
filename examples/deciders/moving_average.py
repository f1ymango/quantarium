"""An example decider: fully invested while the close is above its moving average.

Register it, then choose it on a cell's Configure tab:

    python -m quantarium decisions decider-create moving_average -- python examples/deciders/moving_average.py 20

Quantarium sends one JSON line per session and reads one back. A decider can be
written in any language and can call anything, such as another framework or
a language model; this one uses only the standard library. See
docs/referee.md for the protocol.
"""

import json
import sys

WINDOW = int(sys.argv[1]) if len(sys.argv) > 1 else 20
closes: dict[str, list[float]] = {}


def reply(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    if message["op"] == "init":
        reply({"ok": True})
        continue
    # Only bars not sent before arrive; "reset" means the whole history was resent (after a split).
    for symbol, change in message["history"].items():
        new = [row[4] for row in change["append"]]
        closes[symbol] = new if change.get("reset") else closes.get(symbol, []) + new
    above = [symbol for symbol, series in closes.items()
             if len(series) >= WINDOW and series[-1] > sum(series[-WINDOW:]) / WINDOW]
    weights = {symbol: round(1 / len(above), 6) for symbol in above}
    reply({"weights": weights, "note": f"{len(above)} above their {WINDOW}-session average"})
