"""Simple, disclosed controls for a Lab experiment on daily bars."""

from __future__ import annotations

from .model import Decision


class BuyAndHold:
    """Keep starting holdings, or buy equal weights once and then hold."""

    def __init__(self) -> None:
        self.started = False

    def on_bar(self, context):
        if self.started:
            return Decision()
        self.started = True
        if any(context.positions.values()):
            return Decision()
        weight = 1 / len(context.bars)
        return Decision(weights={symbol: weight for symbol in context.bars})


class EqualWeight:
    """Rebalance to equal weights after each completed daily session."""

    def on_bar(self, context):
        weight = 1 / len(context.bars)
        return {symbol: weight for symbol in context.bars}
