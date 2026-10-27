"""Small example strategies using only information available at the current close."""

from __future__ import annotations

from quantarium.model import Context


class BuyAndHold:
    def on_bar(self, context: Context) -> float:
        return 1.0


class EqualWeight:
    def on_bar(self, context: Context) -> dict[str, float]:
        weight = 1 / len(context.bars)
        return {symbol: weight for symbol in context.bars}


class RecentLeader:
    def on_bar(self, context: Context) -> dict[str, float]:
        if any(len(context.history[symbol]) < 2 for symbol in context.bars):
            return {}
        leader = max(
            sorted(context.bars),
            key=lambda symbol: context.history[symbol][-1].close / context.history[symbol][-2].close,
        )
        return {leader: 1.0}


class AlternatingDays:
    """Example of a stateful strategy that can resume from a feed checkpoint."""

    def __init__(self) -> None:
        self.count = 0

    def on_bar(self, context: Context) -> float:
        self.count += 1
        return 1.0 if self.count % 2 else 0.0

    def snapshot(self) -> int:
        return self.count

    def restore(self, state: int | None) -> None:
        self.count = state or 0



class TrendFollower:
    """Hold the stock while its close is above its recent average, else cash."""

    def __init__(self, window: int = 20) -> None:
        self.window = window

    def on_bar(self, context: Context) -> float:
        history = context.history[next(iter(context.bars))]
        if len(history) < self.window:
            return 0.0
        recent = [bar.close for bar in history[-self.window:]]
        return 1.0 if recent[-1] > sum(recent) / len(recent) else 0.0
