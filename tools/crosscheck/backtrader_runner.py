"""Replay one cross-check case in backtrader, with Quantarium's assumptions.

Runs in the reference environment (see docs/plans/v0.8.x-engine.md), never
inside Quantarium:

    <reference python> -I backtrader_runner.py case.json result.json

The case names a Static price file, the symbols, a strategy and its
parameters, starting cash, and costs. Quantarium's assumptions, and how this
runner sets backtrader to match them:

- The strategy decides after each close, using data through that close
  (backtrader's ``next``).
- At the next open, target shares are floor(equity at the open * weight /
  open), with equity valued at the open (``cheat_on_open`` and
  ``next_open``).
- Sells go before buys, each in symbol order. A sell fills at the open less
  slippage; a buy at the open plus slippage (``set_slippage_perc`` on the
  open, allowed outside the bar's range, as Quantarium does not clamp).
- A filled order pays the flat commission plus a commission per share, but
  at least the minimum (``PerOrder``). Buys are capped so that shares and
  commission fit in the cash left after the sells and earlier buys; the runner
  computes that cap itself, and backtrader's own cash check is off.
- Whole shares, long only, no partial fills beyond the cash cap.
- Starting holdings are bought at the first session's open, before
  anything else, paying slippage and commission.
- With a rebalance interval of N, the strategy decides only after sessions
  1, 1 + N, 1 + 2N, ...; the next open trades only after a decision.
- With a volume cap, each rebalance fill is limited to the previous
  session's volume times the cap (no cap on the first session).
- Resting orders (StopGuard, DipBuyer) use backtrader's own stop and limit
  orders, renewed after each close. A stop fills at its price, or at the open
  when the open is already past it, less slippage; a limit fills at its price
  or a better open, without slippage (``slip_limit=False``).

The result holds the daily equity at each close and every fill.
"""

import csv
import json
import math
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import backtrader as bt


class PerOrder(bt.CommInfoBase):
    """A filled order's commission: flat plus per share, but at least the minimum."""

    params = (("flat", 0.0), ("per_share", 0.0), ("minimum", 0.0), ("stocklike", True),
              ("commtype", bt.CommInfoBase.COMM_FIXED))

    def _getcommission(self, size, price, pseudoexec):
        return max(self.p.minimum, self.p.flat + self.p.per_share * abs(size)) if size else 0.0


def commission_for(case: dict, shares: int) -> float:
    return max(case.get("commission_minimum", 0),
               case.get("commission_per_order", 0) + case.get("commission_per_share", 0) * shares)


def affordable(case: dict, cash: float, price: float) -> int:
    by_flat = math.floor((cash - case.get("commission_per_order", 0) + 1e-9)
                         / (price + case.get("commission_per_share", 0)))
    by_minimum = math.floor((cash - case.get("commission_minimum", 0) + 1e-9) / price)
    return max(0, min(by_flat, by_minimum))


RESTING = {"StopGuard", "DipBuyer"}


def targets(name: str, parameters: dict, symbols: list[str], closes: dict[str, list[float]]) -> dict[str, float]:
    """The example strategies in examples/strategies.py, written again over plain lists of closes."""
    if name == "BuyAndHold":
        return {symbols[0]: 1.0}
    if name == "EqualWeight":
        return {symbol: 1 / len(symbols) for symbol in symbols}
    if name == "RecentLeader":
        if any(len(closes[symbol]) < 2 for symbol in symbols):
            return {}
        leader = max(sorted(symbols), key=lambda symbol: closes[symbol][-1] / closes[symbol][-2])
        return {leader: 1.0}
    if name == "TrendFollower":
        window = int(parameters.get("window", 20))
        history = closes[symbols[0]]
        if len(history) < window:
            return {symbols[0]: 0.0}
        recent = history[-window:]
        return {symbols[0]: 1.0 if recent[-1] > sum(recent) / len(recent) else 0.0}
    raise ValueError(f"Unknown strategy {name}")


class Replay(bt.Strategy):
    params = (("case", None), ("record", None))

    def __init__(self):
        self.symbols = sorted(data._name for data in self.datas)
        self.data_of = {data._name: data for data in self.datas}
        self.closes = defaultdict(list)
        self.wanted = None
        self.sessions = 0
        self.started = False
        self.resting = []

    def next(self):
        case = self.p.case
        day = self.datas[0].datetime.date(0).isoformat()
        self.sessions += 1
        for symbol in self.symbols:
            self.closes[symbol].append(self.data_of[symbol].close[0])
        self.p.record["equity"].append([day, self.broker.getvalue()])
        if case["strategy"] in RESTING:
            self.decide_with_orders()
            return
        if (self.sessions - 1) % case.get("rebalance_every", 1):
            self.wanted = None
            return
        self.wanted = targets(case["strategy"], case.get("parameters", {}), self.symbols, self.closes)

    def decide_with_orders(self):
        """The resting-order strategies of crosscheck_strategies.py, with backtrader's orders."""
        for order in self.resting:
            self.cancel(order)
        self.resting = []
        symbol = self.symbols[0]
        data = self.data_of[symbol]
        close = data.close[0]
        held = self.getposition(data).size
        self.wanted = None
        if self.p.case["strategy"] == "StopGuard":
            if not held:
                self.wanted = {symbol: 1.0}
            else:
                self.resting.append(self.sell(data=data, size=held, exectype=bt.Order.Stop, price=close * 0.97))
        elif held:
            self.resting.append(self.sell(data=data, size=held, exectype=bt.Order.Limit, price=close * 1.03))
        else:
            price = close * 0.98
            shares = int(self.broker.getcash() * 0.95 // price)
            if shares:
                self.resting.append(self.buy(data=data, size=shares, exectype=bt.Order.Limit, price=price))

    def prenext_open(self):
        # backtrader calls this, not next_open, at the very first open; starting holdings are bought there.
        self.next_open()

    def next_open(self):
        case = self.p.case
        if not self.started:
            self.started = True
            for symbol, shares in sorted((case.get("initial_positions") or {}).items()):
                self.buy(data=self.data_of[symbol], size=shares)
        if self.wanted is None:
            return
        slip = case.get("slippage_bps", 0) / 10_000
        opens = {symbol: self.data_of[symbol].open[0] for symbol in self.symbols}
        positions = {symbol: self.getposition(self.data_of[symbol]).size for symbol in self.symbols}
        cash = self.broker.getcash()
        equity = cash + sum(positions[symbol] * opens[symbol] for symbol in self.symbols)
        desired = {symbol: math.floor(equity * self.wanted.get(symbol, 0) / opens[symbol]) for symbol in self.symbols}
        fraction = case.get("volume_cap")
        caps = ({symbol: math.floor(self.data_of[symbol].volume[-1] * fraction) for symbol in self.symbols}
                if fraction is not None and len(self.datas[0]) > 1 else {})
        for symbol in self.symbols:
            requested = max(0, positions[symbol] - desired[symbol])
            filled = min(requested, caps.get(symbol, requested))
            price = opens[symbol] * (1 - slip)
            if filled and cash + filled * price >= commission_for(case, filled):
                self.sell(data=self.data_of[symbol], size=filled)
                cash += filled * price - commission_for(case, filled)
        for symbol in self.symbols:
            requested = max(0, desired[symbol] - positions[symbol])
            if not requested:
                continue
            price = opens[symbol] * (1 + slip)
            filled = min(requested, affordable(case, cash, price), caps.get(symbol, requested))
            if filled:
                self.buy(data=self.data_of[symbol], size=filled)
                cash -= filled * price + commission_for(case, filled)
        self.wanted = None

    def notify_order(self, order):
        if order.status == order.Completed:
            self.p.record["fills"].append([bt.num2date(order.executed.dt).date().isoformat(), order.data._name,
                                           "buy" if order.isbuy() else "sell", abs(order.executed.size),
                                           order.executed.price])


def main(case_path: str, result_path: str) -> None:
    case = json.loads(Path(case_path).read_text(encoding="utf-8"))
    rows = defaultdict(list)
    with open(case["prices"], newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            if row["symbol"] in case["symbols"]:
                rows[row["symbol"]].append(row)
    cerebro = bt.Cerebro(cheat_on_open=True, stdstats=False)
    with tempfile.TemporaryDirectory() as folder:
        for symbol in sorted(case["symbols"]):
            path = Path(folder) / f"{symbol}.csv"
            with path.open("w", newline="", encoding="utf-8") as target:
                writer = csv.writer(target)
                writer.writerow(["date", "open", "high", "low", "close", "volume"])
                for row in rows[symbol]:
                    writer.writerow([row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"]])
            cerebro.adddata(bt.feeds.GenericCSVData(
                dataname=str(path), dtformat="%Y-%m-%d", datetime=0, open=1, high=2, low=3, close=4, volume=5,
                openinterest=-1, timeframe=bt.TimeFrame.Days), name=symbol)
        cerebro.broker.setcash(case["cash"])
        cerebro.broker.set_checksubmit(False)
        cerebro.broker.set_slippage_perc(case.get("slippage_bps", 0) / 10_000, slip_open=True, slip_limit=False,
                                         slip_match=True, slip_out=True)
        cerebro.broker.addcommissioninfo(PerOrder(flat=case.get("commission_per_order", 0),
                                                  per_share=case.get("commission_per_share", 0),
                                                  minimum=case.get("commission_minimum", 0)))
        record = {"equity": [], "fills": []}
        cerebro.addstrategy(Replay, case=case, record=record)
        cerebro.run()
    Path(result_path).write_text(json.dumps({"engine": f"backtrader {bt.__version__}", **record}), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
