"""Starter strategies Quantarium copies into a new workspace's strategies/ folder.

Each class decides target holdings after a session closes. Edit this file or
add your own files beside it; Quantarium finds classes with an on_bar method.
"""


class BuyAndHold:
    """Put all of the cell's money in its stock and keep it there."""

    def on_bar(self, context):
        return 1.0


class TrendFollower:
    """Hold the stock while its close is above its recent average, else cash."""

    def __init__(self, window: int = 20):
        self.window = window

    def on_bar(self, context):
        history = context.history[next(iter(context.bars))]
        if len(history) < self.window:
            return 0.0
        recent = [bar.close for bar in history[-self.window:]]
        return 1.0 if recent[-1] > sum(recent) / len(recent) else 0.0


class EqualWeight:
    """Split the money evenly across every holding."""

    def on_bar(self, context):
        return {symbol: 1 / len(context.bars) for symbol in context.bars}


class RecentLeader:
    """Hold only the stock that rose most in the latest session."""

    def on_bar(self, context):
        if any(len(context.history[symbol]) < 2 for symbol in context.bars):
            return {}
        leader = max(sorted(context.bars),
                     key=lambda symbol: context.history[symbol][-1].close
                     / context.history[symbol][-2].close)
        return {leader: 1.0}
