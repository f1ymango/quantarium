"""Strategies for the cross-check that use Quantarium's resting orders.

``backtrader_runner.py`` writes the same rules again with backtrader's own
stop and limit orders. Each strategy places either target weights or resting
orders on a given day, never both, so the two engines need not agree on
which of the two acts first on the same bar.
"""

from quantarium.model import Decision, Order


class StopGuard:
    """Fully invested; while holding, a sell stop 3% under the latest close, renewed each day."""

    def on_bar(self, context):
        symbol = next(iter(context.bars))
        held = context.positions.get(symbol, 0)
        if not held:
            return Decision(weights=1.0, orders=())
        return Decision(weights=None, orders=(Order(symbol, "sell", "stop", held, context.bars[symbol].close * 0.97),))


class DipBuyer:
    """While flat, a buy limit 2% under the close; while holding, a sell limit 3% over the close."""

    def on_bar(self, context):
        symbol = next(iter(context.bars))
        close = context.bars[symbol].close
        held = context.positions.get(symbol, 0)
        if held:
            return Decision(weights=None, orders=(Order(symbol, "sell", "limit", held, close * 1.03),))
        price = close * 0.98
        shares = int(context.cash * 0.95 // price)
        return Decision(weights=None, orders=(Order(symbol, "buy", "limit", shares, price),) if shares else ())
