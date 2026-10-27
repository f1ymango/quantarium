"""A stand-in decider for tests: speaks Quantarium's decider protocol on stdin and stdout.

The first argument picks the behaviour: "trend" (fully invested after an
up close, else cash), "garbage" (replies that are not JSON), "slow" (never
replies to a session), "flood" (one endless line), or "env" (reports, in
its note, which of the variables named in the second argument it can see).
"""

import json
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "trend"
WATCHED = sys.argv[2].split(",") if len(sys.argv) > 2 else []
closes: dict[str, list[float]] = {}


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    if message["op"] == "init":
        send({"ok": True})
        continue
    if MODE == "garbage":
        sys.stdout.write("not json\n")
        sys.stdout.flush()
        continue
    if MODE == "slow":
        time.sleep(60)
        continue
    if MODE == "flood":
        for _ in range(40):
            sys.stdout.write("x" * 1_000_000)
        sys.stdout.write("\n")
        sys.stdout.flush()
        continue
    for symbol, change in message["history"].items():
        rows = [row[4] for row in change["append"]]
        closes[symbol] = rows if change.get("reset") else closes.get(symbol, []) + rows
    if MODE == "env":
        import os

        seen = [name for name in WATCHED if name in os.environ]
        send({"weights": None, "note": "seen:" + ",".join(seen)})
        continue
    symbol = sorted(message["bars"])[0]
    history = closes.get(symbol, [])
    up = len(history) >= 2 and history[-1] > history[-2]
    send({"weights": {symbol: 1.0 if up else 0.0}, "note": "up" if up else "down"})
